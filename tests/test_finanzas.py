from __future__ import annotations

import datetime
import json

import httpx
import pytest

from duna_buzon import temporizadores
from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient, MetaEntrada

from .conftest import CLAVE_VALIDA_BASE64


def _config(tmp_path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "el-token-secreto",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


def _meta() -> MetaEntrada:
    return MetaEntrada(
        fuente="correo",
        id_externo="ext-1",
        remitente="banco@ejemplo.com",
        asunto="Alerta",
        recibido_en=datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC),
        cuerpo_texto="cuerpo",
    )


def _cliente_con(handler, tmp_path) -> FinanzasClient:
    return FinanzasClient(_config(tmp_path), transport=httpx.MockTransport(handler))


def test_meta_json_solo_lleva_las_seis_claves_del_contrato() -> None:
    datos = json.loads(_meta().como_json())
    assert set(datos) == {"fuente", "id_externo", "remitente", "asunto", "recibido_en", "cuerpo_texto"}


# ---------------------------------------------------------------------------
# `origen` (BUZON-ORIGEN-WIRE-1) -- optional seventh key, omitted entirely
# (never sent as JSON `null`) unless a caller passes a value.
# ---------------------------------------------------------------------------


def test_meta_sin_origen_omite_la_clave_por_completo() -> None:
    datos = json.loads(_meta().como_json())
    assert "origen" not in datos


def test_meta_con_origen_agrega_la_septima_clave() -> None:
    meta = MetaEntrada(
        fuente="correo",
        id_externo="ext-1",
        remitente="banco@ejemplo.com",
        asunto="Alerta",
        recibido_en=datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC),
        cuerpo_texto="cuerpo",
        origen="imap:legal-duna",
    )
    datos = json.loads(meta.como_json())
    assert set(datos) == {
        "fuente",
        "id_externo",
        "remitente",
        "asunto",
        "recibido_en",
        "cuerpo_texto",
        "origen",
    }
    assert datos["origen"] == "imap:legal-duna"


def test_autorizacion_bearer_y_meta_se_envian(tmp_path) -> None:
    capturada: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturada["auth"] = request.headers.get("authorization")
        capturada["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()

    assert capturada["auth"] == "Bearer el-token-secreto"
    assert "multipart/form-data" in capturada["content_type"]
    assert resultado.estado == "forwarded"
    assert resultado.id_entrada == "e1"


def test_201_es_forwarded(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "forwarded"
    assert resultado.codigo_http == 201


def test_200_es_duplicated(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "e1", "duplicada": True, "adjuntos_omitidos": 0})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "duplicated"


def test_401_es_unauthorized(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "unauthorized"


@pytest.mark.parametrize("codigo", [413, 422])
def test_413_422_son_rejected(tmp_path, codigo: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, json={"detail": "nope"})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "rejected"
    assert resultado.codigo_http == codigo


@pytest.mark.parametrize("codigo", [503, 500, 502])
def test_5xx_es_retry(tmp_path, codigo: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, text="boom")

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "retry"


@pytest.mark.parametrize("codigo", [408, 429])
def test_408_429_son_retry(tmp_path, codigo: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, text="boom")

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "retry"
    assert resultado.codigo_http == codigo


@pytest.mark.parametrize("codigo", [400, 403, 404, 405])
def test_4xx_no_transitorios_son_rechazo_permanente(tmp_path, codigo: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, json={"detail": "nope"})

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "rechazo_permanente"
    assert resultado.codigo_http == codigo


def test_error_de_conexion_es_retry(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente = _cliente_con(handler, tmp_path)
    resultado = cliente.enviar_entrada(_meta(), [])
    cliente.cerrar()
    assert resultado.estado == "retry"
    assert resultado.clase_excepcion == "ConnectError"


def test_el_cliente_usa_el_timeout_explicito_compartido(tmp_path) -> None:
    cliente = _cliente_con(lambda request: httpx.Response(201, json={}), tmp_path)
    try:
        assert cliente._cliente.timeout == temporizadores.TIMEOUT_HTTPX
    finally:
        cliente.cerrar()
