"""The one line a pass ever prints: counts, never a sender, a subject, a
body, a filename, a token, a cache or a Graph message id."""

from __future__ import annotations

from duna_buzon.sincronizar import Contadores


def linea_resumen(contadores: Contadores, motivo: str | None = None) -> str:
    base = (
        f"vistos={contadores.vistos} "
        f"reenviados={contadores.reenviados} "
        f"duplicados={contadores.duplicados} "
        f"omitidos_por_reglas={contadores.omitidos_por_reglas} "
        f"omitidos_por_413_422={contadores.omitidos_por_413_422} "
        f"adjuntos_descartados={contadores.adjuntos_descartados}"
    )
    if motivo is None:
        return base
    return f"{base} parada={motivo}"
