"""The four commands -- `entrar`, `sincronizar`, `estado`, `fuentes` -- wired
to one `Config` and iterating `fuentes.py`'s own per-source layout.
`__main__.py` is the thin argument-parsing shell around this module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from duna_buzon import auth, credencial_imap, cripto
from duna_buzon import cursor as modulo_cursor
from duna_buzon import fuentes as modulo_fuentes
from duna_buzon import registro
from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.fuentes import FuenteConfig
from duna_buzon.proveedor import construir_proveedor
from duna_buzon.reglas import ReglasInvalidasError, cargar_reglas
from duna_buzon.sincronizar import Contadores, ejecutar_pasada

NOMBRE_ARCHIVO_REGLAS = "reglas.json"
NOMBRE_ARCHIVO_ESTADO = "estado.json"


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
    config: Config, fuente: FuenteConfig, cliente_finanzas: FinanzasClient
) -> tuple[str, str | None]:
    """Runs one source's own pass end to end and returns `(linea, motivo)`
    -- the summary line this source's `estado.json` carries, and the stop
    reason (`None` if it finished cleanly). Never raises for a failure that
    is this source's own (auth, rules, the provider, Finanzas): a source
    that cannot even sign in is reported exactly like one that signs in and
    then has its pass stopped, so one source's trouble never stops
    another's."""
    directorio = str(modulo_fuentes.directorio_fuente(config.directorio_datos, fuente.id))

    def _detenida(motivo: str) -> tuple[str, str | None]:
        linea = registro.linea_resumen(Contadores(), motivo)
        cripto.asegurar_directorio_datos(Path(directorio))
        cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
        return linea, motivo

    try:
        if fuente.proveedor == "microsoft":
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

    proveedor = construir_proveedor(fuente, token, cursor_proveedor_inicial=cursor_proveedor_inicial)
    try:
        resultado = ejecutar_pasada(
            proveedor=proveedor,
            cliente_finanzas=cliente_finanzas,
            reglas=reglas,
            cursor_inicial=cursor_inicial,
            etiqueta_fuente=fuente.etiqueta_wire,
            cursor_proveedor_inicial=cursor_proveedor_inicial,
        )
    finally:
        proveedor.cerrar()

    if resultado.debe_persistir_cursor and resultado.cursor_final is not None:
        modulo_cursor.guardar_cursor(directorio, resultado.cursor_final)
    if resultado.debe_persistir_cursor and resultado.cursor_proveedor_final is not None:
        modulo_cursor.guardar_cursor_proveedor(directorio, resultado.cursor_proveedor_final)

    linea = registro.linea_resumen(resultado.contadores, resultado.motivo)
    cripto.asegurar_directorio_datos(Path(directorio))
    cripto.escribir_archivo_sensible(Path(directorio) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8"))
    return linea, resultado.motivo


def comando_sincronizar(config: Config) -> int:
    _migrar_si_necesario(config)
    activas = modulo_fuentes.fuentes_activas(config.directorio_datos)
    if not activas:
        print("error: no active sources configured", file=sys.stderr)
        return 1

    cliente_finanzas = FinanzasClient(config)
    algun_fallo = False
    try:
        for fuente in activas:
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
) -> int:
    """Writes a new `fuente.json` -- never signs in. `entrar --fuente
    <id>` is the next, separate step (build instructions for
    `BUZON-FUENTE-IMAP-1`)."""
    if proveedor != "imap":
        print(f"error: agregar-fuente does not support provider {proveedor!r} yet", file=sys.stderr)
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
        ),
    )
    print(f"fuente {id_fuente!r} agregada: ejecute 'entrar --fuente {id_fuente}' para firmar.")
    return 0
