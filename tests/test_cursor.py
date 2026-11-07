from __future__ import annotations

import datetime
from pathlib import Path

from duna_buzon import cursor


def test_sin_archivo_usa_el_valor_inicial(tmp_path: Path) -> None:
    inicial = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    assert cursor.cargar_cursor(str(tmp_path), valor_inicial=inicial) == inicial


def test_guardar_y_cargar_ida_y_vuelta(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 3, 1, 10, 30, tzinfo=datetime.UTC)
    cursor.guardar_cursor(str(tmp_path), momento)
    cargado = cursor.cargar_cursor(str(tmp_path), valor_inicial=datetime.datetime(2000, 1, 1))
    assert cargado == momento


def test_archivo_de_cursor_es_modo_0600(tmp_path: Path, no_es_windows: None) -> None:
    from duna_buzon import cripto

    momento = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)
    cursor.guardar_cursor(str(tmp_path), momento)
    ruta = tmp_path / cursor.NOMBRE_ARCHIVO
    assert cripto.modo_octal(ruta) == 0o600


def test_el_solape_es_de_diez_minutos() -> None:
    assert cursor.SOLAPE == datetime.timedelta(minutes=10)
