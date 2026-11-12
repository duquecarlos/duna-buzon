"""The four commands -- `entrar`, `sincronizar`, `estado`, `fuentes` -- wired
to one `Config` and iterating `fuentes.py`'s own per-source layout.
`__main__.py` is the thin argument-parsing shell around this module.

`sincronizar` ALSO pulls every source Carlos connected through Cuenta Duna
(`_pull_fuentes_cuenta`, `BUZON-FUENTES-PULL-1`) before running the pass:
one `GET /servicio/fuentes/{sub}` against `cuenta.py`'s `CuentaClient`,
refreshing each returned source's `fuente.json` (never its credential --
Cuenta sources have none on disk) and marking any Cuenta-origin source no
longer returned as inactive. A failed or unconfigured pull is not an
error: it is logged in one count-only line and every LOCAL source still
runs exactly as before this slice.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import sys
from pathlib import Path

from duna_buzon import auth, credencial_imap, cripto
from duna_buzon import cursor as modulo_cursor
from duna_buzon import fuentes as modulo_fuentes
from duna_buzon import registro
from duna_buzon.cliente_imap import ClienteImap, ClienteImapError
from duna_buzon.config import Config
from duna_buzon.cuenta import CuentaClient, CuentaPullError
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.fuentes import FuenteConfig
from duna_buzon.limites import LIMITE_ORIGEN
from duna_buzon.proveedor import construir_proveedor
from duna_buzon.reglas import ReglasInvalidasError, cargar_reglas
from duna_buzon.sincronizar import Contadores, ejecutar_pasada

NOMBRE_ARCHIVO_REGLAS = "reglas.json"
NOMBRE_ARCHIVO_ESTADO = "estado.json"

# BUZON-ORIGEN-WIRE-1 -- a manual (`agregar-fuente`) source id longer than
# this is refused at add time, well under `limites.LIMITE_ORIGEN` (64) once
# combined with its provider prefix ("imap:"), so `etiqueta_registro` never
# needs the >64 fallback below for a source this connector itself created.
LIMITE_ID_FUENTE_AGREGAR = 48


def _ahora() -> datetime.datetime:
    """BUZON-GRAPH-TIMEOUT-1 -- the one clock read this module does, so a
    test can pin it with `monkeypatch.setattr(cli, "_ahora", ...)`."""
    return datetime.datetime.now(datetime.UTC)


def _migrar_si_necesario(config: Config) -> None:
    modulo_fuentes.migrar_fuente_unica_si_necesario(config.directorio_datos, config.clave_cifrado)


def comando_entrar(config: Config, id_fuente: str | None) -> int:
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)

    if id_fuente is None:
        if len(todas) != 1:
            print(
                "error: --fuente is required (more than one source configured, or none yet)",
                file=sys.stderr,
            )
            return 1
        fuente = todas[0]
    else:
        try:
            fuente = next(f for f in todas if f.id == id_fuente)
        except StopIteration:
            print(f"error: no such source: {id_fuente}", file=sys.stderr)
            return 1

    if fuente.origen == "cuenta":
        print(
            f"error: {fuente.id!r} is managed by Cuenta Duna (connect it there, not with entrar)",
            file=sys.stderr,
        )
        return 1

    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))

    if fuente.proveedor == "microsoft":
        auth.entrar(config, directorio)
        return 0

    assert fuente.proveedor == "imap"
    assert fuente.host is not None and fuente.port is not None
    try:
        credencial_imap.entrar(
            config, directorio, host=fuente.host, puerto=fuente.port, usuario=fuente.direccion
        )
    except credencial_imap.VerificacionLoginError as exc:
        print(f"error: imap login failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _ejecutar_una_fuente(
    config: Config,
    fuente: FuenteConfig,
    cliente_finanzas: FinanzasClient,
    *,
    token_cuenta: str | None = None,
) -> tuple[str, str | None]:
    """Runs one source's own pass end to end and returns `(linea, motivo)`
    -- the summary line this source's `estado.json` carries, and the stop
    reason (`None` if it finished cleanly). Never raises for a failure that
    is this source's own (auth, rules, the provider, Finanzas): a source
    that cannot even sign in is reported exactly like one that signs in and
    then has its pass stopped, so one source's trouble never stops
    another's.

    `token_cuenta` is the access token `_pull_fuentes_cuenta` already
    pulled for a `origen="cuenta"` source THIS run -- such a source has no
    `credencial.bin` to read, so this never calls `auth`/`credencial_imap`
    for it."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))

    def _detenida(motivo: str) -> tuple[str, str | None]:
        # No provider exists yet at this point (auth/rules both run before
        # `construir_proveedor`), so there were never any Graph retries to
        # report -- `reintentos=0` is exact, not a placeholder.
        linea = registro.linea_resumen(Contadores(), motivo, finalizado_en=_ahora(), reintentos=0)
        cripto.asegurar_directorio_datos(Path(directorio))
        cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
        return linea, motivo

    try:
        if fuente.origen == "cuenta":
            assert token_cuenta is not None
            token = token_cuenta
        elif fuente.proveedor == "microsoft":
            token = auth.obtener_token(config, directorio)
        else:
            assert fuente.proveedor == "imap"
            token = credencial_imap.obtener_credencial(config, directorio)
    except (auth.SinCuentaError, credencial_imap.SinCredencialError, cripto.ClaveInvalidaError) as exc:
        return _detenida(f"auth error={exc}")

    ruta_reglas = Path(directorio) / NOMBRE_ARCHIVO_REGLAS
    try:
        reglas = cargar_reglas(ruta_reglas)
    except ReglasInvalidasError as exc:
        return _detenida(f"reglas error={exc}")

    cursor_inicial = modulo_cursor.cargar_cursor(directorio, valor_inicial=reglas.desde)
    cursor_proveedor_inicial = modulo_cursor.cargar_cursor_proveedor(directorio)

    etiqueta_origen: str | None = None
    if config.enviar_origen:
        etiqueta = fuente.etiqueta_registro
        if len(etiqueta) <= LIMITE_ORIGEN:
            etiqueta_origen = etiqueta
        else:
            print(
                f"fuente={fuente.id} warning: origen label exceeds {LIMITE_ORIGEN} "
                "characters, sending without origen",
                file=sys.stderr,
            )

    proveedor = construir_proveedor(fuente, token, cursor_proveedor_inicial=cursor_proveedor_inicial)
    try:
        resultado = ejecutar_pasada(
            proveedor=proveedor,
            cliente_finanzas=cliente_finanzas,
            reglas=reglas,
            cursor_inicial=cursor_inicial,
            cursor_proveedor_inicial=cursor_proveedor_inicial,
            etiqueta_origen=etiqueta_origen,
        )
    finally:
        proveedor.cerrar()

    if resultado.debe_persistir_cursor and resultado.cursor_final is not None:
        modulo_cursor.guardar_cursor(directorio, resultado.cursor_final)
    if resultado.debe_persistir_cursor and resultado.cursor_proveedor_final is not None:
        modulo_cursor.guardar_cursor_proveedor(directorio, resultado.cursor_proveedor_final)

    # `reintentos_usados` is `GraphClient`'s own counter (BUZON-GRAPH-
    # TIMEOUT-1); every other provider (IMAP, Gmail) and every test fake
    # simply lacks it, so this reports 0 for them -- never a guess.
    reintentos = getattr(proveedor, "reintentos_usados", 0)
    # `top_lista_usado` is `GraphClient`'s own (BUZON-GRAPH-502-1); every
    # other provider has no such concept, so this omits it for them rather
    # than guessing a number that means nothing there.
    top_usado = getattr(proveedor, "top_lista_usado", None)
    linea = registro.linea_resumen(
        resultado.contadores,
        resultado.motivo,
        finalizado_en=_ahora(),
        reintentos=reintentos,
        top=top_usado,
    )
    cripto.asegurar_directorio_datos(Path(directorio))
    cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
    return linea, resultado.motivo


