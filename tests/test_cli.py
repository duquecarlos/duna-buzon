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


def _escribir_reglas_vacias(tmp_path: Path) -> None:
    (tmp_path / cli.NOMBRE_ARCHIVO_REGLAS).write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "resultado_falso_kwargs,motivo_esperado",
    [
        (
            {"detenida_por_rechazo_permanente": True, "motivo": "finanzas enviar http=403"},
            "finanzas enviar http=403",
        ),
        (
            {"detenida_por_error_transitorio": True, "motivo": "finanzas enviar http=503"},
            "finanzas enviar http=503",
        ),
        (
            {"detenida_por_autenticacion": True, "motivo": "graph listar http=401"},
            "graph listar http=401",
        ),
        (
            {"detenida_por_error_transitorio": True, "motivo": "graph adjuntos error=ConnectError"},
            "graph adjuntos error=ConnectError",
        ),
    ],
)
def test_sincronizar_detenida_imprime_el_motivo_en_stderr_y_en_estado(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resultado_falso_kwargs: dict,
    motivo_esperado: str,
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_reglas_vacias(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg: "token-falso")

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=2, omitidos_por_reglas=1), **resultado_falso_kwargs
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == f"parada: {motivo_esperado}"
    assert f"parada={motivo_esperado}" in salida.out

    assert cli.comando_estado(config) == 0
    salida_estado = capsys.readouterr()
    assert f"parada={motivo_esperado}" in salida_estado.out


def test_sincronizar_sin_detenerse_no_imprime_nada_en_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_reglas_vacias(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg: "token-falso")

    resultado_falso = ResultadoPasada(contadores=Contadores(vistos=1))
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert salida.err == ""
    assert "parada=" not in salida.out


def test_parada_nunca_imprime_el_token_el_remitente_ni_el_asunto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as json_mod

    import httpx

    from duna_buzon.finanzas import FinanzasClient
    from duna_buzon.graph import GraphClient

    token_graph = "TOKEN-GRAPH-SECRETO-NO-DEBE-APARECER"
    token_finanzas = "TOKEN-FINANZAS-SECRETO-NO-DEBE-APARECER"
    remitente = "secreto-remitente@no-debe-aparecer.com"
    asunto = "ASUNTO-SECRETO-NO-DEBE-APARECER"

    config = Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": token_finanzas,
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )
    (tmp_path / cli.NOMBRE_ARCHIVO_REGLAS).write_text(
        json_mod.dumps({"desde": "2026-01-01T00:00:00+00:00", "remitentes": [remitente], "palabras": []}),
        encoding="utf-8",
    )

    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "internetMessageId": "<m1@mail.example>",
                        "from": {"emailAddress": {"address": remitente}},
                        "subject": asunto,
                        "receivedDateTime": "2026-03-01T10:00:00Z",
                        "hasAttachments": False,
                    }
                ]
            },
        )

    def finanzas_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "nope"})

    monkeypatch.setattr(auth, "obtener_token", lambda cfg: token_graph)
    monkeypatch.setattr(
        cli,
        "GraphClient",
        lambda token: GraphClient(token, transport=httpx.MockTransport(graph_handler)),
    )
    monkeypatch.setattr(
        cli,
        "FinanzasClient",
        lambda cfg: FinanzasClient(cfg, transport=httpx.MockTransport(finanzas_handler)),
    )

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == "parada: finanzas enviar http=403"
    assert "parada=finanzas enviar http=403" in salida.out
    for marcador in (token_graph, token_finanzas, remitente, asunto):
        assert marcador not in salida.out
        assert marcador not in salida.err
