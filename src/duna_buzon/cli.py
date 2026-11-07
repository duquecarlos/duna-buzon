"""The three commands -- `entrar`, `sincronizar`, `estado` -- wired to one
`Config`. `__main__.py` is the thin argparse shell around this module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from duna_buzon import auth, cripto
from duna_buzon import cursor as modulo_cursor
from duna_buzon import registro
from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.graph import GraphClient
from duna_buzon.reglas import cargar_reglas
from duna_buzon.sincronizar import ejecutar_pasada

NOMBRE_ARCHIVO_REGLAS = "reglas.json"
NOMBRE_ARCHIVO_ESTADO = "estado.json"


def comando_entrar(config: Config) -> int:
    auth.entrar(config)
    return 0


def comando_sincronizar(config: Config) -> int:
    try:
        token = auth.obtener_token(config)
    except (auth.SinCuentaError, cripto.ClaveInvalidaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    ruta_reglas = Path(config.directorio_datos) / NOMBRE_ARCHIVO_REGLAS
    reglas = cargar_reglas(ruta_reglas)
    cursor_inicial = modulo_cursor.cargar_cursor(config.directorio_datos, valor_inicial=reglas.desde)

    cliente_graph = GraphClient(token)
    cliente_finanzas = FinanzasClient(config)
    try:
        resultado = ejecutar_pasada(
            cliente_graph=cliente_graph,
            cliente_finanzas=cliente_finanzas,
            reglas=reglas,
            cursor_inicial=cursor_inicial,
        )
    finally:
        cliente_graph.cerrar()
        cliente_finanzas.cerrar()

    linea = registro.linea_resumen(resultado.contadores)
    print(linea)

    cripto.asegurar_directorio_datos(Path(config.directorio_datos))
    cripto.escribir_archivo_sensible(
        Path(config.directorio_datos) / NOMBRE_ARCHIVO_ESTADO, linea.encode("utf-8")
    )

    if resultado.debe_persistir_cursor and resultado.cursor_final is not None:
        modulo_cursor.guardar_cursor(config.directorio_datos, resultado.cursor_final)

    detenida = resultado.detenida_por_autenticacion or resultado.detenida_por_error_transitorio
    return 1 if detenida else 0


def comando_estado(config: Config) -> int:
    if not auth.cache_cargable(config):
        print("error: the cache could not be decrypted under the configured key", file=sys.stderr)
        return 1

    ruta_cursor = Path(config.directorio_datos) / modulo_cursor.NOMBRE_ARCHIVO
    if ruta_cursor.is_file():
        datos = json.loads(ruta_cursor.read_text(encoding="utf-8"))
        print(f"cursor={datos['cursor']}")
    else:
        print("cursor=none")

    ruta_estado = Path(config.directorio_datos) / NOMBRE_ARCHIVO_ESTADO
    if ruta_estado.is_file():
        print(ruta_estado.read_text(encoding="utf-8").strip())

    return 0
