"""`registro.linea_resumen`'s own formatting -- counts, in a fixed order,
plus the optional fields only a provider that has something to report
ever adds."""

from __future__ import annotations

from duna_buzon.registro import linea_resumen
from duna_buzon.sincronizar import Contadores


def test_omitidos_por_error_proveedor_ausente_cuando_es_cero() -> None:
    # Every existing Microsoft/Gmail pass, and every IMAP pass before
    # BUZON-IMAP-MENSAJE-AISLADO-1, never had anything to report here --
    # the line must stay byte-identical to what it always was.
    linea = linea_resumen(Contadores())
    assert "omitidos_por_error_proveedor" not in linea


def test_omitidos_por_error_proveedor_aparece_cuando_no_es_cero() -> None:
    linea = linea_resumen(Contadores(), omitidos_por_error_proveedor=2)
    assert "omitidos_por_error_proveedor=2" in linea
    # Sits with the other always-present counts, before the optional
    # reintentos/top/finalizado_en fields.
    assert linea == (
        "vistos=0 reenviados=0 duplicados=0 omitidos_por_reglas=0 "
        "omitidos_por_413_422=0 adjuntos_descartados=0 omitidos_por_error_proveedor=2"
    )


def test_omitidos_por_error_proveedor_convive_con_reintentos_y_top() -> None:
    linea = linea_resumen(Contadores(), reintentos=3, top=10, omitidos_por_error_proveedor=1)
    assert linea == (
        "vistos=0 reenviados=0 duplicados=0 omitidos_por_reglas=0 "
        "omitidos_por_413_422=0 adjuntos_descartados=0 omitidos_por_error_proveedor=1 "
        "reintentos=3 top=10"
    )


def test_omitidos_por_fecha_proveedor_ausente_cuando_es_cero() -> None:
    linea = linea_resumen(Contadores())
    assert "omitidos_por_fecha_proveedor" not in linea


def test_omitidos_por_fecha_proveedor_aparece_cuando_no_es_cero() -> None:
    linea = linea_resumen(Contadores(), omitidos_por_fecha_proveedor=2)
    assert linea == (
        "vistos=0 reenviados=0 duplicados=0 omitidos_por_reglas=0 "
        "omitidos_por_413_422=0 adjuntos_descartados=0 omitidos_por_fecha_proveedor=2"
    )


def test_omitidos_por_fecha_y_por_error_proveedor_conviven_en_ese_orden() -> None:
    linea = linea_resumen(Contadores(), omitidos_por_error_proveedor=1, omitidos_por_fecha_proveedor=2)
    assert linea == (
        "vistos=0 reenviados=0 duplicados=0 omitidos_por_reglas=0 "
        "omitidos_por_413_422=0 adjuntos_descartados=0 omitidos_por_error_proveedor=1 "
        "omitidos_por_fecha_proveedor=2"
    )
