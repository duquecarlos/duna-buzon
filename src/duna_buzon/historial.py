"""Bounded per-source pass history -- `$BUZON_DATOS/fuentes/<id>/historial.jsonl`,
written beside `estado.json`'s own single-pass summary (never replacing it --
every reader of `estado.json` keeps working unchanged). Keeps the most
recent `CAPACIDAD` pass summaries so bulk/background work stays visible
after it runs (`BUZON-ESTADO-HISTORIAL-PASADAS-1`, follow-up to
`BUZON-HUB-CENSO-2026-11-19`, hub principle `hit_01m7syzg15w97bzrbfz4dmc6fq`):
a pass that forwarded nothing, or failed, no longer erases what the ones
before it did, because `estado.json` was always overwritten on every pass.

Every write (`agregar_pasada`) rewrites the whole file atomically -- a temp
file in the SAME directory, then `os.replace` -- trimmed to the cap in that
same write, so a reader never sees more than `CAPACIDAD` lines and never a
half-written file. One JSON object per line, oldest first.

Counts only, same invariant as `estado.json` and `registro.py`'s own summary
line -- never a subject, a sender, or any other message content. `errors` is
the pass's own stop reason (`sincronizar.ResultadoPasada.motivo`, already a
controlled, content-free vocabulary -- "auth error=...", "finanzas enviar
http=...", never message content), or `None` for a pass that finished
cleanly.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

NOMBRE_ARCHIVO = "historial.jsonl"

# How many pass summaries are kept on disk -- `estado --pasadas N` can never
# show more than this, however large `N` is.
CAPACIDAD = 20


@dataclass(frozen=True)
class RegistroPasada:
    finalizado_en: str
    vistos: int
    reenviados: int
    duplicados: int
    omitidos: int
    errors: str | None = None


def _ruta(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO


def _a_dict(registro: RegistroPasada) -> dict[str, object]:
    return {
        "finalizado_en": registro.finalizado_en,
        "vistos": registro.vistos,
        "reenviados": registro.reenviados,
        "duplicados": registro.duplicados,
        "omitidos": registro.omitidos,
        "errors": registro.errors,
    }


def _registro_desde_dict(datos: dict[str, object]) -> RegistroPasada:
    return RegistroPasada(
        finalizado_en=str(datos["finalizado_en"]),
        vistos=int(datos["vistos"]),  # type: ignore[arg-type]
        reenviados=int(datos["reenviados"]),  # type: ignore[arg-type]
        duplicados=int(datos["duplicados"]),  # type: ignore[arg-type]
        omitidos=int(datos["omitidos"]),  # type: ignore[arg-type]
        errors=datos.get("errors"),  # type: ignore[arg-type]
    )


def _escribir_atomico(ruta: Path, registros: list[RegistroPasada]) -> None:
    contenido = "".join(json.dumps(_a_dict(r)) + "\n" for r in registros)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=str(ruta.parent), prefix=".historial-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido.encode("utf-8"))
        if os.name == "posix":
            os.chmod(temporal, 0o600)
        os.replace(temporal, ruta)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporal)
        raise


def leer_historial(directorio_fuente: str) -> tuple[list[RegistroPasada], int]:
    """Every pass recorded, oldest first, plus how many lines were corrupt
    and had to be skipped. A missing file is an empty history, not an error.
    A line that is not valid JSON, or is missing a required field, is
    skipped and counted -- never raised, so one bad line never hides every
    other pass."""
    ruta = _ruta(directorio_fuente)
    if not ruta.is_file():
        return [], 0
    registros: list[RegistroPasada] = []
    descartadas = 0
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            datos = json.loads(linea)
            registros.append(_registro_desde_dict(datos))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            descartadas += 1
    return registros, descartadas


def agregar_pasada(directorio_fuente: str, registro: RegistroPasada) -> None:
    """Appends one pass's summary, then rewrites the file keeping only the
    most recent `CAPACIDAD` entries. Reads first with `leer_historial`, so a
    corrupt line already on disk is dropped rather than carried forward --
    self-healing on the very next pass, never a reason to fail this one."""
    anteriores, _ = leer_historial(directorio_fuente)
    recortadas = [*anteriores, registro][-CAPACIDAD:]
    _escribir_atomico(_ruta(directorio_fuente), recortadas)
