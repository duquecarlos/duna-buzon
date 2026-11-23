"""Per-source marker for an unbroken run of authentication failures --
`$BUZON_DATOS/fuentes/<id>/alerta.json`, written beside that same source's
own `cursor.json` and `historial.jsonl`. Carries only a timestamp: the UTC
instant the FIRST auth failure of the CURRENT unbroken run happened. Absent
means this source authenticated on its last completed pass, or has never
failed at all.

`cli.py` is the one caller that decides WHEN a pass counts as an auth
failure (`ProveedorAutenticacionError`, the Finanzas `unauthorized` status,
or the `SinCuentaError`/`SinCredencialError`/`ClaveInvalidaError` family
raised before a provider is even built) -- this module only persists the
one timestamp, set once and kept until a pass clears it.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from duna_buzon import cripto

NOMBRE_ARCHIVO = "alerta.json"


def _ruta(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO


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
