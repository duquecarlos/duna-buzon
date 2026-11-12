from __future__ import annotations

import base64
import datetime

import httpx
import pytest

from duna_buzon.gmail import AdjuntoMetadata, GmailAutenticacionError, GmailClient, GmailErrorTransitorio

_DESDE = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)


def _b64url(texto: bytes) -> str:
    return base64.urlsafe_b64encode(texto).decode("ascii").rstrip("=")


def _epoch_ms(momento: datetime.datetime) -> str:
    return str(int(momento.timestamp() * 1000))


def _mensaje_json(
    id_: str,
    *,
    remitente: str = "a@b.com",
    asunto: str | None = "hola",
    fecha: datetime.datetime = _DESDE + datetime.timedelta(hours=10),
    message_id: str | None = "<m1@mail.example>",
    payload: dict | None = None,
) -> dict:
    headers = [{"name": "From", "value": remitente}]
    if asunto is not None:
        headers.append({"name": "Subject", "value": asunto})
    if message_id is not None:
        headers.append({"name": "Message-ID", "value": message_id})
    cuerpo = dict(payload) if payload is not None else {
        "mimeType": "text/plain",
        "body": {"size": 5, "data": _b64url(b"hola")},
    }
    # Gmail always carries the message's own headers on the TOP-level
    # payload, multipart or not -- overwritten here regardless of what
    # `payload` passed in, since none of this file's test payloads set it.
    cuerpo["headers"] = headers
    return {"id": id_, "internalDate": _epoch_ms(fecha), "payload": cuerpo}


def _parte_adjunto(
    *, nombre: str = "factura.pdf", id_adjunto: str = "a1", tamano: int = 123, en_linea: bool = False
) -> dict:
    headers = [{"name": "Content-Disposition", "value": "inline" if en_linea else "attachment"}]
    return {
        "mimeType": "application/pdf",
        "filename": nombre,
        "headers": headers,
        "body": {"attachmentId": id_adjunto, "size": tamano},
    }


def _parte_cuerpo(tipo: str, texto: bytes) -> dict:
    return {"mimeType": tipo, "headers": [], "body": {"size": len(texto), "data": _b64url(texto)}}


def _cliente_con(handler) -> GmailClient:
    return GmailClient("token-falso", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# GET-only
# ---------------------------------------------------------------------------


def test_todas_las_llamadas_a_gmail_son_get() -> None:
    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request.method)
        assert request.method == "GET", f"non-GET call to Gmail: {request.method} {request.url}"
        if request.url.path.endswith("/attachments/a1"):
            return httpx.Response(200, json={"size": 3, "data": _b64url(b"abc")})
        if "/users/me/messages/" in request.url.path:
            return httpx.Response(
                200, json=_mensaje_json("m1", payload={"mimeType": "multipart/mixed", "parts": [_parte_adjunto()]})
            )
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    cliente.listar_adjuntos_metadata("m1")
    cliente.obtener_mensaje_completo("m1", [AdjuntoMetadata(id="a1", nombre="factura.pdf", tamano=123, es_en_linea=False, tipo_odata="application/pdf")])
    cliente.cerrar()

    assert llamadas
    assert all(m == "GET" for m in llamadas)


# ---------------------------------------------------------------------------
# Listing, paging, and the day-granularity filter
# ---------------------------------------------------------------------------


def test_listar_mensajes_simple_sin_adjuntos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/attachments/" in request.url.path:
            raise AssertionError("no attachment call expected")
        if request.url.path.endswith("/users/me/messages") and "pageToken" not in request.url.params:
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(200, json=_mensaje_json("m1", remitente="carlos@gmail.com", asunto="hola"))

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert len(mensajes) == 1
    assert mensajes[0].id == "m1"
    assert mensajes[0].remitente == "carlos@gmail.com"
    assert mensajes[0].asunto == "hola"
    assert mensajes[0].internet_message_id == "<m1@mail.example>"
    assert mensajes[0].tiene_adjuntos is False


def test_listar_sigue_el_nextpagetoken() -> None:
    llamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1"))
        if request.url.path.endswith("/users/me/messages/m2"):
            return httpx.Response(200, json=_mensaje_json("m2", message_id="<m2@mail.example>"))
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"messages": [{"id": "m1"}], "nextPageToken": "pagina-2"})
        return httpx.Response(200, json={"messages": [{"id": "m2"}]})

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1", "m2"]
    peticiones_lista = [p for p in llamadas if p.url.path.endswith("/users/me/messages")]
    assert len(peticiones_lista) == 2
    assert peticiones_lista[1].url.params.get("pageToken") == "pagina-2"
    # Every listing call still carries the same `q` -- Gmail's `pageToken`
    # does not stand in for it.
    assert peticiones_lista[0].url.params.get("q") == peticiones_lista[1].url.params.get("q")


def test_la_busqueda_usa_after_con_un_dia_de_holgura() -> None:
    capturada: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages"):
            capturada["q"] = request.url.params.get("q", "")
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(200, json={"messages": []})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    esperado = int((_DESDE - datetime.timedelta(days=1)).timestamp())
    assert capturada["q"] == f"after:{esperado}"


