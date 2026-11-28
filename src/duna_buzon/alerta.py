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
`_estado_silencio` never calls that silence FROM `visto.json` alone.

`primera_pasada.json` (BUZON-CARPETAS-CONTEO-1) carries only a timestamp
too: the UTC instant of the FIRST pass this connector EVER attempted for
this source, set once and kept forever after -- `visto.json` above is
`None` for a source that has NEVER once seen a message, and before this
marker existed that meant `_estado_silencio` could never call such a
source silent either, however many empty passes it ran: a mailbox that
never once produced anything from the very start stayed invisible forever,
which is exactly the failure BUZON-ALERTA-SILENCIO-1 was meant to end.
`cli.py`'s own `_estado_silencio` falls back to THIS marker only when
`visto.json` is absent, so a source that used to see mail still measures
silence from its own last sighting, never from this older marker.

`carpetas_actual.json`/`carpetas_base.json` (BUZON-SILENCIO-SIN-CORREO-
NUEVO-1) each carry one `{"carpetas_uidnext": {<carpeta>: <uidnext>}}`
map -- counts and UIDs only, never a subject, sender or address. `actual`
is simply the MOST RECENT pass's own map (`ClienteImap.carpetas_uidnext`,
BUZON-CARPETAS-CONTEO-1's own live-pass twin), overwritten every pass that
has one at all. `base` is the same shape but is the map from the START of
the CURRENT silence window: overwritten whenever a pass sees mail
(`vistos>0`, the window restarts) or set once, the very first time any map
is available at all, so a source that has never once seen mail still gets
a baseline to compare against instead of staying forever without one.
`cli.py`'s own `_proveedor_sin_cambios` compares the two: equal (same
folders, same UIDNEXTs) means nothing new has reached the provider since
the window started, so there is nothing in Buzon's own reach to check;
either one missing (no IMAP folder concept for this source, e.g. Microsoft/
Graph, or no pass with folder data yet) always reads as "could not
measure", never as "nothing to check"."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from duna_buzon import cripto

NOMBRE_ARCHIVO = "alerta.json"
NOMBRE_ARCHIVO_VISTO = "visto.json"
NOMBRE_ARCHIVO_PRIMERA_PASADA = "primera_pasada.json"
NOMBRE_ARCHIVO_CARPETAS_ACTUAL = "carpetas_actual.json"
NOMBRE_ARCHIVO_CARPETAS_BASE = "carpetas_base.json"


def _ruta(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO


def _ruta_visto(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_VISTO


def _ruta_primera_pasada(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_PRIMERA_PASADA


def _ruta_carpetas_actual(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_CARPETAS_ACTUAL


def _ruta_carpetas_base(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_CARPETAS_BASE


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


def cargar_primera_pasada_en(directorio_fuente: str) -> datetime.datetime | None:
    """The UTC instant of the FIRST pass this connector ever attempted for
    this source, or `None` when it has never attempted one at all."""
    ruta = _ruta_primera_pasada(directorio_fuente)
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(datos["primera_pasada_en"])


def marcar_primera_pasada(directorio_fuente: str, momento: datetime.datetime) -> None:
    """Sets `primera_pasada_en` to `momento` ONLY when nothing is stored
    yet -- the FIRST pass this source ever attempts is the one that
    sticks, set once and kept forever after (BUZON-CARPETAS-CONTEO-1), so
    `_estado_silencio`'s own fallback below always names when this
    connector actually started watching this source, never "now"."""
    if cargar_primera_pasada_en(directorio_fuente) is not None:
        return
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    contenido = json.dumps({"primera_pasada_en": momento.isoformat()}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta_primera_pasada(directorio_fuente), contenido)


def _cargar_mapa_carpetas(ruta: Path) -> dict[str, int] | None:
    if not ruta.is_file():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    mapa = datos.get("carpetas_uidnext") if isinstance(datos, dict) else None
    return mapa if isinstance(mapa, dict) else None


def cargar_carpetas_actual(directorio_fuente: str) -> dict[str, int] | None:
    """This source's own `{carpeta: uidnext}` map as of its MOST RECENT
    pass, or `None` when no pass has ever recorded one (no IMAP folder
    concept for this source, or no pass has completed with folder data
    yet)."""
    return _cargar_mapa_carpetas(_ruta_carpetas_actual(directorio_fuente))


def marcar_carpetas_actual(directorio_fuente: str, mapa: dict[str, int]) -> None:
    """Overwrites `carpetas_actual.json` UNCONDITIONALLY with `mapa` --
    every pass that has one replaces the last, exactly like
    `marcar_ultimo_visto`'s own latest-sighting marker."""
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    contenido = json.dumps({"carpetas_uidnext": mapa}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta_carpetas_actual(directorio_fuente), contenido)


def cargar_carpetas_base(directorio_fuente: str) -> dict[str, int] | None:
    """This source's own `{carpeta: uidnext}` map as of the START of the
    CURRENT silence window, or `None` when none has ever been recorded."""
    return _cargar_mapa_carpetas(_ruta_carpetas_base(directorio_fuente))


def marcar_carpetas_base(directorio_fuente: str, mapa: dict[str, int]) -> None:
    """Overwrites `carpetas_base.json` UNCONDITIONALLY with `mapa` -- the
    caller (`cli.py`) decides WHEN this should happen: on a pass that sees
    mail (the window restarts) or the very first time any map is ever
    available (so a source that has never once seen mail still gets a
    baseline instead of none at all), never on every pass."""
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    contenido = json.dumps({"carpetas_uidnext": mapa}).encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta_carpetas_base(directorio_fuente), contenido)
