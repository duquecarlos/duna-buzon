"""The one line a pass ever prints: counts, the pass's own finish time,
retry count (BUZON-GRAPH-TIMEOUT-1), for a Graph source the `$top` the LIST
call was last using (BUZON-GRAPH-502-1), and -- only when nonzero, for a
provider that has one -- how many folders/messages that provider itself
skipped rather than let block the rest of the pass (BUZON-IMAP-MENSAJE-
AISLADO-1), plus how many messages an IMAP source's own date fallback
deliberately skipped (BUZON-IMAP-FECHA-DROP-VISIBLE-1) -- never a sender,
a subject, a body, a filename, a token, a cache or a Graph message id."""

from __future__ import annotations

import datetime

from duna_buzon.sincronizar import Contadores


def formato_fecha_utc(momento: datetime.datetime) -> str:
    """Public so `historial.py` can stamp a pass record with the exact same
    format `linea_resumen` itself uses for `finalizado_en`."""
    momento_utc = (
        momento.astimezone(datetime.UTC) if momento.tzinfo else momento.replace(tzinfo=datetime.UTC)
    )
    return momento_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def linea_resumen(
    contadores: Contadores,
    motivo: str | None = None,
    *,
    finalizado_en: datetime.datetime | None = None,
    reintentos: int | None = None,
    top: int | None = None,
    omitidos_por_error_proveedor: int = 0,
    omitidos_por_fecha_proveedor: int = 0,
) -> str:
    partes = [
        f"vistos={contadores.vistos}",
        f"reenviados={contadores.reenviados}",
        f"duplicados={contadores.duplicados}",
        f"omitidos_por_reglas={contadores.omitidos_por_reglas}",
        f"omitidos_por_413_422={contadores.omitidos_por_413_422}",
        f"adjuntos_descartados={contadores.adjuntos_descartados}",
    ]
    # Only shown when a provider actually skipped something -- a plain
    # Microsoft/Gmail pass, and every IMAP pass before this one ever
    # existed, never had anything to show here, so no existing line
    # changes (BUZON-IMAP-MENSAJE-AISLADO-1).
    if omitidos_por_error_proveedor:
        partes.append(f"omitidos_por_error_proveedor={omitidos_por_error_proveedor}")
    # Same shape, for `ClienteImap.omitidos_por_fecha_proveedor`
    # (BUZON-IMAP-FECHA-DROP-VISIBLE-1): a message the SINCE-date fallback
    # deliberately skipped, never a provider that has nothing to report.
    if omitidos_por_fecha_proveedor:
        partes.append(f"omitidos_por_fecha_proveedor={omitidos_por_fecha_proveedor}")
    if reintentos is not None:
        partes.append(f"reintentos={reintentos}")
    if top is not None:
        partes.append(f"top={top}")
    if finalizado_en is not None:
        partes.append(f"finalizado_en={formato_fecha_utc(finalizado_en)}")
    linea = " ".join(partes)
    if motivo is None:
        return linea
    return f"{linea} parada={motivo}"
