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


def test_guardar_cursor_atomico_ida_y_vuelta(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 3, 1, 10, 30, tzinfo=datetime.UTC)
    cursor.guardar_cursor_atomico(str(tmp_path), momento)
    cargado = cursor.cargar_cursor(str(tmp_path), valor_inicial=datetime.datetime(2000, 1, 1))
    assert cargado == momento


def test_guardar_cursor_atomico_no_deja_archivo_tmp(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)
    cursor.guardar_cursor_atomico(str(tmp_path), momento)
    nombres = [p.name for p in tmp_path.iterdir()]
    assert cursor.NOMBRE_ARCHIVO in nombres
    assert not any(n.endswith(".tmp") for n in nombres)


def test_guardar_cursor_atomico_sobreescribe_un_cursor_existente(tmp_path: Path) -> None:
    primero = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    segundo = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)
    cursor.guardar_cursor(str(tmp_path), primero)
    cursor.guardar_cursor_atomico(str(tmp_path), segundo)
    cargado = cursor.cargar_cursor(str(tmp_path), valor_inicial=datetime.datetime(2000, 1, 1))
    assert cargado == segundo


def test_guardar_cursor_atomico_archivo_es_modo_0600(tmp_path: Path, no_es_windows: None) -> None:
    from duna_buzon import cripto

    momento = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)
    cursor.guardar_cursor_atomico(str(tmp_path), momento)
    ruta = tmp_path / cursor.NOMBRE_ARCHIVO
    assert cripto.modo_octal(ruta) == 0o600


def test_borrar_cursor_proveedor_sin_archivo_no_falla(tmp_path: Path) -> None:
    cursor.borrar_cursor_proveedor(str(tmp_path))  # no-op, never raises
    assert cursor.cargar_cursor_proveedor(str(tmp_path)) is None


def test_borrar_cursor_proveedor_quita_el_archivo_existente(tmp_path: Path) -> None:
    cursor.guardar_cursor_proveedor(str(tmp_path), "123:456")
    assert cursor.cargar_cursor_proveedor(str(tmp_path)) == "123:456"
    cursor.borrar_cursor_proveedor(str(tmp_path))
    assert cursor.cargar_cursor_proveedor(str(tmp_path)) is None
    assert not (tmp_path / cursor.NOMBRE_ARCHIVO_PROVEEDOR).exists()
