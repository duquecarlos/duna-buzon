from __future__ import annotations

import json
from pathlib import Path

from duna_buzon import historial


def _registro(n: int) -> historial.RegistroPasada:
    return historial.RegistroPasada(
        finalizado_en=f"2026-11-{n:02d}T10:00:00Z",
        vistos=n,
        reenviados=n,
        duplicados=0,
        omitidos=0,
        errors=None,
    )


def test_sin_archivo_es_historial_vacio(tmp_path: Path) -> None:
    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert registros == []
    assert descartadas == 0


def test_agregar_y_leer_ida_y_vuelta(tmp_path: Path) -> None:
    historial.agregar_pasada(str(tmp_path), _registro(1))
    historial.agregar_pasada(str(tmp_path), _registro(2))

    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert descartadas == 0
    assert [r.vistos for r in registros] == [1, 2]
    assert registros[0].finalizado_en == "2026-11-01T10:00:00Z"
    assert registros[1].errors is None


def test_agregar_conserva_el_error_de_la_pasada(tmp_path: Path) -> None:
    con_error = historial.RegistroPasada(
        finalizado_en="2026-11-05T10:00:00Z",
        vistos=0,
        reenviados=0,
        duplicados=0,
        omitidos=0,
        errors="finanzas enviar http=500",
    )
    historial.agregar_pasada(str(tmp_path), con_error)

    registros, _ = historial.leer_historial(str(tmp_path))
    assert registros[0].errors == "finanzas enviar http=500"


def test_se_recorta_a_la_capacidad(tmp_path: Path) -> None:
    for n in range(1, historial.CAPACIDAD + 6):
        historial.agregar_pasada(str(tmp_path), _registro(n))

    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert descartadas == 0
    assert len(registros) == historial.CAPACIDAD
    # The cap drops the OLDEST entries first -- the most recent
    # `CAPACIDAD` passes survive, oldest-first within that window.
    esperados = list(range(6, historial.CAPACIDAD + 6))
    assert [r.vistos for r in registros] == esperados


def test_una_linea_corrupta_se_descarta_y_se_cuenta(tmp_path: Path) -> None:
    ruta = tmp_path / historial.NOMBRE_ARCHIVO
    ruta.write_text(
        json.dumps(
            {
                "finalizado_en": "2026-11-01T10:00:00Z",
                "vistos": 1,
                "reenviados": 1,
                "duplicados": 0,
                "omitidos": 0,
                "errors": None,
            }
        )
        + "\n"
        + "esto no es json\n"
        + json.dumps(
            {
                "finalizado_en": "2026-11-02T10:00:00Z",
                "vistos": 2,
                "reenviados": 2,
                "duplicados": 0,
                "omitidos": 0,
                "errors": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert descartadas == 1
    assert [r.vistos for r in registros] == [1, 2]


def test_una_linea_con_campo_faltante_se_descarta_y_se_cuenta(tmp_path: Path) -> None:
    ruta = tmp_path / historial.NOMBRE_ARCHIVO
    ruta.write_text(json.dumps({"finalizado_en": "2026-11-01T10:00:00Z", "vistos": 1}) + "\n", encoding="utf-8")

    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert registros == []
    assert descartadas == 1


def test_agregar_sobre_una_linea_corrupta_la_descarta_del_archivo(tmp_path: Path) -> None:
    ruta = tmp_path / historial.NOMBRE_ARCHIVO
    ruta.write_text("esto no es json\n", encoding="utf-8")

    historial.agregar_pasada(str(tmp_path), _registro(1))

    registros, descartadas = historial.leer_historial(str(tmp_path))
    assert descartadas == 0
    assert [r.vistos for r in registros] == [1]


def test_archivo_de_historial_es_modo_0600(tmp_path: Path, no_es_windows: None) -> None:
    from duna_buzon import cripto

    historial.agregar_pasada(str(tmp_path), _registro(1))
    ruta = tmp_path / historial.NOMBRE_ARCHIVO
    assert cripto.modo_octal(ruta) == 0o600
