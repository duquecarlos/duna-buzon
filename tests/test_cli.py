from __future__ import annotations

from pathlib import Path

import pytest

from duna_buzon import auth, cli, cripto
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


def test_estado_sin_nada_todavia_es_saludable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "cursor=none" in salida.out


def test_estado_reporta_cursor_y_la_ultima_pasada(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    import datetime

    from duna_buzon import cursor as modulo_cursor

    modulo_cursor.guardar_cursor(config.directorio_datos, datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC))
    cripto.asegurar_directorio_datos(Path(config.directorio_datos))
    cripto.escribir_archivo_sensible(
        Path(config.directorio_datos) / cli.NOMBRE_ARCHIVO_ESTADO, b"vistos=1 reenviados=1"
    )

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "2026-03-01" in salida.out
    assert "vistos=1" in salida.out


def test_estado_con_clave_equivocada_falla(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    otra_clave = bytes((b + 1) % 256 for b in config.clave_cifrado)
    cripto.asegurar_directorio_datos(Path(config.directorio_datos))
    cripto.escribir_archivo_sensible(
        Path(config.directorio_datos) / "cache.bin", cripto.cifrar(b"{}", otra_clave)
    )

    assert cli.comando_estado(config) == 1
    salida = capsys.readouterr()
    assert "clave" in salida.err.lower() or "key" in salida.err.lower()


def test_entrar_delega_en_auth_entrar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    llamado_con: list[Config] = []

    def falso_entrar(cfg: Config) -> None:
        llamado_con.append(cfg)

    monkeypatch.setattr(auth, "entrar", falso_entrar)
    assert cli.comando_entrar(config) == 0
    assert llamado_con == [config]


def test_sincronizar_sin_cuenta_retorna_1_y_nunca_toca_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)

    def falso_obtener_token(cfg: Config):  # type: ignore[no-untyped-def]
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert "entrar" in salida.err
