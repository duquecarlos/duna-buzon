from __future__ import annotations

import base64
import datetime

import httpx
import pytest

from duna_buzon import temporizadores
from duna_buzon.graph import (
    AdjuntoMetadata,
    GraphAutenticacionError,
    GraphClient,
    GraphErrorTransitorio,
)

_DESDE = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)


def _mensaje_json(
    id_: str,
    *,
    remitente: str = "a@b.com",
    asunto: str | None = "hola",
    recibido: str = "2026-03-01T10:00:00Z",
) -> dict:
    return {
        "id": id_,
        "internetMessageId": f"<{id_}@mail.example>",
        "from": {"emailAddress": {"address": remitente}},
        "subject": asunto,
        "receivedDateTime": recibido,
        "hasAttachments": False,
    }


def _cliente_con(handler) -> GraphClient:
    transporte = httpx.MockTransport(handler)
    return GraphClient("token-falso", transport=transporte)


def _metadato(
    *, id: str = "a1", nombre: str = "factura.pdf", tamano: int = 0, es_en_linea: bool = False,
    tipo_odata: str = "#microsoft.graph.fileAttachment",
) -> AdjuntoMetadata:
    return AdjuntoMetadata(
        id=id, nombre=nombre, tamano=tamano, es_en_linea=es_en_linea, tipo_odata=tipo_odata
    )


# ---------------------------------------------------------------------------
# GET-only: no method this client ever sends is anything but GET.
# ---------------------------------------------------------------------------


def test_todas_las_llamadas_a_graph_son_get() -> None:
    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request.method)
        assert request.method == "GET", f"non-GET call made to Graph: {request.method} {request.url}"
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            return httpx.Response(200, json={"contentBytes": ""})
        if segmentos[-1] == "attachments":
            return httpx.Response(200, json={"value": []})
        if segmentos[-1] != "messages":
            return httpx.Response(200, json={"body": {"content": "<p>hola</p>"}})
        return httpx.Response(200, json={"value": [_mensaje_json("m1")]})

    cliente = _cliente_con(handler)
    list(cliente.listar_mensajes(_DESDE))
    cliente.listar_adjuntos_metadata("m1")
    cliente.obtener_mensaje_completo("m1", [_metadato()])
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
    with pytest.raises(GraphAutenticacionError) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.paso == "listar"
    assert excinfo.value.motivo == "graph listar http=401"


def test_graph_500_lanza_error_transitorio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.motivo == "graph listar http=500"


def test_error_de_conexion_lanza_error_transitorio() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        raise httpx.ConnectError("no se pudo conectar")

    transporte = httpx.MockTransport(handler)
    dormidas: list[float] = []
    cliente = GraphClient("token-falso", transport=transporte, dormir=dormidas.append)
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()
    assert excinfo.value.motivo == "graph listar error=ConnectError"
    # BUZON-GRAPH-TIMEOUT-1 -- a transport failure is retried twice (three
    # attempts total) before this raises.
    assert llamadas == 3
    assert dormidas == [1.0, 4.0]
    assert cliente.reintentos_usados == 2


def test_graph_error_en_adjuntos_nombra_el_paso() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        cliente.listar_adjuntos_metadata("m1")
    cliente.cerrar()
    assert excinfo.value.motivo == "graph adjuntos http=500"


def test_graph_error_en_mensaje_completo_nombra_el_paso() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        cliente.obtener_mensaje_completo("m1", [])
    cliente.cerrar()
    assert excinfo.value.motivo == "graph mensaje http=403"


def test_graph_error_en_adjunto_individual_nombra_el_paso() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"body": {"content": "<p>hola</p>"}})

    cliente = _cliente_con(handler)
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        cliente.obtener_mensaje_completo("m1", [_metadato()])
    cliente.cerrar()
    assert excinfo.value.motivo == "graph adjunto http=500"


# ---------------------------------------------------------------------------
# Attachment metadata (never content) and the full message (body + content)
# ---------------------------------------------------------------------------


def test_listar_adjuntos_metadata_no_trae_contenido() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "contentBytes" not in str(request.url)
        assert request.url.params.get("$select") == "name,size,isInline"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "a1",
                        "name": "factura.pdf",
                        "size": 123,
                        "isInline": False,
                        "@odata.type": "#microsoft.graph.fileAttachment",
                    }
                ]
            },
        )

    cliente = _cliente_con(handler)
    metadatos = cliente.listar_adjuntos_metadata("m1")
    cliente.cerrar()

    assert len(metadatos) == 1
    assert metadatos[0].nombre == "factura.pdf"
    assert metadatos[0].tamano == 123
    assert metadatos[0].es_en_linea is False
    assert metadatos[0].tipo_odata == "#microsoft.graph.fileAttachment"