def _pull_fuentes_cuenta(config: Config) -> dict[str, str] | None:
    """One pull of every source Carlos connected through Cuenta Duna
    (`BUZON-FUENTES-PULL-1`). Returns `{id_fuente: access_token}` for every
    source ACTIVE this run, or `None` the moment Cuenta Duna is not
    configured at all or the pull itself failed -- in neither case is this
    an error for the pass as a whole: the caller (`comando_sincronizar`)
    just runs every LOCAL source exactly as if this function did not
    exist.

    On success (even an empty list), this also:
    - writes/refreshes a `fuente.json` for every returned source, keyed by
      `fuentes.id_fuente_cuenta` -- never a `credencial.bin`: the token
      lives only in the dict this function returns, for this one run.
    - marks any PREVIOUSLY-active `origen="cuenta"` source that Cuenta
      Duna no longer returns as inactive, without touching its cursor or
      its rules -- "a revoked source simply stops appearing", per this
      slice's own build instructions, and a later reconnect resumes them
      unchanged.
    """
    if not config.cuenta_configurada:
        return None

    assert config.cuenta_url is not None
    assert config.cuenta_client_id is not None
    assert config.cuenta_client_secret is not None
    assert config.cuenta_sub is not None
    cliente = CuentaClient(
        url_base=config.cuenta_url,
        client_id=config.cuenta_client_id,
        client_secret=config.cuenta_client_secret,
        sub=config.cuenta_sub,
    )
    try:
        fuentes_pulladas = cliente.listar_fuentes()
    except CuentaPullError as exc:
        print(f"cuenta: {exc.motivo}", file=sys.stderr)
        return None
    finally:
        cliente.cerrar()

    tokens: dict[str, str] = {}
    presentes: set[str] = set()
    for pullada in fuentes_pulladas:
        id_fuente = modulo_fuentes.id_fuente_cuenta(pullada.id)
        presentes.add(id_fuente)
        tokens[id_fuente] = pullada.access_token
        modulo_fuentes.guardar_fuente(
            config.directorio_datos,
            FuenteConfig(
                id=id_fuente,
                proveedor=pullada.proveedor,
                direccion=pullada.direccion,
                etiqueta=pullada.direccion,
                token_finanzas_ref=config.cuenta_sub,
                activa=True,
                origen="cuenta",
            ),
        )

    for existente in modulo_fuentes.listar_fuentes(config.directorio_datos):
        if existente.origen == "cuenta" and existente.activa and existente.id not in presentes:
            modulo_fuentes.guardar_fuente(config.directorio_datos, dataclasses.replace(existente, activa=False))

    return tokens


