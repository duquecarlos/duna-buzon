from __future__ import annotations

from pathlib import Path

import pytest

from duna_buzon import cli
from duna_buzon.__main__ import main
from duna_buzon.config import Config

from .conftest import CLAVE_VALIDA_BASE64


def test_sin_argumentos_es_uso_incorrecto(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_comando_desconocido_es_uso_incorrecto(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lo-que-sea"]) == 2


def test_variable_faltante_devuelve_1_y_nombra_la_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BUZON_GRAPH_CLIENT_ID", raising=False)
    monkeypatch.setenv("BUZON_CLAVE_CIFRADO", CLAVE_VALIDA_BASE64)
    monkeypatch.setenv("FINANZAS_BUZON_TOKEN", "token")
    assert main(["estado"]) == 1
    assert "BUZON_GRAPH_CLIENT_ID" in capsys.readouterr().err


def test_estado_se_despacha_con_la_config_del_entorno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUZON_GRAPH_CLIENT_ID", "cliente")
    monkeypatch.setenv("BUZON_CLAVE_CIFRADO", CLAVE_VALIDA_BASE64)
    monkeypatch.setenv("FINANZAS_BUZON_TOKEN", "token")
    monkeypatch.setenv("BUZON_DATOS", str(tmp_path))

    recibido: list[Config] = []

    def comando_falso(config: Config) -> int:
        recibido.append(config)
        return 0

    monkeypatch.setattr(cli, "comando_estado", comando_falso)
    assert main(["estado"]) == 0
    assert recibido[0].directorio_datos == str(tmp_path)
