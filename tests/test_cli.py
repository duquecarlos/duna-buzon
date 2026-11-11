from __future__ import annotations

import json
from pathlib import Path

import pytest

from duna_buzon import auth, cli, cripto
from duna_buzon.config import Config
from duna_buzon.fuentes import FuenteConfig, guardar_fuente

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


def _escribir_fuente(
    tmp_path: Path,
    *,
    id: str = "hotmail-carlos",
    proveedor: str = "microsoft",
    direccion: str = "carlos@hotmail.com",
    etiqueta: str = "Hotmail",
    token_finanzas_ref: str = "carlos",
    activa: bool = True,
) -> Path:
    guardar_fuente(
        str(tmp_path),
        FuenteConfig(
            id=id,
            proveedor=proveedor,  # type: ignore[arg-type]
            direccion=direccion,
            etiqueta=etiqueta,
            token_finanzas_ref=token_finanzas_ref,
            activa=activa,
        ),
    )
    return tmp_path / "fuentes" / id


def _escribir_reglas_vacias(directorio: Path) -> None:
    (directorio / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
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

    directorio = _escribir_fuente(tmp_path)
    modulo_cursor.guardar_cursor(str(directorio), datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC))
    cripto.escribir_archivo_sensible(directorio / cli.NOMBRE_ARCHIVO_ESTADO, b"vistos=1 reenviados=1")

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos cursor=2026-03-01" in salida.out
    assert "fuente=hotmail-carlos vistos=1 reenviados=1" in salida.out


def test_estado_con_clave_equivocada_falla(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    otra_clave = bytes((b + 1) % 256 for b in config.clave_cifrado)
    directorio = _escribir_fuente(tmp_path)
    cripto.escribir_archivo_sensible(directorio / "credencial.bin", cripto.cifrar(b"{}", otra_clave))

    assert cli.comando_estado(config) == 1
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos" in salida.err
    assert "key" in salida.err.lower()


def test_entrar_delega_en_auth_entrar_con_la_sola_fuente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    llamado_con: list[tuple[Config, str]] = []

    def falso_entrar(cfg: Config, directorio_fuente: str) -> None:
        llamado_con.append((cfg, directorio_fuente))

    monkeypatch.setattr(auth, "entrar", falso_entrar)
    assert cli.comando_entrar(config, None) == 0
    assert llamado_con == [(config, str(directorio))]


def test_entrar_sin_fuente_y_varias_configuradas_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos")
    _escribir_fuente(tmp_path, id="gmail-carlos", proveedor="imap")

    assert cli.comando_entrar(config, None) == 1
    assert "--fuente" in capsys.readouterr().err


def test_entrar_con_id_desconocido_es_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path)

    assert cli.comando_entrar(config, "no-existe") == 1
    assert "no-existe" in capsys.readouterr().err


def test_entrar_sobre_fuente_no_microsoft_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="gmail-carlos", proveedor="imap")

    assert cli.comando_entrar(config, "gmail-carlos") == 1
    assert "gmail-carlos" in capsys.readouterr().err


def test_sincronizar_sin_fuentes_activas_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_sincronizar(config) == 1
    assert "no active sources" in capsys.readouterr().err


def test_sincronizar_sin_cuenta_retorna_1_y_nunca_toca_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    _escribir_reglas_vacias(directorio)

    def falso_obtener_token(cfg: Config, directorio_fuente: str):  # type: ignore[no-untyped-def]
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert "entrar" in salida.err
    assert "fuente=hotmail-carlos" in salida.err


def _escribir_fuente_y_reglas(tmp_path: Path) -> Path:
    directorio = _escribir_fuente(tmp_path)
    _escribir_reglas_vacias(directorio)
    return directorio


class _ProveedorFalso:
    def cerrar(self) -> None:
        pass


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
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(cli, "construir_proveedor", lambda fuente, token: _ProveedorFalso())

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=2, omitidos_por_reglas=1), **resultado_falso_kwargs
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == f"fuente=hotmail-carlos parada: {motivo_esperado}"
    assert f"fuente=hotmail-carlos vistos=2" in salida.out
    assert f"parada={motivo_esperado}" in salida.out

    assert cli.comando_estado(config) == 0
    salida_estado = capsys.readouterr()
    assert f"parada={motivo_esperado}" in salida_estado.out


def test_sincronizar_sin_detenerse_no_imprime_nada_en_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(cli, "construir_proveedor", lambda fuente, token: _ProveedorFalso())

    resultado_falso = ResultadoPasada(contadores=Contadores(vistos=1))
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert salida.err == ""
    assert "parada=" not in salida.out


def test_una_fuente_fallando_no_detiene_a_las_demas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    directorio_b = _escribir_fuente(tmp_path, id="otra-fuente")
    _escribir_reglas_vacias(directorio_b)

    def falso_obtener_token(cfg: Config, directorio_fuente: str) -> str:
        if directorio_fuente.endswith("otra-fuente"):
            raise auth.SinCuentaError("no account is signed in: run entrar")
        return "token-falso"

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)
    monkeypatch.setattr(cli, "construir_proveedor", lambda fuente, token: _ProveedorFalso())
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=3))
    )

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos vistos=3" in salida.out
    assert "fuente=otra-fuente" in salida.err
    assert "entrar" in salida.err


def test_parada_nunca_imprime_el_token_el_remitente_ni_el_asunto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    directorio = _escribir_fuente(tmp_path)
    (directorio / "reglas.json").write_text(
        json.dumps({"desde": "2026-01-01T00:00:00+00:00", "remitentes": [remitente], "palabras": []}),
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

    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: token_graph)
    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token: GraphClient(token, transport=httpx.MockTransport(graph_handler)),
    )
    monkeypatch.setattr(
        cli,
        "FinanzasClient",
        lambda cfg: FinanzasClient(cfg, transport=httpx.MockTransport(finanzas_handler)),
    )

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == "fuente=hotmail-carlos parada: finanzas enviar http=403"
    assert "parada=finanzas enviar http=403" in salida.out
    for marcador in (token_graph, token_finanzas, remitente, asunto):
        assert marcador not in salida.out
        assert marcador not in salida.err


def test_fuentes_lista_sin_secretos(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", direccion="carlos@hotmail.com")
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", activa=False)

    assert cli.comando_fuentes(config) == 0
    salida = capsys.readouterr().out
    assert "hotmail-carlos proveedor=microsoft direccion=carlos@hotmail.com" in salida
    assert "estado=activa" in salida
    assert "legal-duna proveedor=imap" in salida
    assert "estado=inactiva" in salida


def test_fuentes_sin_nada_configurado(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert cli.comando_fuentes(config) == 0
    assert "no sources configured" in capsys.readouterr().out


def test_migracion_automatica_desde_sincronizar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The legacy single-mailbox layout at the data root migrates itself
    into `fuentes/hotmail-carlos/` the first time any command runs, and
    the pass then proceeds against the migrated source."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    (tmp_path / "cursor.json").write_text('{"cursor": "2026-03-01T10:00:00+00:00"}', encoding="utf-8")
    _escribir_reglas_vacias(tmp_path)

    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(cli, "construir_proveedor", lambda fuente, token: _ProveedorFalso())
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=5))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos vistos=5" in salida.out
    assert not (tmp_path / "cursor.json").exists()
    assert (tmp_path / "fuentes" / "hotmail-carlos" / "cursor.json").is_file()
