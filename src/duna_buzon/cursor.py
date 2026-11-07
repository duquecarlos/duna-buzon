"""The sync cursor -- `$BUZON_DATOS/cursor.json`, mode 0600. Advances only
past messages a pass stored or deliberately skipped (rule mismatch, or a
permanent 413/422 defect); never past a message a pass could not finish
because of a failure, by simply never being written for a pass that stops
early. A 10-minute overlap on every read is this module's caller's job
(`sincronizar.py`), not this one's -- Finanzas is idempotent on
`id_externo`, so a re-sent overlap message costs nothing.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from duna_buzon import cripto

NOMBRE_ARCHIVO = "cursor.json"

SOLAPE = datetime.timedelta(minutes=10)


def _ruta(directorio_datos: str) -> Path:
    return Path(directorio_datos) / NOMBRE_ARCHIVO


def cargar_cursor(directorio_datos: str, *, valor_inicial: datetime.datetime) -> datetime.datetime:
    """`valor_inicial` is `reglas.json`'s own `desde`, used only the very
    first time -- once a cursor file exists, it alone governs."""
    ruta = _ruta(directorio_datos)
    if not ruta.is_file():
        return valor_inicial
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(datos["cursor"])


def guardar_cursor(directorio_datos: str, momento: datetime.datetime) -> None:
    cripto.asegurar_directorio_datos(Path(directorio_datos))
    contenido = json.dumps({"cursor": momento.isoformat()}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta(directorio_datos), contenido)