def test_mensaje_anterior_a_desde_se_filtra_en_python() -> None:
    """The day-granularity `after:` query can over-fetch; this client must
    still filter exactly, the same discipline `cliente_imap.py`'s own
    SINCE-date fallback uses."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/viejo"):
            return httpx.Response(200, json=_mensaje_json("viejo", fecha=_DESDE - datetime.timedelta(hours=1)))
        if request.url.path.endswith("/users/me/messages/nuevo"):
            return httpx.Response(200, json=_mensaje_json("nuevo", fecha=_DESDE + datetime.timedelta(hours=1)))
        return httpx.Response(200, json={"messages": [{"id": "viejo"}, {"id": "nuevo"}]})

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["nuevo"]


def test_listar_401_lanza_error_de_autenticacion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    cliente = _cliente_con(handler)
    with pytest.raises(GmailAutenticacionError) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.motivo == "gmail listar http=401"


def test_listar_500_lanza_error_transitorio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(GmailErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.motivo == "gmail listar http=500"


def test_error_de_conexion_lanza_error_transitorio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente = _cliente_con(handler)
    with pytest.raises(GmailErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.motivo == "gmail listar error=ConnectError"


# ---------------------------------------------------------------------------
# Message-ID fallback
# ---------------------------------------------------------------------------


def test_sin_message_id_usa_el_id_de_gmail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1", message_id=None))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert mensajes[0].internet_message_id == "m1"


# ---------------------------------------------------------------------------
# Attachments: detection, download, inline skip, caching
# ---------------------------------------------------------------------------


def test_adjunto_se_detecta_y_se_descarga() -> None:
    contenido = b"%PDF-contenido"
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [_parte_cuerpo("text/plain", b"cuerpo"), _parte_adjunto(nombre="factura.pdf", id_adjunto="a1")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1/attachments/a1"):
            return httpx.Response(200, json={"size": len(contenido), "data": _b64url(contenido)})
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1", payload=payload))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    mensajes = list(cliente.listar_mensajes(_DESDE))
    assert mensajes[0].tiene_adjuntos is True

    metadatos = cliente.listar_adjuntos_metadata("m1")
    assert len(metadatos) == 1
    assert metadatos[0].nombre == "factura.pdf"
    assert metadatos[0].es_en_linea is False
    assert metadatos[0].tamano == 123

    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()
    assert len(completo.adjuntos) == 1
    assert completo.adjuntos[0].nombre == "factura.pdf"
    assert completo.adjuntos[0].contenido == contenido
    assert completo.cuerpo_html == "cuerpo"


def test_adjunto_en_linea_no_se_descarga() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            _parte_cuerpo("text/html", b"<p>hola</p>"),
            _parte_adjunto(nombre="en-linea.png", id_adjunto="a1", en_linea=True),
        ],
    }
    llamadas_adjunto: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1/attachments/a1"):
            llamadas_adjunto.append(request)
            return httpx.Response(200, json={"size": 1, "data": _b64url(b"x")})
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1", payload=payload))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    metadatos = cliente.listar_adjuntos_metadata("m1")
    assert metadatos[0].es_en_linea is True

    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()
    assert not llamadas_adjunto
    assert completo.adjuntos == ()
    assert completo.cuerpo_html == "<p>hola</p>"


def test_cuerpo_html_preferido_sobre_texto_plano() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [_parte_cuerpo("text/plain", b"texto plano"), _parte_cuerpo("text/html", b"<p>html</p>")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1", payload=payload))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    completo = cliente.obtener_mensaje_completo("m1", [])
    cliente.cerrar()
    assert completo.cuerpo_html == "<p>html</p>"


def test_el_mensaje_completo_se_cachea_y_no_se_vuelve_a_pedir() -> None:
    llamadas_mensaje: list[httpx.Request] = []
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [_parte_cuerpo("text/plain", b"cuerpo"), _parte_adjunto()],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1/attachments/a1"):
            return httpx.Response(200, json={"size": 3, "data": _b64url(b"abc")})
        if request.url.path.endswith("/users/me/messages/m1"):
            llamadas_mensaje.append(request)
            return httpx.Response(200, json=_mensaje_json("m1", payload=payload))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    metadatos = cliente.listar_adjuntos_metadata("m1")
    cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    # Exactly ONE `format=full` fetch for m1, even though listing,
    # metadata and content were all read off it.
    assert len(llamadas_mensaje) == 1


def test_token_expirado_a_mitad_de_pasada_lanza_error_de_autenticacion() -> None:
    """A 401 while fetching attachment CONTENT (the pulled Cuenta Duna
    token expired mid-pass) stops the pass the same generic way every
    other provider error does -- no special handling needed."""
    payload = {"mimeType": "multipart/mixed", "parts": [_parte_adjunto()]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages/m1/attachments/a1"):
            return httpx.Response(401, json={"error": "token expired"})
        if request.url.path.endswith("/users/me/messages/m1"):
            return httpx.Response(200, json=_mensaje_json("m1", payload=payload))
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    metadatos = cliente.listar_adjuntos_metadata("m1")
    with pytest.raises(GmailAutenticacionError) as excinfo:
        cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()
    assert excinfo.value.motivo == "gmail adjunto http=401"