def test_obtener_mensaje_completo_pide_el_cuerpo_sin_expand() -> None:
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        return httpx.Response(200, json={"body": {"content": "<p>hola</p>"}})

    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1", [])
    cliente.cerrar()

    assert len(capturadas) == 1
    peticion = capturadas[0]
    assert peticion.url.path.endswith("/me/messages/m1")
    assert peticion.url.params.get("$select") == "body"
    assert "$expand" not in peticion.url.params
    assert completo.cuerpo_html == "<p>hola</p>"
    assert completo.adjuntos == ()
    assert completo.adjuntos_descartados == 0


def test_obtener_mensaje_completo_pide_cada_adjunto_no_en_linea_por_separado() -> None:
    contenido_a = base64.b64encode(b"%PDF-a").decode("ascii")
    contenido_b = base64.b64encode(b"%PDF-b").decode("ascii")
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            contenido = contenido_a if segmentos[-1] == "a1" else contenido_b
            return httpx.Response(200, json={"contentBytes": contenido})
        return httpx.Response(200, json={"body": {"content": "<p>cuerpo</p>"}})

    metadatos = [
        _metadato(id="a1", nombre="uno.pdf"),
        _metadato(id="a2", nombre="dos.pdf"),
    ]
    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    peticiones_adjunto = [p for p in capturadas if p.url.path.endswith("/attachments/a1")
                          or p.url.path.endswith("/attachments/a2")]
    assert len(peticiones_adjunto) == 2
    for peticion in peticiones_adjunto:
        # No `$select` at all on the per-attachment fetch -- the derived
        # type's own `contentBytes` is read from the whole response.
        assert dict(peticion.url.params) == {}
        assert peticion.url.path.endswith("/me/messages/m1/attachments/a1") or peticion.url.path.endswith(
            "/me/messages/m1/attachments/a2"
        )

    assert {a.nombre: a.contenido for a in completo.adjuntos} == {
        "uno.pdf": b"%PDF-a",
        "dos.pdf": b"%PDF-b",
    }
    assert completo.adjuntos_descartados == 0


def test_obtener_mensaje_completo_omite_adjuntos_en_linea_sin_pedirlos() -> None:
    llamadas_a_adjunto: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            llamadas_a_adjunto.append(request)
            return httpx.Response(200, json={"contentBytes": "deberia-no-pedirse"})
        return httpx.Response(200, json={"body": {"content": "<p>cuerpo</p>"}})

    metadatos = [_metadato(id="a1", nombre="en-linea.png", es_en_linea=True)]
    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    assert not llamadas_a_adjunto
    assert completo.adjuntos == ()
    assert completo.adjuntos_descartados == 0


@pytest.mark.parametrize(
    "tipo_odata", ["#microsoft.graph.itemAttachment", "#microsoft.graph.referenceAttachment"]
)
def test_obtener_mensaje_completo_descarta_adjuntos_que_no_son_fileattachment(tipo_odata: str) -> None:
    llamadas_a_adjunto: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            llamadas_a_adjunto.append(request)
            return httpx.Response(200, json={"contentBytes": "deberia-no-pedirse"})
        return httpx.Response(200, json={"body": {"content": "<p>cuerpo</p>"}})

    metadatos = [_metadato(id="a1", nombre="otro.msg", tipo_odata=tipo_odata)]
    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    assert not llamadas_a_adjunto
    assert completo.adjuntos == ()
    assert completo.adjuntos_descartados == 1


def test_obtener_mensaje_completo_mezcla_incluidos_en_linea_y_descartados() -> None:
    contenido = base64.b64encode(b"%PDF-contenido").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            return httpx.Response(200, json={"contentBytes": contenido})
        return httpx.Response(200, json={"body": {"content": "<p>cuerpo html</p>"}})

    metadatos = [
        _metadato(id="a1", nombre="en-linea.png", es_en_linea=True),
        _metadato(id="a2", nombre="factura.pdf", es_en_linea=False),
        _metadato(id="a3", nombre="algo.msg", tipo_odata="#microsoft.graph.itemAttachment"),
    ]
    cliente = _cliente_con(handler)
    completo = cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    assert completo.cuerpo_html == "<p>cuerpo html</p>"
    assert len(completo.adjuntos) == 1
    assert completo.adjuntos[0].nombre == "factura.pdf"
    assert completo.adjuntos[0].contenido == b"%PDF-contenido"
    assert completo.adjuntos_descartados == 1


