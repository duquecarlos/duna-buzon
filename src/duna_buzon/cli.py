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

`sincronizar` ALSO pushes, at the very END of the pass, one heartbeat per
owner sub to Cuenta Duna (`_enviar_estado_a_cuenta`, `BUZON-ESTADO-A-
CUENTA-1`): `POST /servicio/buzon/estado/{sub}` carrying the FULL current
set of this person's own sources, so Cuenta can show every mailbox that
reaches the person's books, with where it is connected. Off by default
(`Config.enviar_estado_cuenta`, `BUZON_ENVIAR_ESTADO_CUENTA`) until
Cuenta's own receiver is live; a failed or unconfigured push is, like the
pull above, never an error for the pass.

A SUCCESSFUL pull or push now also prints one count-only line of its own
(`BUZON-CUENTA-PULL-VISIBLE-1`) -- `cuenta: pull ok fuentes=<n>
inactivadas=<m>` / `cuenta: estado enviado fuentes=<n> http=<code>` --
never a URL, a sub, an email, a token or a source address, and persists the
same facts with their timestamp into `cuenta_estado.json` (directly under
`config.directorio_datos`, never per-source). `comando_estado` reads that
file back, so `duna-deploy-buzon status` (which already runs `python -m
duna_buzon estado`) can finally tell "linked and working" from "never
called", without any change to the entry script itself.

The mail-silence alert (BUZON-ALERTA-SILENCIO-1) is itself split in two
(BUZON-SILENCIO-SIN-CORREO-NUEVO-1), through `_entrada_silencio`: when
every read folder's own UIDNEXT has stayed exactly what it was at the
start of the silent window, the provider itself has nothing new, and
`estado`/`alertas` print an INFO line rather than an ALERTA -- a low-
volume mailbox going quiet for a few days is not a fault to chase. The
ALERTA stays, with its own remedy, whenever the provider DID move (or its
own folder data could not be measured at all) while Buzon forwarded
nothing: that is still exactly the "reglas excluding everything" or
"account stopped receiving mail" case this alert exists to catch.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import sys
from pathlib import Path

from duna_buzon import alerta as modulo_alerta
from duna_buzon import auth, credencial_imap, cripto
from duna_buzon import cursor as modulo_cursor
from duna_buzon import fuentes as modulo_fuentes
from duna_buzon import historial as modulo_historial
from duna_buzon import registro
from duna_buzon.cliente_imap import ClienteImap, ClienteImapError, ClienteImapErrorTransitorio
from duna_buzon.config import Config
from duna_buzon.cuenta import ESTADO_HTTP_OK, CuentaClient, CuentaPullError, FuenteEstadoWire
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.fuentes import FuenteConfig, FuenteInvalidaError
from duna_buzon.graph import GraphClient, GraphError
from duna_buzon.limites import LIMITE_ORIGEN
from duna_buzon.proveedor import construir_proveedor
from duna_buzon.reglas import ReglasInvalidasError, cargar_reglas, parsear_reglas
from duna_buzon.sincronizar import Contadores, ejecutar_pasada

NOMBRE_ARCHIVO_REGLAS = "reglas.json"
NOMBRE_ARCHIVO_ESTADO = "estado.json"

# BUZON-CUENTA-PULL-VISIBLE-1 -- one file, directly under
# `config.directorio_datos` (never per-source: the pull and the heartbeat
# are each ONE call per pass, covering every source at once), holding only
# the two keys `_actualizar_cuenta_estado` ever writes -- a timestamp and
# plain counts, never a URL, a sub, an email or a token. `duna-deploy-buzon
# status` already runs `python -m duna_buzon estado`, so `comando_estado`
# reading this file is enough to make it visible there too; no change to
# the entry script itself.
NOMBRE_ARCHIVO_CUENTA_ESTADO = "cuenta_estado.json"

# `estado`'s own default when `--pasadas` is not given -- enough to see a
# trend without flooding a normal terminal; `--pasadas N` widens this, up to
# `historial.CAPACIDAD`.
PASADAS_PREDETERMINADAS = 5

# BUZON-ORIGEN-WIRE-1 -- a manual (`agregar-fuente`) source id longer than
# this is refused at add time, well under `limites.LIMITE_ORIGEN` (64) once
# combined with its provider prefix ("imap:"), so `etiqueta_registro` never
# needs the >64 fallback below for a source this connector itself created.
LIMITE_ID_FUENTE_AGREGAR = 48

# BUZON-CURSOR-REBOBINAR-CLI-1 -- `rebobinar-cursor` refuses a date more
# than this many days before now: a named constant, never a magic number.
LIMITE_REBOBINAR_DIAS = 90


def _ahora() -> datetime.datetime:
    """BUZON-GRAPH-TIMEOUT-1 -- the one clock read this module does, so a
    test can pin it with `monkeypatch.setattr(cli, "_ahora", ...)`."""
    return datetime.datetime.now(datetime.UTC)


def _remedio_credencial(fuente: FuenteConfig) -> str:
    """How to reconnect ONE source whose credential has died
    (BUZON-ALERTA-AUTH-1) -- never a token, password or address, only the
    next command to run or where to go. `origen="cuenta"` has no `entrar`
    step here at all: Cuenta Duna owns that reconnect."""
    if fuente.origen == "cuenta":
        return "reconecte en Cuenta Duna"
    if fuente.proveedor == "microsoft":
        return f"ejecute 'entrar --fuente {fuente.id}' para firmar"
    assert fuente.proveedor == "imap"
    return f"genere una nueva contrasena de aplicacion, luego ejecute 'entrar --fuente {fuente.id}'"


