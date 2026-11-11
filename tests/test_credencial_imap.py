from __future__ import annotations

from pathlib import Path

import pytest

from duna_buzon import credencial_imap, cripto
from duna_buzon.config import Config

from .conftest import CLAVE_VALIDA_BASE64


def _config(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


def _directorio_fuente(tmp_path: Path) -> str:
    ruta = tmp_path / "fuentes" / "legal-duna"
    ruta.mkdir(parents=True, exist_ok=True)
    return str(ruta)


def test_entrar_lee_sin_eco_verifica_y_guarda_cifrado(
    tmp_path: Path, no_es_windows: None
) -> None:
    config = _config(tmp_path)
    directorio = _directorio_fuente(tmp_path)
    verificaciones: list[tuple[str, int, str, str]] = []

    def verificar_falso(host: str, puerto: int, usuario: str, password: str) -> None:
        verificaciones.append((host, puerto, usuario, password))

    credencial_imap.entrar(
        config,
        directorio,
        host="imap.zoho.com",
        puerto=993,
        usuario="legal@duna.solutions",
        leer_password=lambda: "la-contrasena-de-la-app",
        verificar_login=verificar_falso,
    )

    assert verificaciones == [("imap.zoho.com", 993, "legal@duna.solutions", "la-contrasena-de-la-app")]

    ruta_credencial = Path(directorio) / "credencial.bin"
    assert ruta_credencial.is_file()
    assert cripto.modo_octal(ruta_credencial) == 0o600

    descifrado = cripto.descifrar(ruta_credencial.read_bytes(), config.clave_cifrado)
    assert descifrado.decode("utf-8") == "la-contrasena-de-la-app"


def test_entrar_nunca_pasa_el_password_como_argumento_de_verificar(tmp_path: Path) -> None:
    # The seam receives the password as a normal function argument (never
    # on a command line / argv) -- this just pins that `entrar` never
    # calls `leer_password` more than once nor logs its return value.
    config = _config(tmp_path)
    directorio = _directorio_fuente(tmp_path)
    llamadas = []

    def leer_password() -> str:
        llamadas.append(1)
        return "secreta"

    credencial_imap.entrar(
        config,
        directorio,
        host="imap.zoho.com",
        puerto=993,
        usuario="legal@duna.solutions",
        leer_password=leer_password,
        verificar_login=lambda host, puerto, usuario, password: None,
    )
    assert len(llamadas) == 1


def test_entrar_con_login_invalido_no_guarda_nada(tmp_path: Path) -> None:
    config = _config(tmp_path)
    directorio = _directorio_fuente(tmp_path)

    def verificar_que_falla(host: str, puerto: int, usuario: str, password: str) -> None:
        raise credencial_imap.VerificacionLoginError("IMAP4.error")

    with pytest.raises(credencial_imap.VerificacionLoginError):
        credencial_imap.entrar(
            config,
            directorio,
            host="imap.zoho.com",
            puerto=993,
            usuario="legal@duna.solutions",
            leer_password=lambda: "contrasena-equivocada",
            verificar_login=verificar_que_falla,
        )

    assert not (Path(directorio) / "credencial.bin").exists()


def test_obtener_credencial_sin_nada_pide_entrar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    directorio = _directorio_fuente(tmp_path)
    with pytest.raises(credencial_imap.SinCredencialError, match="entrar"):
        credencial_imap.obtener_credencial(config, directorio)


def test_obtener_credencial_redondea_el_mismo_password(tmp_path: Path) -> None:
    config = _config(tmp_path)
    directorio = _directorio_fuente(tmp_path)

    credencial_imap.entrar(
        config,
        directorio,
        host="imap.zoho.com",
        puerto=993,
        usuario="legal@duna.solutions",
        leer_password=lambda: "la-contrasena-de-la-app",
        verificar_login=lambda host, puerto, usuario, password: None,
    )

    assert credencial_imap.obtener_credencial(config, directorio) == "la-contrasena-de-la-app"
