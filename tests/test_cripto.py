from __future__ import annotations

import os
from pathlib import Path

import pytest

from duna_buzon import cripto

_CLAVE_A = b"\x01" * 32
_CLAVE_B = b"\x02" * 32


def test_cifrar_descifrar_ida_y_vuelta() -> None:
    datos = b"contenido secreto de prueba"
    cifrado = cripto.cifrar(datos, _CLAVE_A)
    assert cripto.descifrar(cifrado, _CLAVE_A) == datos


def test_cada_escritura_usa_un_nonce_distinto() -> None:
    datos = b"los mismos datos claros"
    primero = cripto.cifrar(datos, _CLAVE_A)
    segundo = cripto.cifrar(datos, _CLAVE_A)
    assert primero != segundo
    assert primero[:12] != segundo[:12]


def test_clave_equivocada_es_rechazada() -> None:
    cifrado = cripto.cifrar(b"datos", _CLAVE_A)
    with pytest.raises(cripto.ClaveInvalidaError):
        cripto.descifrar(cifrado, _CLAVE_B)


def test_directorio_de_datos_es_modo_0700(tmp_path: Path, no_es_windows: None) -> None:
    directorio = tmp_path / "datos"
    cripto.asegurar_directorio_datos(directorio)
    assert cripto.modo_octal(directorio) == 0o700


def test_archivo_sensible_es_modo_0600(tmp_path: Path, no_es_windows: None) -> None:
    ruta = tmp_path / "datos" / "secreto.bin"
    cripto.escribir_archivo_sensible(ruta, b"contenido")
    assert cripto.modo_octal(ruta) == 0o600
    assert ruta.read_bytes() == b"contenido"


def test_archivo_sensible_nunca_tiene_una_ventana_con_modo_amplio(
    tmp_path: Path, no_es_windows: None
) -> None:
    # The file is created O_CREAT with the final mode already, never
    # chmod'd open-then-narrow.
    ruta = tmp_path / "datos" / "secreto.bin"
    descriptor_original = os.open

    modos_vistos: list[int] = []

    def open_instrumentado(ruta_arg, flags, mode=0o777):  # type: ignore[no-untyped-def]
        resultado = descriptor_original(ruta_arg, flags, mode)
        modos_vistos.append(mode)
        return resultado

    os.open = open_instrumentado  # type: ignore[assignment]
    try:
        cripto.escribir_archivo_sensible(ruta, b"x")
    finally:
        os.open = descriptor_original  # type: ignore[assignment]

    assert modos_vistos == [0o600]