def _estado_alerta(
    config: Config, fuente: FuenteConfig, ahora: datetime.datetime
) -> tuple[datetime.datetime, int] | None:
    """`(desde, horas)` when this source's own `auth_fallando_desde` has
    passed `config.alerta_auth_horas`, `None` otherwise -- no marker at
    all, or one still under the threshold. Shared by `estado`, `alertas`
    and `sincronizar`'s own journal line, so all three agree on exactly
    what counts as in alerta."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    desde = modulo_alerta.cargar_auth_fallando_desde(directorio)
    if desde is None:
        return None
    transcurrido = ahora - desde
    if transcurrido < datetime.timedelta(hours=config.alerta_auth_horas):
        return None
    horas = int(transcurrido // datetime.timedelta(hours=1))
    return desde, horas


def _estado_silencio(
    config: Config, fuente: FuenteConfig, ahora: datetime.datetime
) -> tuple[datetime.datetime, int] | None:
    """`(desde, horas)` when this source has gone quiet longer than
    `config.alerta_silencio_horas`, `None` otherwise. `desde` is this
    source's own `ultimo_visto_en` (BUZON-ALERTA-SILENCIO-1) when it has
    ever once seen mail; for a source that NEVER has, this falls back to
    `primera_pasada_en` (BUZON-CARPETAS-CONTEO-1) -- the first pass this
    connector ever attempted for it -- so a mailbox that has produced
    nothing from the very start still raises past the threshold instead of
    staying invisible forever. `None` only when this source has never even
    attempted a single pass yet, or one still under the threshold."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    desde = modulo_alerta.cargar_ultimo_visto_en(directorio)
    if desde is None:
        desde = modulo_alerta.cargar_primera_pasada_en(directorio)
    if desde is None:
        return None
    transcurrido = ahora - desde
    if transcurrido < datetime.timedelta(hours=config.alerta_silencio_horas):
        return None
    horas = int(transcurrido // datetime.timedelta(hours=1))
    return desde, horas


def _proveedor_sin_cambios(directorio_fuente: str) -> bool:
    """Whether every folder this source reads has the exact same UIDNEXT
    now as it did at the start of the current silence window (BUZON-
    SILENCIO-SIN-CORREO-NUEVO-1) -- `True` only when both maps exist and
    are equal (same folders, same UIDNEXTs: nothing new has reached the
    provider at all). `False` whenever either map is missing (no IMAP
    folder concept for this source, e.g. Microsoft/Graph, or no pass has
    recorded one yet) or the maps differ (the provider moved, a folder was
    added or removed, or anything else this comparison cannot vouch for)
    -- "could not measure" and "the provider moved" both fall back to
    today's ALERTA, never to "nothing to check"."""
    base = modulo_alerta.cargar_carpetas_base(directorio_fuente)
    actual = modulo_alerta.cargar_carpetas_actual(directorio_fuente)
    if base is None or actual is None:
        return False
    return base == actual


def _entrada_silencio(
    config: Config, fuente: FuenteConfig, ahora: datetime.datetime
) -> dict[str, object] | None:
    """The one shared silence entry `estado` and `alertas --json` both
    build from (BUZON-SILENCIO-SIN-CORREO-NUEVO-1) -- `None` when this
    source is not past its own silence threshold, otherwise a dict whose
    `tipo` carries the split:
      - `"silencio_info"`: every read folder's own UIDNEXT is unchanged
        since the window started -- nothing new ever reached the
        provider, so there is nothing in Buzon's own reach to check;
        `remedio` is `None`.
      - `"silencio"`: the provider moved (or its own folder data could not
        be measured) while Buzon forwarded nothing -- today's alert, with
        its own remedy."""
    en_silencio = _estado_silencio(config, fuente, ahora)
    if en_silencio is None:
        return None
    desde, horas = en_silencio
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    if _proveedor_sin_cambios(directorio):
        return {
            "fuente": fuente.id,
            "tipo": "silencio_info",
            "desde": registro.formato_fecha_utc(desde),
            "horas": horas,
            "remedio": None,
        }
    return {
        "fuente": fuente.id,
        "tipo": "silencio",
        "desde": registro.formato_fecha_utc(desde),
        "horas": horas,
        "remedio": _remedio_silencio(fuente),
    }


def _remedio_silencio(fuente: FuenteConfig) -> str:
    """Never a token, password or address -- only what to go check: the
    mailbox itself (still receiving mail at all?) and this source's own
    rules (not excluding everything that arrives)."""
    return (
        f"revise la fuente {fuente.id!r}: confirme que la cuenta sigue recibiendo correo "
        "y que sus reglas no estan excluyendolo todo"
    )


def _ruta_cuenta_estado(config: Config) -> Path:
    return Path(config.directorio_datos) / NOMBRE_ARCHIVO_CUENTA_ESTADO


def _leer_cuenta_estado(config: Config) -> dict[str, object]:
    """`{}` when the file does not exist yet (Cuenta never configured, or
    configured but never once pulled/pushed successfully) or cannot be
    parsed -- never raises: this is a status read, not a critical path."""
    ruta = _ruta_cuenta_estado(config)
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _actualizar_cuenta_estado(config: Config, clave: str, valor: dict[str, object]) -> None:
    """Read-modify-write of exactly one top-level key (`"ultimo_pull"` or
    `"ultimo_estado"`) -- the pull and the push happen at different points
    of the same pass (or only one of them, when the other failed or is
    unconfigured), so each must leave the other's last recorded success
    untouched."""
    datos = _leer_cuenta_estado(config)
    datos[clave] = valor
    ruta = _ruta_cuenta_estado(config)
    cripto.asegurar_directorio_datos(ruta.parent)
    cripto.escribir_archivo_sensible(ruta, json.dumps(datos).encode("utf-8"))


def _imprimir_estado_cuenta(config: Config) -> None:
    """The `estado` command's own read of `_actualizar_cuenta_estado`'s
    file -- so `duna-deploy-buzon status` (which already runs `python -m
    duna_buzon estado`) shows the last successful Cuenta pull and
    heartbeat, with their own timestamp. Prints nothing at all when the
    file does not exist (BUZON-CUENTA-PULL-VISIBLE-1: an unconfigured or
    never-yet-successful deployment shows nothing new)."""
    datos = _leer_cuenta_estado(config)
    pull = datos.get("ultimo_pull")
    if isinstance(pull, dict):
        print(
            f"cuenta: ultimo pull en={pull.get('en')} fuentes={pull.get('fuentes')} "
            f"inactivadas={pull.get('inactivadas')}"
        )
    estado = datos.get("ultimo_estado")
    if isinstance(estado, dict):
        print(
            f"cuenta: ultimo estado en={estado.get('en')} fuentes={estado.get('fuentes')} "
            f"http={estado.get('http')}"
        )


def _migrar_si_necesario(config: Config) -> None:
    modulo_fuentes.migrar_fuente_unica_si_necesario(config.directorio_datos, config.clave_cifrado)


def _tiene_credencial(directorio_fuente: str) -> bool:
    """Whether a `local`-origen source ever completed `entrar` --
    `credencial.bin` present and non-empty, the same "empty is absent"
    convention `auth.py` and `credencial_imap.py` already use for this
    same file (`BUZON-ESTADO-FUENTE-NUEVA-NUDGE-1`)."""
    ruta = Path(directorio_fuente) / modulo_fuentes.NOMBRE_ARCHIVO_CREDENCIAL
    return ruta.is_file() and ruta.stat().st_size > 0


def comando_validar_reglas() -> int:
    """Validates a `reglas.json` candidate from stdin against this
    program's own loader (`reglas.parsear_reglas`) -- never touches
    `Config` or any secret, by design: BUZON-REGLAS-ENTRADA-1's entry-script
    subcommands (`reglas`, `reglas-ver`) run this, as `duna`, to validate a
    new source's rules without the host script ever sourcing an env file.
    Prints the loader's own error and returns 1 on anything it refuses;
    prints nothing and returns 0 on a valid candidate."""
    texto = sys.stdin.read()
    try:
        parsear_reglas(texto)
    except ReglasInvalidasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


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


def _agregar_historial(
    directorio: str,
    contadores: Contadores,
    motivo: str | None,
    finalizado_en: datetime.datetime,
    *,
    omitidos_por_error_proveedor: int = 0,
    omitidos_por_fecha_proveedor: int = 0,
) -> None:
    """Appends this pass's summary to the source's bounded history
    (`historial.py`), right beside the `estado.json` write that already
    carries the same numbers -- `estado.json` itself is untouched by this
    slice, so every existing reader of it keeps working. `omitidos` merges
    every skip counter (`omitidos_por_reglas`, `omitidos_por_413_422`, and --
    for a provider that has one -- `omitidos_por_error_proveedor`,
    BUZON-IMAP-MENSAJE-AISLADO-1, and `omitidos_por_fecha_proveedor`,
    BUZON-IMAP-FECHA-DROP-VISIBLE-1): the compact table is a trend view, not
    the full breakdown `estado.json` and `registro.linea_resumen` still
    carry for the latest pass."""
    modulo_historial.agregar_pasada(
        directorio,
        modulo_historial.RegistroPasada(
            finalizado_en=registro.formato_fecha_utc(finalizado_en),
            vistos=contadores.vistos,
            reenviados=contadores.reenviados,
            duplicados=contadores.duplicados,
            omitidos=(
                contadores.omitidos_por_reglas
                + contadores.omitidos_por_413_422
                + omitidos_por_error_proveedor
                + omitidos_por_fecha_proveedor
            ),
            errors=motivo,
        ),
    )


def _ejecutar_una_fuente(
    config: Config,
    fuente: FuenteConfig,
    cliente_finanzas: FinanzasClient,
    *,
    token_cuenta: str | None = None,
) -> tuple[str, str | None, bool]:
    """Runs one source's own pass end to end and returns `(linea, motivo,
    fue_fallo_auth)` -- the summary line this source's `estado.json`
    carries, the stop reason (`None` if it finished cleanly), and whether
    THIS pass's own stop was an authentication failure (BUZON-ALERTA-
    AUTH-1; always `False` when `motivo` is `None`). Never raises for a
    failure that is this source's own (auth, rules, the provider,
    Finanzas): a source that cannot even sign in is reported exactly like
    one that signs in and then has its pass stopped, so one source's
    trouble never stops another's.

    Also persists `fuentes/<id>/alerta.json` (`alerta.py`): set on the
    FIRST auth failure of an unbroken run, kept while auth keeps failing,
    cleared the moment a pass authenticates again -- a transient error
    (`ProveedorErrorTransitorio`) touches neither.

    `token_cuenta` is the access token `_pull_fuentes_cuenta` already
    pulled for a `origen="cuenta"` source THIS run -- such a source has no
    `credencial.bin` to read, so this never calls `auth`/`credencial_imap`
    for it."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    # BUZON-CARPETAS-CONTEO-1 -- set once, on the FIRST pass this source
    # ever attempts, whatever it does next: `_estado_silencio`'s own
    # fallback needs to know when this connector started watching this
    # source even if it never once sees mail afterwards.
    modulo_alerta.marcar_primera_pasada(directorio, _ahora())

    def _detenida(motivo: str) -> tuple[str, str | None, bool]:
        # No provider exists yet at this point (auth/rules both run before
        # `construir_proveedor`), so there were never any Graph retries to
        # report -- `reintentos=0` is exact, not a placeholder.
        momento = _ahora()
        linea = registro.linea_resumen(Contadores(), motivo, finalizado_en=momento, reintentos=0)
        cripto.asegurar_directorio_datos(Path(directorio))
        cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
        _agregar_historial(directorio, Contadores(), motivo, momento)
        return linea, motivo, False

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
        modulo_alerta.marcar_auth_fallando(directorio, _ahora())
        linea, motivo, _ = _detenida(f"auth error={exc}")
        return linea, motivo, True

    ruta_reglas = Path(directorio) / NOMBRE_ARCHIVO_REGLAS
    try:
        reglas = cargar_reglas(ruta_reglas)
    except ReglasInvalidasError as exc:
        # A token was obtained this pass -- whatever stops it from here is
        # not a credential problem, so the alert clears.
        modulo_alerta.limpiar_auth_fallando(directorio)
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

    if resultado.detenida_por_autenticacion:
        modulo_alerta.marcar_auth_fallando(directorio, _ahora())
    elif not resultado.detenida_por_error_transitorio:
        # Clean, duplicated/omitted, or a permanent Finanzas rejection --
        # none of those is a credential problem, and a token was obtained
        # to even reach this point, so the alert clears. A transient
        # error touches neither (BUZON-ALERTA-AUTH-1).
        modulo_alerta.limpiar_auth_fallando(directorio)

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
    # `omitidos_por_error_proveedor` is `ClienteImap`'s own counter
    # (BUZON-IMAP-MENSAJE-AISLADO-1): one folder's own `EXAMINE`, or one
    # message's own FETCH/parse, that the provider could not make sense of
    # and skipped rather than let block every other folder or message in
    # this source's pass. Microsoft and Gmail have no such concept yet, so
    # this reports 0 for them -- never a guess.
    omitidos_proveedor = getattr(proveedor, "omitidos_por_error_proveedor", 0)
    # `omitidos_por_fecha_proveedor` is `ClienteImap`'s own counter
    # (BUZON-IMAP-FECHA-DROP-VISIBLE-1): a message its own date-bounded
    # SINCE fallback deliberately skipped. Microsoft and Gmail have no such
    # concept, so this reports 0 for them -- never a guess.
    omitidos_fecha_proveedor = getattr(proveedor, "omitidos_por_fecha_proveedor", 0)
    # `carpetas_uidnext` is `ClienteImap`'s own per-folder map (BUZON-
    # SILENCIO-SIN-CORREO-NUEVO-1), absent for every other provider (no
    # IMAP folder concept) -- this reports nothing for them rather than
    # guessing, so `_proveedor_sin_cambios` always reads their data as
    # missing and never calls them silently fine.
    carpetas_uidnext = getattr(proveedor, "carpetas_uidnext", None)
    if carpetas_uidnext:
        modulo_alerta.marcar_carpetas_actual(directorio, carpetas_uidnext)
        # The baseline restarts whenever this pass saw mail, or is set
        # once when none has ever existed -- never on every silent pass,
        # exactly like `ultimo_visto_en`/`primera_pasada_en` above.
        if resultado.contadores.vistos > 0 or modulo_alerta.cargar_carpetas_base(directorio) is None:
            modulo_alerta.marcar_carpetas_base(directorio, carpetas_uidnext)
    elif carpetas_uidnext is not None:
        # This IS an IMAP-like source (the attribute exists, unlike
        # Microsoft/Gmail), yet it came back EMPTY -- every included
        # folder's own `EXAMINE`/`SEARCH` failed this pass
        # (BUZON-IMAP-MENSAJE-AISLADO-1), or none is included at all.
        # Leaving an earlier pass's `carpetas_actual.json` in place would
        # let `_proveedor_sin_cambios` compare that STALE map against the
        # frozen baseline and read "nothing new reached the provider" --
        # a measurement THIS pass never made. Clearing it instead makes
        # the comparison read as "could not measure", which already falls
        # back to today's ALERTA, never to a false "nothing to check".
        modulo_alerta.borrar_carpetas_actual(directorio)
    momento = _ahora()
    if resultado.contadores.vistos > 0:
        # BUZON-ALERTA-SILENCIO-1 -- this source's mailbox had mail THIS
        # pass (whatever happened to it afterwards: forwarded, duplicated,
        # omitted by rules), so the silence marker resets to now. Never
        # gated on the pass finishing cleanly: a source that stopped on a
        # transient Finanzas error after seeing mail is not "silent".
        modulo_alerta.marcar_ultimo_visto(directorio, momento)
    linea = registro.linea_resumen(
        resultado.contadores,
        resultado.motivo,
        finalizado_en=momento,
        reintentos=reintentos,
        top=top_usado,
        omitidos_por_error_proveedor=omitidos_proveedor,
        omitidos_por_fecha_proveedor=omitidos_fecha_proveedor,
    )
    cripto.asegurar_directorio_datos(Path(directorio))
    cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
    _agregar_historial(
        directorio,
        resultado.contadores,
        resultado.motivo,
        momento,
        omitidos_por_error_proveedor=omitidos_proveedor,
        omitidos_por_fecha_proveedor=omitidos_fecha_proveedor,
    )
    return linea, resultado.motivo, resultado.detenida_por_autenticacion


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
                id_fuente_conectada=pullada.id,
            ),
        )

    inactivadas = 0
    for existente in modulo_fuentes.listar_fuentes(config.directorio_datos):
        if existente.origen == "cuenta" and existente.activa and existente.id not in presentes:
            modulo_fuentes.guardar_fuente(config.directorio_datos, dataclasses.replace(existente, activa=False))
            inactivadas += 1

    # BUZON-CUENTA-PULL-VISIBLE-1 -- count-only, never a URL, a sub, an
    # email or a token: the one line that tells a successful pull apart
    # from one that never happened. A failed pull already has its own
    # line above (`cuenta: {exc.motivo}`); this is the success twin.
    momento = _ahora()
    print(f"cuenta: pull ok fuentes={len(fuentes_pulladas)} inactivadas={inactivadas}")
    _actualizar_cuenta_estado(
        config,
        "ultimo_pull",
        {
            "en": registro.formato_fecha_utc(momento),
            "fuentes": len(fuentes_pulladas),
            "inactivadas": inactivadas,
        },
    )

    return tokens


def _estado_wire_para(
    config: Config, fuente: FuenteConfig, ahora: datetime.datetime
) -> FuenteEstadoWire | None:
    """This source's own heartbeat row for `enviar_estado`, or `None` when
    it has never completed a single pass yet (no `historial.jsonl` to read
    `ultimo_paso_en`/`vistos`/`reenviados` from -- there is nothing true to
    report yet, so it is simply left out of THIS heartbeat; it joins the
    next one, the moment its first pass finishes).

    `vistos`/`reenviados` come from the LATEST recorded pass -- this run's
    own fresh numbers for a source that just ran (`_agregar_historial`
    already wrote them, earlier in this same pass), or the previous run's
    for one that did not run this time (inactive, or a Cuenta pull that
    came back without it) -- never fabricated as zero, which would read as
    "checked, found nothing" rather than "did not run".

    `alerta="reingreso_requerido"` for exactly the two reconnect-needed
    cases this connector already tracks: an `origen="cuenta"` source Cuenta
    Duna no longer returns (`activa=False`), or a `local` source whose own
    auth has been failing past `BUZON_ALERTA_AUTH_HORAS` (the same
    `_estado_alerta` threshold `estado`/`alertas` already use). `None`
    otherwise -- this connector has no other failure it can attribute to a
    single source today, so `"error"` is named by the contract but never
    sent here.

    `fuente_conectada_id` (`CUENTA-BUZON-FUENTE-ID-ECO-2`) is the Cuenta
    Duna id Cuenta itself sent for this mailbox, echoed back so Cuenta can
    link this heartbeat row to the `fuente_conectada` it already knows
    about -- `None` for a local source, via `fuentes.id_fuente_conectada_
    efectiva`, which also recovers it for a `fuente.json` written before
    this slice."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    registros, _ = modulo_historial.leer_historial(directorio)
    if not registros:
        return None
    ultimo = registros[-1]

    alerta: str | None = None
    if fuente.origen == "cuenta" and not fuente.activa:
        alerta = "reingreso_requerido"
    elif _estado_alerta(config, fuente, ahora) is not None:
        alerta = "reingreso_requerido"

    return FuenteEstadoWire(
        fuente_id=fuente.id,
        proveedor=fuente.proveedor,
        direccion=fuente.direccion,
        etiqueta=fuente.etiqueta or None,
        origen="cuenta" if fuente.origen == "cuenta" else "buzon",
        ultimo_paso_en=ultimo.finalizado_en,
        vistos=ultimo.vistos,
        reenviados=ultimo.reenviados,
        alerta=alerta,
        # CUENTA-BUZON-FUENTE-ID-ECO-2 -- `id_fuente_conectada_efectiva`
        # already returns `None` for a local source, so this is correct
        # unconditionally, not just "only for origen == cuenta".
        fuente_conectada_id=modulo_fuentes.id_fuente_conectada_efectiva(fuente),
    )


def _enviar_estado_a_cuenta(config: Config) -> None:
    """`BUZON-ESTADO-A-CUENTA-1` -- the push side of
    `../dev-protocol/contracts/buzon-estado-a-cuenta-v1.md`. A no-op unless
    BOTH `config.enviar_estado_cuenta` (the off-by-default switch) and
    `config.cuenta_configurada` (there is a `sub` and a credential to push
    with) hold -- today's connector serves exactly one person, so that one
    configured `sub` is the "owner sub" for every source, local or
    Cuenta-sourced alike; a deployment with no Cuenta Duna configured at
    all has no sub to push under, same as the contract's own "a fuente
    with no sub is not sent" read at the whole-deployment level.

    Never fails the pass: a `CuentaPullError` (bad credential, Cuenta Duna
    down, an unknown `sub`) is caught here and logged as one count-only
    line, exactly like `_pull_fuentes_cuenta`'s own failure path."""
    if not config.enviar_estado_cuenta or not config.cuenta_configurada:
        return

    assert config.cuenta_url is not None
    assert config.cuenta_client_id is not None
    assert config.cuenta_client_secret is not None
    assert config.cuenta_sub is not None

    ahora = _ahora()
    fuentes_wire = [
        wire
        for fuente in modulo_fuentes.listar_fuentes(config.directorio_datos)
        if (wire := _estado_wire_para(config, fuente, ahora)) is not None
    ]

    cliente = CuentaClient(
        url_base=config.cuenta_url,
        client_id=config.cuenta_client_id,
        client_secret=config.cuenta_client_secret,
        sub=config.cuenta_sub,
    )
    try:
        cliente.enviar_estado(fuentes_wire, enviado_en=ahora)
    except CuentaPullError as exc:
        print(f"cuenta: {exc.motivo}", file=sys.stderr)
    else:
        # BUZON-CUENTA-PULL-VISIBLE-1 -- count-only, never a URL, a sub, an
        # email or a token: the success twin of the failure line above.
        # `enviar_estado` only ever returns (never raises) on
        # `cuenta.ESTADO_HTTP_OK`, so that is the one code a success line
        # can ever report.
        print(f"cuenta: estado enviado fuentes={len(fuentes_wire)} http={ESTADO_HTTP_OK}")
        _actualizar_cuenta_estado(
            config,
            "ultimo_estado",
            {
                "en": registro.formato_fecha_utc(ahora),
                "fuentes": len(fuentes_wire),
                "http": ESTADO_HTTP_OK,
            },
        )
    finally:
        cliente.cerrar()


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
                linea, motivo, fue_fallo_auth = _ejecutar_una_fuente(
                    config, fuente, cliente_finanzas, token_cuenta=token_cuenta
                )
            else:
                linea, motivo, fue_fallo_auth = _ejecutar_una_fuente(config, fuente, cliente_finanzas)
            print(f"fuente={fuente.id} {linea}")
            if motivo is not None:
                parada = f"fuente={fuente.id} parada: {motivo}"
                if fue_fallo_auth and _estado_alerta(config, fuente, _ahora()) is not None:
                    parada += " alerta=si"
                print(parada, file=sys.stderr)
                algun_fallo = True
    finally:
        cliente_finanzas.cerrar()

    _enviar_estado_a_cuenta(config)
    return 1 if algun_fallo else 0


def _imprimir_historial(id_fuente: str, directorio: str, limite: int) -> None:
    """A compact table of PREVIOUS passes -- the line just printed above
    this (from `estado.json`) already shows the latest one, so this drops
    that same entry (`historial`'s own last line) and shows up to `limite`
    of the ones before it, most recent first. A corrupt line is skipped and
    noted once, by count -- never raised, and never shown as a row."""
    registros, descartadas = modulo_historial.leer_historial(directorio)
    if descartadas:
        print(
            f"fuente={id_fuente} historial: {descartadas} linea(s) descartada(s) (formato invalido)",
            file=sys.stderr,
        )
    previas = registros[:-1] if registros else []
    if not previas or limite <= 0:
        return
    for pasada in reversed(previas[-limite:]):
        errors_txt = pasada.errors if pasada.errors is not None else "-"
        print(
            f"fuente={id_fuente} historial finalizado_en={pasada.finalizado_en} "
            f"vistos={pasada.vistos} reenviados={pasada.reenviados} duplicados={pasada.duplicados} "
            f"omitidos={pasada.omitidos} errors={errors_txt}"
        )


def comando_estado(config: Config, *, pasadas: int | None = None) -> int:
    _migrar_si_necesario(config)
    _imprimir_estado_cuenta(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    if not todas:
        print("cursor=none")
        return 0

    limite_pasadas = (
        min(pasadas, modulo_historial.CAPACIDAD) if pasadas is not None else PASADAS_PREDETERMINADAS
    )

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
            # BUZON-ESTADO-FUENTE-NUEVA-NUDGE-1 -- no cursor yet means
            # either "never completed entrar" or "signed in but never
            # synced"; `origen="cuenta"` has no entrar step at all, so it
            # is always the second. Each gets its own next command.
            if fuente.origen == "local" and not _tiene_credencial(directorio):
                print(f"fuente={fuente.id} siguiente: ejecute 'entrar --fuente {fuente.id}' para firmar")
            else:
                print(f"fuente={fuente.id} siguiente: ejecute 'sincronizar' para la primera pasada")

        ruta_estado = Path(directorio) / NOMBRE_ARCHIVO_ESTADO
        if ruta_estado.is_file():
            print(f"fuente={fuente.id} {ruta_estado.read_text(encoding='utf-8').strip()}")

        en_alerta = _estado_alerta(config, fuente, _ahora())
        if en_alerta is not None:
            desde, horas = en_alerta
            print(
                f"fuente={fuente.id} ALERTA credencial caida desde "
                f"{registro.formato_fecha_utc(desde)} ({horas} h) -- "
                f"reconectar: {_remedio_credencial(fuente)}"
            )
        else:
            # BUZON-ESTADO-ALERTA-SILENCIO-1 -- `comando_alertas` has shown
            # a silence alert since BUZON-IMAP-FECHA-DROP-Y-SILENCIO-1, but
            # `estado` (what `duna-deploy-buzon status` actually runs) never
            # printed its twin: a source gone quiet read exactly like one
            # that is fine, right here, where Carlos and Main actually look.
            # Checked only when there is no auth alert already, same
            # priority `comando_alertas` already gives the two (an auth
            # failure already explains the silence).
            # BUZON-SILENCIO-SIN-CORREO-NUEVO-1 -- split in two: the
            # provider itself having nothing new is not something to
            # revise, so it never prints as "ALERTA".
            entrada_silencio = _entrada_silencio(config, fuente, _ahora())
            if entrada_silencio is not None:
                if entrada_silencio["tipo"] == "silencio_info":
                    print(
                        f"fuente={fuente.id} sin correo nuevo en el proveedor desde "
                        f"{entrada_silencio['desde']} ({entrada_silencio['horas']} h): "
                        "nada que revisar en Buzon"
                    )
                else:
                    print(
                        f"fuente={fuente.id} ALERTA silencio desde "
                        f"{entrada_silencio['desde']} ({entrada_silencio['horas']} h) -- "
                        f"{entrada_silencio['remedio']}"
                    )

        _imprimir_historial(fuente.id, directorio, limite_pasadas)

    return 1 if algun_fallo else 0


def comando_alertas(config: Config) -> int:
    """Machine-readable: every source currently past its own auth-failure
    threshold (BUZON-ALERTA-AUTH-1), OR -- when it is not already an auth
    alert, the auth failure already explains the silence -- past its own
    mail-silence threshold (BUZON-ALERTA-SILENCIO-1), as a JSON list --
    `[]` when none. A silence entry's own `tipo` (BUZON-SILENCIO-SIN-
    CORREO-NUEVO-1) is `"silencio_info"` when the provider itself has
    nothing new (no action needed) or `"silencio"` when it moved while
    Buzon forwarded nothing -- `_entrada_silencio` is the one place that
    decides, shared with `estado`'s own printed line, so both always agree.
    Read-only, exits 0 either way: Main's own session-start hub check reads
    this through `duna-deploy-buzon alertas` to relay a dead credential or
    a genuinely gone-quiet source to the desk, never a token, password or
    address beyond the source's own id."""
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    ahora = _ahora()
    alertas: list[dict[str, object]] = []
    for fuente in todas:
        en_alerta = _estado_alerta(config, fuente, ahora)
        if en_alerta is not None:
            desde, horas = en_alerta
            alertas.append(
                {
                    "fuente": fuente.id,
                    "tipo": "auth",
                    "desde": registro.formato_fecha_utc(desde),
                    "horas": horas,
                    "remedio": _remedio_credencial(fuente),
                }
            )
            continue
        entrada_silencio = _entrada_silencio(config, fuente, ahora)
        if entrada_silencio is None:
            continue
        alertas.append(entrada_silencio)
    print(json.dumps(alertas))
    return 0


def comando_fuentes(config: Config) -> int:
    _migrar_si_necesario(config)
    todas, errores = modulo_fuentes.listar_fuentes_con_errores(config.directorio_datos)
    if not todas and not errores:
        print("no sources configured")
        return 0

    for fuente in todas:
        estado_txt = "activa" if fuente.activa else "inactiva"
        print(
            f"{fuente.id} proveedor={fuente.proveedor} direccion={fuente.direccion} "
            f"etiqueta={fuente.etiqueta} estado={estado_txt}"
        )
    for error in errores:
        print(f"fuente={error.id} error: {error.motivo}", file=sys.stderr)
    return 1 if errores else 0


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
    content, and never the credential (`BUZON-IMAP-CARPETAS-INCLUIDAS-1`).

    For every folder THIS source would read, also prints its own counts
    (`BUZON-CARPETAS-CONTEO-1`): MESSAGES, UIDNEXT, UIDVALIDITY (from the
    same EXAMINE `cliente_imap.diagnosticar_carpeta` issues), this source's
    own stored UID watermark for that folder (`marca`, "sin marca" when
    none is stored yet), and how many messages carry a UID past it
    (`nuevos`) -- the line that tells apart "no mail arrived" (`nuevos=0`)
    from "mail is there and a pass is not seeing it" (`nuevos` > 0 while
    `sincronizar` keeps reporting `vistos=0`). One folder's own broken
    EXAMINE or SEARCH is reported on its own line and never stops the rest
    of this report (same isolation `listar_mensajes` already gives a live
    pass, BUZON-IMAP-MENSAJE-AISLADO-1) -- only a connection-level failure
    still stops the whole command."""
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
    cursor_proveedor_inicial = modulo_cursor.cargar_cursor_proveedor(directorio)
    cliente = ClienteImap(
        host=fuente.host,
        puerto=fuente.port,
        usuario=fuente.direccion,
        password=password,
        carpetas=fuente.carpetas,
        cursor_proveedor_inicial=cursor_proveedor_inicial,
    )
    try:
        try:
            listado = cliente.listar_carpetas_con_estado()
        except ClienteImapError as exc:
            print(f"error: imap {exc}", file=sys.stderr)
            return 1

        for nombre, banderas, incluida in listado:
            banderas_txt = ",".join(banderas) if banderas else "-"
            linea = f"carpeta={nombre!r} banderas={banderas_txt} leida={'si' if incluida else 'no'}"
            if incluida:
                try:
                    diagnostico = cliente.diagnosticar_carpeta(nombre)
                except ClienteImapErrorTransitorio as exc:
                    linea += f" error=imap {exc}"
                else:
                    marca_txt = (
                        f"{diagnostico.marca[0]}:{diagnostico.marca[1]}"
                        if diagnostico.marca is not None
                        else "sin marca"
                    )
                    nuevos_txt = str(diagnostico.nuevos) if diagnostico.nuevos is not None else "sin marca"
                    linea += (
                        f" mensajes={diagnostico.mensajes} uidnext={diagnostico.uidnext} "
                        f"uidvalidity={diagnostico.uidvalidity} marca={marca_txt} nuevos={nuevos_txt}"
                    )
            print(linea)
    finally:
        cliente.cerrar()
    return 0


def comando_carpetas_graph(config: Config, id_fuente: str) -> int:
    """Read-only: the microsoft-source twin of `comando_carpetas` above
    (`BUZON-CARPETAS-MICROSOFT-CLI-1`) -- signs in with the already-stored
    token and prints every top-level mail folder Graph's own
    `/me/mailFolders` returns: display name, id, and well-known name
    where one applies, so the operator can choose a `carpetas` entry for
    `fijar-carpetas` without guessing. Never prints message content."""
    _migrar_si_necesario(config)
    todas = modulo_fuentes.listar_fuentes(config.directorio_datos)
    try:
        fuente = next(f for f in todas if f.id == id_fuente)
    except StopIteration:
        print(f"error: no such source: {id_fuente}", file=sys.stderr)
        return 1

    if fuente.proveedor != "microsoft":
        print(f"error: {id_fuente!r} is not a microsoft source", file=sys.stderr)
        return 1

    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    try:
        token = auth.obtener_token(config, directorio)
    except auth.SinCuentaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    cliente = GraphClient(token)
    try:
        listado = cliente.listar_carpetas()
    except GraphError as exc:
        print(f"error: {exc.motivo}", file=sys.stderr)
        return 1
    finally:
        cliente.cerrar()

    for carpeta in listado:
        bien_conocida = carpeta.bien_conocida or "-"
        print(f"carpeta={carpeta.nombre!r} bien_conocida={bien_conocida} id={carpeta.id}")
    return 0


def comando_fijar_carpetas(
    config: Config, id_fuente: str, *, carpetas: list[str] | None, todas: bool
) -> int:
    """Sets (`carpetas` given) or, with `todas`, removes `carpetas` on an
    existing imap or microsoft source's `fuente.json` -- the operator
    tool `deploy/host/README.md` used to say did not exist for a
    microsoft source (BUZON-CARPETAS-MICROSOFT-CLI-1; `agregar-fuente
    imap`'s own `--carpeta` already covers it at CREATE time, but not for
    an EXISTING source of either provider). Writes atomically
    (`fuentes.guardar_fuente_atomico`), preserving every other field, and
    never touches `credencial.bin` or the cursor."""
    _migrar_si_necesario(config)
    try:
        fuente = modulo_fuentes.cargar_fuente(config.directorio_datos, id_fuente)
    except FuenteInvalidaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if fuente.proveedor not in ("imap", "microsoft"):
        print(
            f"error: {id_fuente!r} is a {fuente.proveedor!r} source; fijar-carpetas only "
            "supports imap and microsoft sources",
            file=sys.stderr,
        )
        return 1

    antes = list(fuente.carpetas) if fuente.carpetas else []
    nuevas: tuple[str, ...] | None = None if todas else tuple(carpetas or [])
    modulo_fuentes.guardar_fuente_atomico(
        config.directorio_datos, dataclasses.replace(fuente, carpetas=nuevas or None)
    )
    despues = list(nuevas) if nuevas else []
    print(f"fuente={id_fuente} carpetas antes={antes}")
    print(f"fuente={id_fuente} carpetas despues={despues}")
    return 0


def comando_rebobinar_cursor(
    config: Config, id_fuente: str, fecha: str, *, aplicar: bool
) -> int:
    """Rewinds one source's own date cursor to an earlier ISO date, so its
    next `sincronizar` pass re-reads everything the provider still has
    from that date on -- the only way to re-read a source after a bad
    pass (BUZON-CURSOR-REBOBINAR-CLI-1); today that otherwise means
    hand-editing `cursor.json` on the server. DRY-RUN by default: prints
    the current cursor and the computed new one, writes nothing. Only
    `--aplicar` writes, atomically (`cursor.guardar_cursor_atomico`, the
    same temp-file-then-`os.replace` technique `fijar-carpetas` already
    uses for `fuente.json`), and also clears `cursor_proveedor.json` --
    required for an IMAP source, whose own per-folder UID watermark would
    otherwise override the rewound date on the very next pass (see
    `cursor.borrar_cursor_proveedor`'s own docstring); a safe no-op for
    Microsoft and Gmail, which never read that file.

    Refuses: an unknown source (`FuenteInvalidaError`, same message
    `fijar-carpetas` already uses), a date that is not valid ISO 8601, a
    date in the future, and a date more than `LIMITE_REBOBINAR_DIAS` days
    before now.

    Re-forwarding mail already sent is NOT this command's own job to
    prevent: the next pass's own duplicate guard
    (`sincronizar.ejecutar_pasada`, via Finanzas' 200-vs-201 response on
    `id_externo` -- `finanzas.FinanzasClient.enviar_entrada`) answers
    every re-seen message `"duplicated"`, counted in
    `Contadores.duplicados`, never `reenviados` -- so a rewind can only
    ever re-count or newly forward mail, never re-forward what Finanzas
    already holds."""
    _migrar_si_necesario(config)
    try:
        fuente = modulo_fuentes.cargar_fuente(config.directorio_datos, id_fuente)
    except FuenteInvalidaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        nueva = datetime.datetime.fromisoformat(fecha)
    except ValueError:
        print(f"error: {fecha!r} is not a valid ISO 8601 date", file=sys.stderr)
        return 1
    if nueva.tzinfo is None:
        nueva = nueva.replace(tzinfo=datetime.UTC)

    ahora = _ahora()
    if nueva > ahora:
        print(f"error: {fecha!r} is in the future", file=sys.stderr)
        return 1
    if nueva < ahora - datetime.timedelta(days=LIMITE_REBOBINAR_DIAS):
        print(
            f"error: {fecha!r} is more than {LIMITE_REBOBINAR_DIAS} days back "
            f"(the {LIMITE_REBOBINAR_DIAS}-day limit)",
            file=sys.stderr,
        )
        return 1

    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))
    ruta_cursor = Path(directorio) / modulo_cursor.NOMBRE_ARCHIVO
    actual = (
        modulo_cursor.cargar_cursor(directorio, valor_inicial=nueva) if ruta_cursor.is_file() else None
    )
    antes_txt = actual.isoformat() if actual is not None else "none"
    tenia_cursor_proveedor = (Path(directorio) / modulo_cursor.NOMBRE_ARCHIVO_PROVEEDOR).is_file()

    print(f"fuente={fuente.id} cursor antes={antes_txt}")
    print(f"fuente={fuente.id} cursor despues={nueva.isoformat()}")
    if tenia_cursor_proveedor:
        print(f"fuente={fuente.id} cursor_proveedor antes=presente despues=none")

    if not aplicar:
        print(f"fuente={fuente.id} dry-run: nothing written (pass --aplicar to write)")
        return 0

    modulo_cursor.guardar_cursor_atomico(directorio, nueva)
    modulo_cursor.borrar_cursor_proveedor(directorio)
    print(f"fuente={fuente.id} cursor rebobinado")
    return 0