def comando_sincronizar(config: Config) -> int:
    _migrar_si_necesario(config)
    tokens_cuenta = _pull_fuentes_cuenta(config)

    activas = modulo_fuentes.fuentes_activas(config.directorio_datos)
    if not activas:
        print("error: no active sources configured", file=sys.stderr)
        return 1

    cliente_finanzas = FinanzasClient(config)
    algun_fallo = False
    try:
        for fuente in activas:
            if fuente.origen == "cuenta":
                token_cuenta = (tokens_cuenta or {}).get(fuente.id)
                if token_cuenta is None:
                    # The pull failed, was not configured, or this source
                    # simply was not in THIS pull's result -- it does not
                    # run this pass, and that is not a failure of the pass:
                    # its cursor is untouched, ready for the next pull.
                    continue
                linea, motivo = _ejecutar_una_fuente(
                    config, fuente, cliente_finanzas, token_cuenta=token_cuenta
                )
            else:
                linea, motivo = _ejecutar_una_fuente(config, fuente, cliente_finanzas)
            print(f"fuente={fuente.id} {linea}")
            if motivo is not None:
                print(f"fuente={fuente.id} parada: {motivo}", file=sys.stderr)
                algun_fallo = True
    finally:
        cliente_finanzas.cerrar()

    return 1 if algun_fallo else 0


def comando_estado(config: Config) -> int:
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    if not todas:
        print("cursor=none")
        return 0

    algun_fallo = False
    for fuente in todas:
        directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))

        if not auth.cache_cargable(config, directorio):
            print(
                f"fuente={fuente.id} error: the credential could not be decrypted "
                "under the configured key",
                file=sys.stderr,
            )
            algun_fallo = True
            continue

        ruta_cursor = Path(directorio) / modulo_cursor.NOMBRE_ARCHIVO
        if ruta_cursor.is_file():
            datos = json.loads(ruta_cursor.read_text(encoding="utf-8"))
            print(f"fuente={fuente.id} cursor={datos['cursor']}")
        else:
            print(f"fuente={fuente.id} cursor=none")

        ruta_estado = Path(directorio) / NOMBRE_ARCHIVO_ESTADO
        if ruta_estado.is_file():
            print(f"fuente={fuente.id} {ruta_estado.read_text(encoding='utf-8').strip()}")

    return 1 if algun_fallo else 0


