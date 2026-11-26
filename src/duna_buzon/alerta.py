"""Per-source markers that turn a silent problem into a visible one --
`$BUZON_DATOS/fuentes/<id>/alerta.json` and `.../visto.json`, written
beside that same source's own `cursor.json` and `historial.jsonl`.

`alerta.json` carries only a timestamp: the UTC instant the FIRST auth
failure of the CURRENT unbroken run happened. Absent means this source
authenticated on its last completed pass, or has never failed at all.
`cli.py` is the one caller that decides WHEN a pass counts as an auth
failure (`ProveedorAutenticacionError`, the Finanzas `unauthorized` status,
or the `SinCuentaError`/`SinCredencialError`/`ClaveInvalidaError` family
raised before a provider is even built) -- this module only persists the
one timestamp, set once and kept until a pass clears it.

`visto.json` (BUZON-ALERTA-SILENCIO-1) carries only a timestamp too: the UTC
instant of the MOST RECENT pass that saw at least one message
(`Contadores.vistos > 0`) -- never set-once like `alerta.json`'s own
marker, since here the LATEST sighting is what matters. Deliberately not
`historial.jsonl`: that file is bounded to `historial.CAPACIDAD` (20)
entries, which for a source that runs every few minutes can hold as little
as 3h20 of history -- far too short to notice a mailbox that quietly
stopped producing anything a day ago. Absent means this source has never
once seen a message -- not "used to see mail", so `cli.py`'s own
`_estado_silencio` never calls that silence."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from duna_buzon import cripto

NOMBRE_ARCHIVO = "alerta.json"
NOMBRE_ARCHIVO_VISTO = "visto.json"


def _ruta(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO


def _ruta_visto(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_VISTO


def cargar_auth_fallando_desde(directorio_fuente: str) -> datetime.datetime | None:
    ruta = _ruta(directorio_fuente)
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(datos["auth_fallando_desde"])


def marcar_auth_fallando(directorio_fuente: str, momento: datetime.datetime) -> None:
    """Sets `auth_fallando_desde` to `momento` ONLY when nothing is stored
    yet -- the first failure of an unbroken run is the one that sticks;
    every call after it, for the same run, is a no-op, by construction,
    so the alert's own `desde` always names the START of the run."""
    if cargar_auth_fallando_desde(directorio_fuente) is not None:
        return
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    contenido = json.dumps({"auth_fallando_desde": momento.isoformat()}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta(directorio_fuente), contenido)


def limpiar_auth_fallando(directorio_fuente: str) -> None:
    """Removes the marker if present -- a no-op when there was none."""
    _ruta(directorio_fuente).unlink(missing_ok=True)


def cargar_ultimo_visto_en(directorio_fuente: str) -> datetime.datetime | None:
    """The UTC instant of the most recent pass that saw at least one
    message, or `None` when this source has never once seen one."""
    ruta = _ruta_visto(directorio_fuente)
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(datos["ultimo_visto_en"])


def marcar_ultimo_visto(directorio_fuente: str, momento: datetime.datetime) -> None:
    """Sets `ultimo_visto_en` to `momento` UNCONDITIONALLY -- unlike
    `marcar_auth_fallando`'s own set-once marker, this one tracks the
    LATEST sighting, so every pass that sees mail moves it forward."""
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    contenido = json.dumps({"ultimo_visto_en": momento.isoformat()}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta_visto(directorio_fuente), contenido)
