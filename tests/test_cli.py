from __future__ import annotations

import json
from pathlib import Path

import pytest

from duna_buzon import auth, cli, credencial_imap, cripto
from duna_buzon.config import Config
from duna_buzon.cuenta import CuentaPullError, FuenteCuenta
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


def _config_con_origen(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_ENVIAR_ORIGEN": "1",
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
    host: str | None = None,
    port: int | None = None,
    carpetas: tuple[str, ...] | None = None,
) -> Path:
    if proveedor == "imap":
        host = host or "imap.zoho.com"
        port = port or 993
    guardar_fuente(
        str(tmp_path),
        FuenteConfig(
            id=id,
            proveedor=proveedor,  # type: ignore[arg-type]
            direccion=direccion,
            etiqueta=etiqueta,
            token_finanzas_ref=token_finanzas_ref,
            activa=activa,
            host=host,
            port=port,
            carpetas=carpetas,
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


def test_entrar_sobre_fuente_imap_delega_en_credencial_imap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        host="imap.zoho.com",
        port=993,
    )
    llamado_con: list[tuple[Config, str, str, int, str]] = []

    def falso_entrar(cfg: Config, directorio_fuente: str, *, host: str, puerto: int, usuario: str) -> None:
        llamado_con.append((cfg, directorio_fuente, host, puerto, usuario))

    monkeypatch.setattr(credencial_imap, "entrar", falso_entrar)
    assert cli.comando_entrar(config, "legal-duna") == 0
    assert llamado_con == [(config, str(directorio), "imap.zoho.com", 993, "legal@duna.solutions")]


def test_entrar_sobre_fuente_imap_con_login_invalido_es_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        host="imap.zoho.com",
        port=993,
    )

    def falso_entrar(cfg: Config, directorio_fuente: str, *, host: str, puerto: int, usuario: str) -> None:
        raise credencial_imap.VerificacionLoginError("IMAP4.error")

    monkeypatch.setattr(credencial_imap, "entrar", falso_entrar)
    assert cli.comando_entrar(config, "legal-duna") == 1
    assert "imap login failed" in capsys.readouterr().err


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
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

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
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    resultado_falso = ResultadoPasada(contadores=Contadores(vistos=1))
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert salida.err == ""
    assert "parada=" not in salida.out


def test_sincronizar_sin_enviar_origen_no_pasa_etiqueta_ni_imprime_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1, off by default: no label is computed and
    nothing is printed about it."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] is None
    assert capsys.readouterr().err == ""


def test_sincronizar_con_enviar_origen_pasa_la_etiqueta_de_la_fuente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1: the label is this source's own
    `"<proveedor>:<id>"` -- here a Microsoft source, `microsoft:hotmail-
    carlos`."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_origen(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] == "microsoft:hotmail-carlos"
    assert capsys.readouterr().err == ""


def test_sincronizar_con_enviar_origen_etiqueta_imap_tiene_forma_proveedor_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_origen(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")
    _escribir_reglas_vacias(tmp_path / "fuentes" / "legal-duna")
    monkeypatch.setattr(credencial_imap, "obtener_credencial", lambda cfg, directorio: "credencial-falsa")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=0))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] == "imap:legal-duna"
    # Never a password, token or address in the label -- only the
    # provider and this connector's own source id.
    assert "legal@duna.solutions" not in capturado["etiqueta_origen"]
    assert "credencial-falsa" not in capturado["etiqueta_origen"]


