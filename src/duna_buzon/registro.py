"""The one line a pass ever prints: counts, the pass's own finish time and
retry count (BUZON-GRAPH-TIMEOUT-1) -- never a sender, a subject, a body,
a filename, a token, a cache or a Graph message id."""

from __future__ import annotations

import datetime

from duna_buzon.sincronizar import Contadores


def _formato_fecha_utc(momento: datetime.datetime) -> str:
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
) -> str:
    partes = [
        f"vistos={contadores.vistos}",
        f"reenviados={contadores.reenviados}",
        f"duplicados={contadores.duplicados}",
        f"omitidos_por_reglas={contadores.omitidos_por_reglas}",
        f"omitidos_por_413_422={contadores.omitidos_por_413_422}",
        f"adjuntos_descartados={contadores.adjuntos_descartados}",
    ]
    if reintentos is not None:
        partes.append(f"reintentos={reintentos}")
    if finalizado_en is not None:
        partes.append(f"finalizado_en={_formato_fecha_utc(finalizado_en)}")
    linea = " ".join(partes)
    if motivo is None:
        return linea
    return f"{linea} parada={motivo}"