# ---------------------------------------------------------------------------
# Guard: no request this client builds ever names `contentBytes` inside a
# `$select` or `$expand` -- that is exactly the shape that produced the real
# 400 this slice fixes (`contentBytes` only exists on the derived
# `fileAttachment` type, not on the base `attachment` type `$expand` selects
# over).
# ---------------------------------------------------------------------------


def test_ningun_request_pone_contentbytes_en_select_ni_expand() -> None:
    contenido = base64.b64encode(b"%PDF-contenido").decode("ascii")
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            return httpx.Response(200, json={"contentBytes": contenido})
        if segmentos[-1] == "attachments":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "a1",
                            "name": "factura.pdf",
                            "size": 14,
                            "isInline": False,
                            "@odata.type": "#microsoft.graph.fileAttachment",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"body": {"content": "<p>cuerpo</p>"}})

    cliente = _cliente_con(handler)
    metadatos = cliente.listar_adjuntos_metadata("m1")
    cliente.obtener_mensaje_completo("m1", metadatos)
    cliente.cerrar()

    assert capturadas
    for peticion in capturadas:
        seleccion = peticion.url.params.get("$select", "")
        expansion = peticion.url.params.get("$expand", "")
        assert "contentBytes" not in seleccion
        assert "contentBytes" not in expansion
        assert "$expand" not in peticion.url.params


# ---------------------------------------------------------------------------
# The real bug this slice fixes: `contentBytes` inside `$expand` over the
# base `attachment` type is what Graph answers 400 to. This fake reproduces
# that exact rejection, so it would catch a regression back to the old
# query shape.
# ---------------------------------------------------------------------------


def _handler_realista_que_rechaza_la_forma_vieja(request: httpx.Request) -> httpx.Response:
    segmentos = [s for s in request.url.path.split("/") if s]
    expansion = request.url.params.get("$expand", "")
    if "contentBytes" in expansion:
        return httpx.Response(400, json={"error": {"code": "BadRequest"}})
    if segmentos[-2] == "attachments":
        return httpx.Response(200, json={"contentBytes": base64.b64encode(b"ok").decode("ascii")})
    if segmentos[-1] == "attachments":
        return httpx.Response(200, json={"value": []})
    return httpx.Response(200, json={"body": {"content": "<p>cuerpo</p>"}})


def test_la_forma_vieja_de_la_peticion_recibe_400_en_el_fake_realista() -> None:
    cliente = _cliente_con(_handler_realista_que_rechaza_la_forma_vieja)
    # Builds the exact OLD request shape by hand (this client no longer
    # produces it): `$expand` naming `contentBytes` over the base
    # `attachment` type.
    respuesta = cliente._cliente.get(
        "/me/messages/m1",
        params={"$select": "body", "$expand": "attachments($select=name,contentBytes,isInline)"},
    )
    cliente.cerrar()
    assert respuesta.status_code == 400


def test_obtener_mensaje_completo_funciona_contra_el_fake_realista() -> None:
    cliente = _cliente_con(_handler_realista_que_rechaza_la_forma_vieja)
    completo = cliente.obtener_mensaje_completo("m1", [])
    cliente.cerrar()
    assert completo.cuerpo_html == "<p>cuerpo</p>"


# ---------------------------------------------------------------------------
# BUZON-GRAPH-TIMEOUT-1 -- explicit timeouts, and retrying the transient
# class only (a transport failure, or 429/503) inside the same pass.
# ---------------------------------------------------------------------------


def test_el_cliente_usa_el_timeout_explicito_compartido() -> None:
    cliente = _cliente_con(lambda request: httpx.Response(200, json={"value": []}))
    try:
        assert cliente._cliente.timeout == temporizadores.TIMEOUT_HTTPX
    finally:
        cliente.cerrar()


def test_timeout_se_reintenta_y_despues_tiene_exito() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json={"value": [_mensaje_json("m1")]})

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1"]
    assert llamadas == 2
    assert dormidas == [1.0]
    assert cliente.reintentos_usados == 1


def test_timeout_persistente_x3_agota_los_reintentos_y_lanza() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        raise httpx.ReadTimeout("timeout")

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    # The original attempt plus the two allowed retries -- never a fourth.
    assert llamadas == 3
    assert dormidas == [1.0, 4.0]
    assert cliente.reintentos_usados == 2
    assert excinfo.value.motivo == "graph listar error=ReadTimeout"