def comando_fuentes(config: Config) -> int:
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    if not todas:
        print("no sources configured")
        return 0

    for fuente in todas:
        estado_txt = "activa" if fuente.activa else "inactiva"
        print(
            f"{fuente.id} proveedor={fuente.proveedor} direccion={fuente.direccion} "
            f"etiqueta={fuente.etiqueta} estado={estado_txt}"
        )
    return 0


def comando_agregar_fuente(
    config: Config,
    *,
    proveedor: str,
    id_fuente: str,
    direccion: str,
    host: str,
    puerto: int,
    token_finanzas: str,
    etiqueta: str | None,
    carpetas: list[str] | None = None,
) -> int:
    """Writes a new `fuente.json` -- never signs in. `entrar --fuente
    <id>` is the next, separate step (build instructions for
    `BUZON-FUENTE-IMAP-1`)."""
    if proveedor != "imap":
        print(f"error: agregar-fuente does not support provider {proveedor!r} yet", file=sys.stderr)
        return 1

    if len(id_fuente) > LIMITE_ID_FUENTE_AGREGAR:
        print(
            f"error: source id {id_fuente!r} is {len(id_fuente)} characters, "
            f"over the {LIMITE_ID_FUENTE_AGREGAR}-character limit",
            file=sys.stderr,
        )
        return 1

    _migrar_si_necesario(config)
    existentes = modulo_fuentes.listar_fuentes(config.directorio_datos)
    if any(f.id == id_fuente for f in existentes):
        print(f"error: a source named {id_fuente!r} already exists", file=sys.stderr)
        return 1

    modulo_fuentes.guardar_fuente(
        config.directorio_datos,
        FuenteConfig(
            id=id_fuente,
            proveedor="imap",
            direccion=direccion,
            etiqueta=etiqueta or direccion,
            token_finanzas_ref=token_finanzas,
            activa=True,
            host=host,
            port=puerto,
            carpetas=tuple(carpetas) if carpetas else None,
        ),
    )
    print(f"fuente {id_fuente!r} agregada: ejecute 'entrar --fuente {id_fuente}' para firmar.")
    return 0


def comando_carpetas(config: Config, id_fuente: str) -> int:
    """Read-only: logs in to ONE imap source with its already-stored app
    password and prints every folder the server's own `LIST` returns --
    name, flags, and whether this source would read it today -- so the
    operator can see, before the first sync, whether a shared mailbox like
    Zoho's is exposed at all, and under what name. Never prints message
    content, and never the credential (`BUZON-IMAP-CARPETAS-INCLUIDAS-1`)."""
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    try:
        fuente = next(f for f in todas if f.id == id_fuente)
    except StopIteration:
        print(f"error: no such source: {id_fuente}", file=sys.stderr)
        return 1

    if fuente.proveedor != "imap":
        print(f"error: {id_fuente!r} is not an imap source", file=sys.stderr)
        return 1

    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    try:
        password = credencial_imap.obtener_credencial(config, directorio)
    except (credencial_imap.SinCredencialError, cripto.ClaveInvalidaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    assert fuente.host is not None and fuente.port is not None
    cliente = ClienteImap(
        host=fuente.host,
        puerto=fuente.port,
        usuario=fuente.direccion,
        password=password,
        carpetas=fuente.carpetas,
    )
    try:
        listado = cliente.listar_carpetas_con_estado()
    except ClienteImapError as exc:
        print(f"error: imap {exc}", file=sys.stderr)
        return 1
    finally:
        cliente.cerrar()

    for nombre, banderas, incluida in listado:
        banderas_txt = ",".join(banderas) if banderas else "-"
        print(f"carpeta={nombre!r} banderas={banderas_txt} leida={'si' if incluida else 'no'}")
    return 0
