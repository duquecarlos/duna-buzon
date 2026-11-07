from __future__ import annotations

import base64
import datetime
from dataclasses import dataclass, field

import httpx

from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.graph import GraphClient
from duna_buzon.reglas import Reglas
from duna_buzon.sincronizar import ejecutar_pasada

from .conftest import CLAVE_VALIDA_BASE64


@dataclass
class MensajeFalso:
    id: str
    remitente: str = "alguien@otro-dominio.com"
    asunto: str | None = "hola"
    recibido_en: str = "2026-03-01T10:00:00Z"
    adjuntos: list[tuple[str, bytes]] = field(default_factory=list)


def _transporte_graph(mensajes: list[MensajeFalso]) -> httpx.MockTransport:
    por_id = {m.id: m for m in mensajes}

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        # segmentos is like ["v1.0", "me", "messages"] or [..., "messages",
        # "<id>"] or [..., "messages", "<id>", "attachments"].
        if segmentos[-1] == "attachments":
            id_mensaje = segmentos[-2]
            mensaje = por_id[id_mensaje]
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": f"{id_mensaje}-{i}", "name": nombre, "size": len(contenido)}
                        for i, (nombre, contenido) in enumerate(mensaje.adjuntos)
                    ]
                },
            )
        if segmentos[-1] != "messages":
            id_mensaje = segmentos[-1]
            mensaje = por_id[id_mensaje]
            return httpx.Response(
                200,
                json={
                    "body": {"content": f"<p>cuerpo de {id_mensaje}</p>"},
                    "attachments": [
                        {
                            "name": nombre,
                            "isInline": False,
                            "contentBytes": base64.b64encode(contenido).decode("ascii"),
                        }
                        for nombre, contenido in mensaje.adjuntos
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": m.id,
                        "internetMessageId": f"<{m.id}@mail.example>",
                        "from": {"emailAddress": {"address": m.remitente}},
                        "subject": m.asunto,
                        "receivedDateTime": m.recibido_en,
                        "hasAttachments": bool(m.adjuntos),
                    }
                    for m in mensajes
                ]
            },
        )

    return httpx.MockTransport(handler)


def _reglas() -> Reglas:
    return Reglas(
        desde=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        remitentes=("notificaciones@banco-ejemplo.com",),
        palabras=("factura",),
    )


def _config_finanzas(tmp_path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


def _cliente_finanzas(tmp_path, handler) -> FinanzasClient:
    return FinanzasClient(_config_finanzas(tmp_path), transport=httpx.MockTransport(handler))


_CURSOR_INICIAL = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)


def _finanzas_siempre_201(request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})


def test_mensaje_que_coincide_se_reenvia_y_avanza_el_cursor(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.contadores.vistos == 1
    assert resultado.contadores.reenviados == 1
    assert resultado.cursor_final == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)
    assert resultado.debe_persistir_cursor


def test_mensaje_sin_coincidencia_se_omite_sin_leer_cuerpo_ni_adjuntos(tmp_path) -> None:
    llamadas_de_cuerpo: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/me/messages/m1" in url and "attachments" not in url:
            llamadas_de_cuerpo.append(url)
        if "/me/messages/" in url:
            if "attachments" in url:
                return httpx.Response(200, json={"value": []})
            return httpx.Response(200, json={"body": {"content": "<p>nunca deberia leerse</p>"}, "attachments": []})
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "internetMessageId": "<m1@mail.example>",
                        "from": {"emailAddress": {"address": "alguien@otro-dominio.com"}},
                        "subject": "nada relevante",
                        "receivedDateTime": "2026-03-01T10:00:00Z",
                        "hasAttachments": False,
                    }
                ]
            },
        )

    cliente_graph = GraphClient("token", transport=httpx.MockTransport(handler))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.contadores.vistos == 1
    assert resultado.contadores.omitidos_por_reglas == 1
    assert resultado.contadores.reenviados == 0
    assert not llamadas_de_cuerpo
    assert resultado.cursor_final == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)


def test_401_de_finanzas_detiene_la_pasada_y_no_persiste_cursor(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_401)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.detenida_por_autenticacion
    assert not resultado.debe_persistir_cursor


def test_503_de_finanzas_detiene_la_pasada_y_no_persiste_cursor(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="no file store")

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_503)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.detenida_por_error_transitorio
    assert not resultado.debe_persistir_cursor


def test_graph_401_detiene_la_pasada(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    cliente_graph = GraphClient("token", transport=httpx.MockTransport(handler))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.detenida_por_autenticacion
    assert not resultado.debe_persistir_cursor


def test_413_o_422_se_omiten_como_defecto_permanente_y_el_cursor_avanza(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_422(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad meta"})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_422)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.contadores.omitidos_por_413_422 == 1
    assert resultado.debe_persistir_cursor
    assert resultado.cursor_final == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)


def test_reenvio_duplicado_por_el_solape_se_cuenta_como_duplicado(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_200_duplicada(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "e1", "duplicada": True, "adjuntos_omitidos": 0})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_200_duplicada)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.contadores.duplicados == 1
    assert resultado.contadores.reenviados == 0
    assert resultado.debe_persistir_cursor


def test_regla_c_con_adjunto_zip_se_reenvia_con_el_adjunto(tmp_path) -> None:
    mensajes = [
        MensajeFalso(
            id="m1",
            remitente="alguien@sin-relacion.com",
            asunto="sin relacion",
            adjuntos=[("factura.zip", b"PK\x03\x04contenido")],
        )
    ]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    capturado: dict[str, bytes] = {}

    def finanzas_capturar(request: httpx.Request) -> httpx.Response:
        capturado["cuerpo"] = request.read()
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_capturar)

    resultado = ejecutar_pasada(
        cliente_graph=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
    )

    assert resultado.contadores.reenviados == 1
    assert resultado.contadores.adjuntos_descartados == 0
    assert b"factura.zip" in capturado["cuerpo"]