def test_sincronizar_con_etiqueta_sobre_el_tope_la_omite_y_avisa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1's >64 fallback: only reachable here because this
    source was written directly (bypassing `agregar-fuente`'s own
    48-character guard), exactly like a Cuenta-pulled source would be."""
    from duna_buzon.limites import LIMITE_ORIGEN
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    id_fuente = "a" * (LIMITE_ORIGEN - len("imap:") + 1)
    assert len(f"imap:{id_fuente}") == LIMITE_ORIGEN + 1

    config = _config_con_origen(tmp_path)
    _escribir_fuente(tmp_path, id=id_fuente, proveedor="imap", direccion="legal@duna.solutions")
    _escribir_reglas_vacias(tmp_path / "fuentes" / id_fuente)
    monkeypatch.setattr(credencial_imap, "obtener_credencial", lambda cfg, directorio: "credencial-falsa")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=0))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] is None
    error = capsys.readouterr().err
    assert f"fuente={id_fuente}" in error
    assert str(LIMITE_ORIGEN) in error


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
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
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
        lambda fuente, token, cursor_proveedor_inicial=None: GraphClient(
            token, transport=httpx.MockTransport(graph_handler)
        ),
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
    _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", activa=False, host="imap.zoho.com", port=993
    )

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


def test_agregar_fuente_imap_escribe_fuente_json_sin_firmar(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 0
    )

    fuente = cargar_fuente(str(tmp_path), "legal-duna")
    assert fuente.proveedor == "imap"
    assert fuente.direccion == "legal@duna.solutions"
    assert fuente.host == "imap.zoho.com"
    assert fuente.port == 993
    assert fuente.token_finanzas_ref == "carlos"
    assert fuente.activa is True
    assert fuente.etiqueta == "legal@duna.solutions"
    # Never signs in -- no credential file exists yet.
    assert not (tmp_path / "fuentes" / "legal-duna" / "credencial.bin").exists()


def test_agregar_fuente_imap_con_carpetas_las_guarda(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
            carpetas=["Legal", "Compartidos/Facturas"],
        )
        == 0
    )

    fuente = cargar_fuente(str(tmp_path), "legal-duna")
    assert fuente.carpetas == ("Legal", "Compartidos/Facturas")


def test_agregar_fuente_con_id_repetido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap")

    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    assert "legal-duna" in capsys.readouterr().err


def test_agregar_fuente_id_en_el_tope_se_acepta(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    id_fuente = "a" * cli.LIMITE_ID_FUENTE_AGREGAR
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente=id_fuente,
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 0
    )
    assert cargar_fuente(str(tmp_path), id_fuente).id == id_fuente


def test_agregar_fuente_id_sobre_el_tope_se_rechaza(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    id_fuente = "a" * (cli.LIMITE_ID_FUENTE_AGREGAR + 1)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente=id_fuente,
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    error = capsys.readouterr().err
    assert str(cli.LIMITE_ID_FUENTE_AGREGAR) in error
    assert not (tmp_path / "fuentes" / id_fuente).exists()


def test_agregar_fuente_proveedor_no_imap_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="microsoft",
            id_fuente="otra",
            direccion="x@y.com",
            host="h",
            puerto=1,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    assert "microsoft" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `carpetas <id>` (BUZON-IMAP-CARPETAS-INCLUIDAS-1)
# ---------------------------------------------------------------------------


def test_comando_carpetas_sin_filtro_marca_todo_lo_no_excluido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions"
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )

    inbox = _CarpetaFalsa(nombre="INBOX", mensajes={})
    legal = _CarpetaFalsa(nombre="Legal", mensajes={})
    trash = _CarpetaFalsa(nombre="Trash", banderas=("\\Trash",), mensajes={})
    with _ServidorImapFalso(carpetas=[inbox, legal, trash]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert "carpeta='INBOX' banderas=- leida=si" in salida
    assert "carpeta='Legal' banderas=- leida=si" in salida
    assert "carpeta='Trash' banderas=\\Trash leida=no" in salida


def test_comando_carpetas_con_filtro_marca_solo_las_incluidas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        carpetas=("Legal",),
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )

    inbox = _CarpetaFalsa(nombre="INBOX", mensajes={})
    legal = _CarpetaFalsa(nombre="Legal", mensajes={})
    with _ServidorImapFalso(carpetas=[inbox, legal]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert "carpeta='INBOX' banderas=- leida=no" in salida
    assert "carpeta='Legal' banderas=- leida=si" in salida


def test_comando_carpetas_id_desconocido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_carpetas(config, "no-existe") == 1
    assert "no-existe" in capsys.readouterr().err


def test_comando_carpetas_fuente_no_imap_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    assert cli.comando_carpetas(config, "hotmail-carlos") == 1
    assert "not an imap source" in capsys.readouterr().err


def test_comando_carpetas_sin_credencial_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")
    assert cli.comando_carpetas(config, "legal-duna") == 1
    assert "entrar" in capsys.readouterr().err


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
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=5))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos vistos=5" in salida.out
    assert not (tmp_path / "cursor.json").exists()
    assert (tmp_path / "fuentes" / "hotmail-carlos" / "cursor.json").is_file()


# ---------------------------------------------------------------------------
# Cuenta Duna pull (BUZON-FUENTES-PULL-1)
# ---------------------------------------------------------------------------


def _config_con_cuenta(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_CUENTA_URL": "https://cuenta.duna.internal",
            "BUZON_CUENTA_CLIENT_ID": "buzon",
            "BUZON_CUENTA_CLIENT_SECRET": "secreto-cuenta",
            "BUZON_CUENTA_SUB": "sub-carlos",
        }
    )


def _escribir_reglas_vacias_en(directorio: Path) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
    )


class _CuentaClienteFalso:
    """Replaces `cli.CuentaClient` -- never touches the network. `resultado`
    is either a `list[FuenteCuenta]` (pull succeeds) or a `CuentaPullError`
    instance (pull fails)."""

    def __init__(self, resultado: list[FuenteCuenta] | CuentaPullError, **kwargs) -> None:
        self._resultado = resultado
        self.cerrado = False

    def listar_fuentes(self) -> list[FuenteCuenta]:
        if isinstance(self._resultado, CuentaPullError):
            raise self._resultado
        return self._resultado

    def cerrar(self) -> None:
        self.cerrado = True


def test_sin_cuenta_configurada_nunca_construye_un_cuenta_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)  # no BUZON_CUENTA_* set at all
    assert config.cuenta_configurada is False
    _escribir_fuente_y_reglas(tmp_path)

    def _nunca_llamar(*args, **kwargs):
        raise AssertionError("CuentaClient must never be constructed when Cuenta is not configured")

    monkeypatch.setattr(cli, "CuentaClient", _nunca_llamar)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0


def test_pull_exitoso_ejecuta_la_fuente_de_cuenta_con_el_token_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.fuentes import cargar_fuente
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-cuenta-secreto",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))

    tokens_vistos: list[str] = []

    def falso_construir_proveedor(fuente, token, cursor_proveedor_inicial=None):
        tokens_vistos.append(token)
        return _ProveedorFalso()

    monkeypatch.setattr(cli, "construir_proveedor", falso_construir_proveedor)
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=2))
    )

    def _nunca_auth_local(*args, **kwargs):
        raise AssertionError("a cuenta-origin source must never call auth.obtener_token")

    monkeypatch.setattr(auth, "obtener_token", _nunca_auth_local)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "fuente=cuenta-abc123 vistos=2" in salida.out
    assert tokens_vistos == ["token-cuenta-secreto"]

    fuente = cargar_fuente(str(tmp_path), "cuenta-abc123")
    assert fuente.proveedor == "google"
    assert fuente.direccion == "carlos@gmail.com"
    assert fuente.origen == "cuenta"
    assert fuente.activa is True

    # Never writes a credential, and never writes the pulled token to disk
    # anywhere -- not in `fuente.json`, not in a `credencial.bin`.
    directorio_cuenta = tmp_path / "fuentes" / "cuenta-abc123"
    assert not (directorio_cuenta / "credencial.bin").exists()
    assert "token-cuenta-secreto" not in (directorio_cuenta / "fuente.json").read_text(encoding="utf-8")


def test_pull_404_omite_fuentes_de_cuenta_pero_las_locales_siguen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)  # the usual LOCAL "hotmail-carlos" source

    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso(CuentaPullError(http_status=404)))
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=3))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "cuenta: cuenta fuentes http=404" in salida.err
    assert "fuente=hotmail-carlos vistos=3" in salida.out


def test_fuente_de_cuenta_revocada_se_marca_inactiva_y_deja_de_ejecutarse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.fuentes import cargar_fuente
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-1",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0
    assert cargar_fuente(str(tmp_path), "cuenta-abc123").activa is True
    capsys.readouterr()

    # Second pass: Cuenta Duna no longer returns this source (revoked).
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([]))
    llamadas_ejecutadas: list[str] = []
    monkeypatch.setattr(
        cli,
        "ejecutar_pasada",
        lambda **kwargs: (
            llamadas_ejecutadas.append("llamado") or ResultadoPasada(contadores=Contadores(vistos=1))
        ),
    )

    assert cli.comando_sincronizar(config) == 1  # no active sources left at all
    fuente_revocada = cargar_fuente(str(tmp_path), "cuenta-abc123")
    assert fuente_revocada.activa is False
    assert not llamadas_ejecutadas
    # The cursor/rules directory itself is untouched -- a reconnect can
    # resume it.
    assert (tmp_path / "fuentes" / "cuenta-abc123" / "reglas.json").is_file()


def test_fuente_de_cuenta_reconectada_reanuda_el_cursor_existente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime

    from duna_buzon import cursor as modulo_cursor
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    directorio_cuenta = tmp_path / "fuentes" / "cuenta-abc123"
    _escribir_reglas_vacias_en(directorio_cuenta)
    cursor_previo = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    modulo_cursor.guardar_cursor(str(directorio_cuenta), cursor_previo)

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-reconectado",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    cursores_vistos: list[datetime.datetime] = []

    def falso_ejecutar_pasada(*, cursor_inicial, **kwargs):
        cursores_vistos.append(cursor_inicial)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert cursores_vistos == [cursor_previo]