def test_429_respeta_retry_after_acotado_a_30s() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return httpx.Response(429, headers={"Retry-After": "90"})
        return httpx.Response(200, json={"value": []})

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert llamadas == 2
    # The server asked for 90s; this client never waits more than 30.
    assert dormidas == [30.0]
    assert cliente.reintentos_usados == 1


def test_503_sin_retry_after_usa_el_backoff_por_defecto() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return httpx.Response(503, text="no disponible")
        return httpx.Response(200, json={"value": []})

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert llamadas == 2
    assert dormidas == [1.0]


def test_500_se_reintenta_igual_que_503() -> None:
    """BUZON-GRAPH-502-1: 500 joined the retried class alongside 429/503 --
    before this slice it was never retried at all."""
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"value": []})

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert llamadas == 2
    assert dormidas == [1.0]


# ---------------------------------------------------------------------------
# BUZON-GRAPH-502-1 -- 500/502/504 join the retried class, and a LIST page
# that keeps failing even after its retry budget is spent degrades its own
# `$top` (50 -> 10 -> 1) before giving up.
# ---------------------------------------------------------------------------


def test_502_se_reintenta_y_tiene_exito() -> None:
    llamadas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"value": [_mensaje_json("m1")]})

    dormidas: list[float] = []
    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=dormidas.append
    )
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1"]
    assert llamadas == 2
    assert dormidas == [1.0]
    assert cliente.top_lista_usado == 50


def test_502_persistente_degrada_a_top_10_y_tiene_exito() -> None:
    tops_vistos: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        top = request.url.params.get("$top")
        tops_vistos.append(top)
        if top == "10":
            return httpx.Response(200, json={"value": [_mensaje_json("m1")]})
        return httpx.Response(502, text="bad gateway")

    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=lambda segundos: None
    )
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1"]
    # $top=50 tried three times (the original attempt plus its two
    # retries), all 502, then the single degraded attempt at $top=10
    # succeeds -- $top=1 is never even tried.
    assert tops_vistos == ["50", "50", "50", "10"]
    assert cliente.top_lista_usado == 10


def test_502_persistente_en_todo_tamano_se_rinde_y_registra_el_top_mas_chico() -> None:
    tops_vistos: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tops_vistos.append(request.url.params.get("$top"))
        return httpx.Response(502, text="bad gateway")

    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), dormir=lambda segundos: None
    )
    with pytest.raises(GraphErrorTransitorio) as excinfo:
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert tops_vistos == ["50", "50", "50", "10", "1"]
    assert excinfo.value.motivo == "graph listar http=502"
    assert cliente.top_lista_usado == 1


def test_carpetas_configuradas_lee_solo_esas_carpetas_en_orden_ascendente() -> None:
    peticiones: list[str] = []
    mensajes_por_carpeta = {
        "inbox": [_mensaje_json("m1", recibido="2026-03-01T09:00:00Z")],
        "archive": [_mensaje_json("m2", recibido="2026-03-01T11:00:00Z")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request.url.path)
        segmentos = [s for s in request.url.path.split("/") if s]
        carpeta = segmentos[-2]
        assert segmentos[-3] == "mailFolders"
        return httpx.Response(200, json={"value": mensajes_por_carpeta[carpeta]})

    cliente = GraphClient(
        "token-falso", transport=httpx.MockTransport(handler), carpetas=("inbox", "archive")
    )
    mensajes = list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert [m.id for m in mensajes] == ["m1", "m2"]  # no duplicate, folder order kept
    assert [m.recibido_en for m in mensajes] == sorted(m.recibido_en for m in mensajes)
    assert any(p.endswith("/me/mailFolders/inbox/messages") for p in peticiones)
    assert any(p.endswith("/me/mailFolders/archive/messages") for p in peticiones)
    assert not any(p.endswith("/me/messages") for p in peticiones)


def test_carpetas_no_configuradas_mantiene_la_peticion_de_hoy_byte_a_byte() -> None:
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        return httpx.Response(200, json={"value": []})

    cliente = GraphClient("token-falso", transport=httpx.MockTransport(handler))
    list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()

    assert len(capturadas) == 1
    peticion = capturadas[0]
    assert peticion.url.path.endswith("/me/messages")
    assert "mailFolders" not in peticion.url.path
    assert dict(peticion.url.params) == {
        "$filter": "receivedDateTime ge 2026-03-01T00:00:00Z",
        "$orderby": "receivedDateTime asc",
        "$select": "id,internetMessageId,from,subject,receivedDateTime,hasAttachments",
        "$top": "50",
    }
