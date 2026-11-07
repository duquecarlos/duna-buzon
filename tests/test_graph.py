from __future__ import annotations

import base64
import datetime
import json

import httpx
import pytest

from duna_buzon.graph import (
    GraphAutenticacionError,
    GraphClient,
    GraphErrorTransitorio,
)

_DESDE = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)


def _mensaje_json(id_: str, *, remitente: str = "a@b.com", asunto: str | None = "hola") -> dict:
    return {
        "id": id_,
        "internetMessageId": f"<{id_}@mail.example>",
        "from": {"emailAddress": {"address": remitente}},
        "subject": asunto,
        "receivedDateTime": "2026-03-01T10:00:00Z",
        "hasAttachments": False,
    }


def _cliente_con(handler) -> GraphClient:
    transporte = httpx.MockTransport(handler)
    return GraphClient("token-falso", transport=transporte)


# ---------------------------------------------------------------------------
# GET-only: no method this client ever sends is anything but GET.
# ---------------------------------------------------------------------------


def test_todas_las_llamadas_a_graph_son_get() -> None:
    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request.method)
        assert request.method == "GET", f"non-GET call made to Graph: {request.method} {request.url}"
        url_str = str(request.url)
        if "/attachments" in url_str:
            return httpx.Response(200, json={"value": []})
        if "/me/messages/m1" in url_str:
            return httpx.Response(200, json={"body": {"content": "<p>hola</p>"}, "attachments": []})
        return httpx.Response(200, json={"value": [_mensaje_json("m1")]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    cliente.listar_adjuntos_metadata("m1")
    cliente.obtener_mensaje_completo("m1")
    cliente.cerrar()

    assert llamadas
    assert all(metodo == "GET" for metodo in llamadas)


# ---------------------------------------------------------------------------
# Listing and pagination
# ---------------------------------------------------------------------------


def test_listar_mensajes_sigue_el_nextlink() -> None:
    paginas = [
        {
            "value": [_mensaje_json("m1")],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=1",
        },
        {"value": [_mensaje_json("m2")]},
    ]
    llamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json=paginas.pop(0))

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1", "m2"]
    assert len(llamadas) == 2
    assert llamadas[0].url.params.get("$filter") is not None
    # The second call hits the nextLink verbatim -- no $filter re-added.
    assert "skip=1" in str(llamadas[1].url)


def test_el_filtro_usa_la_fecha_desde_en_utc() -> None:
    capturada: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturada["filtro"] = request.url.params.get("$filter", "")
        return httpx.Response(200, json={"value": []})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert "2026-03-01T00:00:00Z" in capturada["filtro"]


def test_graph_401_lanza_error_de_autenticacion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    cliente = _cliente_con(handler)
    with pytest.raises(GraphAutenticacionError):
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()


def test_graph_500_lanza_error_transitorio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio):
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()


def test_error_de_conexion_lanza_error_transitorio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio):
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()


# ---------------------------------------------------------------------------
# Attachment metadata (never content) and the full message (body + content)
# ---------------------------------------------------------------------------


def test_listar_adjuntos_metadata_no_trae_contenido() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "contentBytes" not in str(request.url)
        return httpx.Response(
            200, json={"value": [{"id": "a1", "name": "factura.pdf", "size": 123}]}
        )

    cliente = _cliente_con(handler)
    metadatos = cliente.listar_adjuntos_metadata("m1")
    cliente.cerrar()

    assert len(metadatos) == 1
    assert metadatos[0].nombre == "factura.pdf"
    assert metadatos[0].tamano == 123


def test_obtener_mensaje_completo_decodifica_adjuntos_no_en_linea() -> None:
    contenido = base64.b64encode(b"%PDF-contenido").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "body": {"content": "<p>cuerpo html</p>"},
                "attachments": [
                    {"name": "en-linea.png", "contentBytes": contenido, "isInline": True},
                    {"name": "factura.pdf", "contentBytes": contenido, "isInline": False},
                ],
            },
        )

    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1")
    cliente.cerrar()

    assert completo.cuerpo_html == "<p>cuerpo html</p>"
    assert len(completo.adjuntos) == 1
    assert completo.adjuntos[0].nombre == "factura.pdf"
    assert completo.adjuntos[0].contenido == b"%PDF-contenido"
