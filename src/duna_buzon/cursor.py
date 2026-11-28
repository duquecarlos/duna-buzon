"""The sync cursor -- `$BUZON_DATOS/cursor.json`, mode 0600. Advances only
past messages a pass stored or deliberately skipped (rule mismatch, or a
permanent 413/422 defect); never past a message a pass could not finish
because of a failure, by simply never being written for a pass that stops
early. A 10-minute overlap on every read is this module's caller's job
(`sincronizar.py`), not this one's -- Finanzas is idempotent on
`id_externo`, so a re-sent overlap message costs nothing.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import tempfile
from pathlib import Path

from duna_buzon import cripto

NOMBRE_ARCHIVO = "cursor.json"
NOMBRE_ARCHIVO_PROVEEDOR = "cursor_proveedor.json"

SOLAPE = datetime.timedelta(minutes=10)


def _ruta(directorio_datos: str) -> Path:
    return Path(directorio_datos) / NOMBRE_ARCHIVO


def _ruta_proveedor(directorio_datos: str) -> Path:
    return Path(directorio_datos) / NOMBRE_ARCHIVO_PROVEEDOR


def cargar_cursor(directorio_datos: str, *, valor_inicial: datetime.datetime) -> datetime.datetime:
    """`valor_inicial` is `reglas.json`'s own `desde`, used only the very
    first time -- once a cursor file exists, it alone governs."""
    ruta = _ruta(directorio_datos)
    if not ruta.is_file():
        return valor_inicial
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(datos["cursor"])


def _escribir_json_atomico(ruta: Path, contenido: bytes) -> None:
    """Writes `contenido` to `ruta` via a temp file in the SAME directory
    followed by `os.replace` -- never a window where a reader (or a
    concurrent `sincronizar` pass) could see a truncated or half-written
    file, and a crash or kill mid-write leaves the OLD file exactly as it
    was rather than a corrupt one `cargar_cursor`/`cargar_cursor_proveedor`
    cannot parse (BUZON-SIGUIENTE-SEGUIMIENTO-2026-11-28-C: a truncated
    `cursor.json` raises uncaught out of `cargar_cursor`, which
    `comando_sincronizar`'s own `for fuente in activas:` loop does not
    wrap per source -- one corrupted file stops every source still to run
    that pass, not only its own). The same shape
    `fuentes.guardar_fuente_atomico` already set for `fuente.json`."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=str(ruta.parent), prefix=".cursor-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido)
        if os.name == "posix":
            os.chmod(temporal, 0o600)
        os.replace(temporal, ruta)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporal)
        raise


def guardar_cursor(directorio_datos: str, momento: datetime.datetime) -> None:
    """Writes `cursor.json` atomically (see `_escribir_json_atomico`) --
    every caller gets the same crash-safety, not only the operator-
    triggered `rebobinar-cursor --aplicar` path below."""
    cripto.asegurar_directorio_datos(Path(directorio_datos))
    contenido = json.dumps({"cursor": momento.isoformat()}).encode("utf-8")
    _escribir_json_atomico(_ruta(directorio_datos), contenido)


def guardar_cursor_atomico(directorio_datos: str, momento: datetime.datetime) -> None:
    """Kept as its own name for `rebobinar-cursor --aplicar`'s own call
    site (BUZON-CURSOR-REBOBINAR-CLI-1) and its own tests -- `guardar_cursor`
    itself now writes atomically too, so this is an explicit alias, not a
    second implementation."""
    guardar_cursor(directorio_datos, momento)


def cargar_cursor_proveedor(directorio_datos: str) -> str | None:
    """The provider's own opaque resume token (e.g. IMAP's
    `"<uidvalidity>:<uid>"`, from `graph.MensajeResumen.cursor_proveedor`)
    -- `None` for a provider that never sets one (Microsoft, today), and
    for a source that has not yet completed a pass."""
    ruta = _ruta_proveedor(directorio_datos)
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datos["cursor_proveedor"]


def guardar_cursor_proveedor(directorio_datos: str, valor: str) -> None:
    """Writes `cursor_proveedor.json` atomically, for the same reason as
    `guardar_cursor` above."""
    cripto.asegurar_directorio_datos(Path(directorio_datos))
    contenido = json.dumps({"cursor_proveedor": valor}).encode("utf-8")
    _escribir_json_atomico(_ruta_proveedor(directorio_datos), contenido)


def borrar_cursor_proveedor(directorio_datos: str) -> None:
    """Removes `cursor_proveedor.json` if present. `rebobinar-cursor
    --aplicar` (BUZON-CURSOR-REBOBINAR-CLI-1) calls this alongside
    `guardar_cursor_atomico`: an IMAP source's own per-folder UID
    watermark (`cliente_imap.py`'s `_buscar_uids`) takes precedence over
    the date cursor whenever one is stored, so rewinding `cursor.json`
    alone would not make that source re-read anything -- clearing this
    file forces its next pass back onto the date-based `SINCE` fallback,
    from the rewound date. Microsoft and Gmail never read this file at
    all (`graph.py`, `gmail.py` are purely date-driven), so removing it
    is a safe no-op for them."""
    _ruta_proveedor(directorio_datos).unlink(missing_ok=True)
