from __future__ import annotations

import base64
import datetime
import json
import re
from dataclasses import dataclass, field

import httpx
import pytest

from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.graph import GraphClient
from duna_buzon.limites import LIMITE_ASUNTO, LIMITE_ID_EXTERNO, LIMITE_REMITENTE
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


def _meta_json_desde_multipart(cuerpo: bytes) -> dict:
    """Pulls the `meta` form part's JSON value back out of an encoded
    multipart body, so a test can assert on the exact value sent."""
    coincidencia = re.search(r'name="meta"\r\n\r\n(.*?)\r\n--', cuerpo.decode("utf-8"), re.DOTALL)
    assert coincidencia is not None, "no 'meta' part found in the multipart body"
    return json.loads(coincidencia.group(1))


def _transporte_graph(mensajes: list[MensajeFalso]) -> httpx.MockTransport:
    por_id = {m.id: m for m in mensajes}

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        # segmentos is like ["v1.0", "me", "messages"] or [..., "messages",
        # "<id>"] or [..., "messages", "<id>", "attachments"] or [...,
        # "messages", "<id>", "attachments", "<aid>"].
        if segmentos[-2] == "attachments":
            id_mensaje, id_adjunto = segmentos[-3], segmentos[-1]
            mensaje = por_id[id_mensaje]
            indice = int(id_adjunto.rsplit("-", 1)[-1])
            _, contenido = mensaje.adjuntos[indice]
            return httpx.Response(
                200, json={"contentBytes": base64.b64encode(contenido).decode("ascii")}
            )
        if segmentos[-1] == "attachments":
            id_mensaje = segmentos[-2]
            mensaje = por_id[id_mensaje]
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": f"{id_mensaje}-{i}",
                            "name": nombre,
                            "size": len(contenido),
                            "isInline": False,
                            "@odata.type": "#microsoft.graph.fileAttachment",
                        }
                        for i, (nombre, contenido) in enumerate(mensaje.adjuntos)
                    ]
                },
            )
        if segmentos[-1] != "messages":
            id_mensaje = segmentos[-1]
            return httpx.Response(
                200,
                json={"body": {"content": f"<p>cuerpo de {id_mensaje}</p>"}},
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
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
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
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
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
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_autenticacion
    assert not resultado.debe_persistir_cursor
    assert resultado.motivo == "finanzas enviar http=401"


def test_503_de_finanzas_detiene_la_pasada_y_no_persiste_cursor(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="no file store")

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_503)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_error_transitorio
    assert not resultado.debe_persistir_cursor
    assert resultado.motivo == "finanzas enviar http=503"


def test_conexion_rota_con_finanzas_detiene_la_pasada_con_la_clase_de_excepcion(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_rota(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_rota)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_error_transitorio
    assert not resultado.debe_persistir_cursor
    assert resultado.motivo == "finanzas enviar error=ConnectError"


@pytest.mark.parametrize("codigo", [400, 403, 404, 405])
def test_4xx_no_transitorio_de_finanzas_detiene_la_pasada_como_rechazo_permanente(
    tmp_path, codigo: int
) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_rechazo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, json={"detail": "nope"})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_rechazo)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_rechazo_permanente
    assert not resultado.detenida_por_error_transitorio
    assert not resultado.debe_persistir_cursor
    assert resultado.motivo == f"finanzas enviar http={codigo}"


def test_graph_401_detiene_la_pasada(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    cliente_graph = GraphClient("token", transport=httpx.MockTransport(handler))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_autenticacion
    assert not resultado.debe_persistir_cursor
    assert resultado.motivo == "graph listar http=401"


def test_graph_error_transitorio_nombra_el_paso_de_adjuntos(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-1] == "attachments":
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "internetMessageId": "<m1@mail.example>",
                        "from": {"emailAddress": {"address": "notificaciones@banco-ejemplo.com"}},
                        "subject": "hola",
                        "receivedDateTime": "2026-03-01T10:00:00Z",
                        "hasAttachments": True,
                    }
                ]
            },
        )

    cliente_graph = GraphClient("token", transport=httpx.MockTransport(handler))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.detenida_por_error_transitorio
    assert resultado.motivo == "graph adjuntos http=500"


def test_413_o_422_se_omiten_como_defecto_permanente_y_el_cursor_avanza(tmp_path) -> None:
    mensajes = [MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_422(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad meta"})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_422)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
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
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
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
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    assert resultado.contadores.adjuntos_descartados == 0
    assert b"factura.zip" in capturado["cuerpo"]


def _sufijo_internet_message_id() -> str:
    return "<@mail.example>"  # the template `_transporte_graph` fills with `m.id`


def test_id_externo_en_el_tope_se_reenvia_normalmente(tmp_path) -> None:
    id_mensaje = "m" * (LIMITE_ID_EXTERNO - len(_sufijo_internet_message_id()))
    assert len(f"<{id_mensaje}@mail.example>") == LIMITE_ID_EXTERNO

    mensajes = [MensajeFalso(id=id_mensaje, remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    assert resultado.contadores.omitidos_por_413_422 == 0


def test_id_externo_sobre_el_tope_se_omite_antes_de_publicar(tmp_path) -> None:
    id_mensaje = "m" * (LIMITE_ID_EXTERNO - len(_sufijo_internet_message_id()) + 1)
    assert len(f"<{id_mensaje}@mail.example>") == LIMITE_ID_EXTERNO + 1

    mensajes = [MensajeFalso(id=id_mensaje, remitente="notificaciones@banco-ejemplo.com")]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_que_no_debe_recibir_nada(request: httpx.Request) -> httpx.Response:
        raise AssertionError("un id_externo sobre el tope nunca debe publicarse")

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_que_no_debe_recibir_nada)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 0
    assert resultado.contadores.omitidos_por_413_422 == 1
    assert resultado.debe_persistir_cursor
    assert resultado.cursor_final == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)


def _reglas_dominio_grande() -> Reglas:
    return Reglas(
        desde=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        remitentes=("dominio-grande-de-ejemplo.com",),
        palabras=(),
    )


def test_remitente_en_el_tope_se_reenvia_normalmente(tmp_path) -> None:
    dominio = "@dominio-grande-de-ejemplo.com"
    remitente = "a" * (LIMITE_REMITENTE - len(dominio)) + dominio
    assert len(remitente) == LIMITE_REMITENTE

    mensajes = [MensajeFalso(id="m1", remitente=remitente)]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))
    cliente_finanzas = _cliente_finanzas(tmp_path, _finanzas_siempre_201)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas_dominio_grande(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    assert resultado.contadores.omitidos_por_413_422 == 0


def test_remitente_sobre_el_tope_se_omite_antes_de_publicar(tmp_path) -> None:
    dominio = "@dominio-grande-de-ejemplo.com"
    remitente = "a" * (LIMITE_REMITENTE - len(dominio) + 1) + dominio
    assert len(remitente) == LIMITE_REMITENTE + 1

    mensajes = [MensajeFalso(id="m1", remitente=remitente)]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    def finanzas_que_no_debe_recibir_nada(request: httpx.Request) -> httpx.Response:
        raise AssertionError("un remitente sobre el tope nunca debe publicarse")

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_que_no_debe_recibir_nada)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas_dominio_grande(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 0
    assert resultado.contadores.omitidos_por_413_422 == 1
    assert resultado.debe_persistir_cursor
    assert resultado.cursor_final == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)


def test_asunto_en_el_tope_se_envia_sin_truncar(tmp_path) -> None:
    asunto = "a" * LIMITE_ASUNTO
    mensajes = [
        MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com", asunto=asunto)
    ]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    capturado: dict[str, bytes] = {}

    def finanzas_capturar(request: httpx.Request) -> httpx.Response:
        capturado["cuerpo"] = request.read()
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_capturar)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    meta_enviada = _meta_json_desde_multipart(capturado["cuerpo"])
    assert meta_enviada["asunto"] == asunto
    assert len(meta_enviada["asunto"]) == LIMITE_ASUNTO


def test_asunto_sobre_el_tope_se_trunca_y_se_reenvia(tmp_path) -> None:
    asunto = "a" * (LIMITE_ASUNTO + 1)
    mensajes = [
        MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com", asunto=asunto)
    ]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    capturado: dict[str, bytes] = {}

    def finanzas_capturar(request: httpx.Request) -> httpx.Response:
        capturado["cuerpo"] = request.read()
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_capturar)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    assert resultado.contadores.omitidos_por_413_422 == 0
    meta_enviada = _meta_json_desde_multipart(capturado["cuerpo"])
    assert len(meta_enviada["asunto"]) == LIMITE_ASUNTO
    assert meta_enviada["asunto"] == "a" * LIMITE_ASUNTO


def test_asunto_multibyte_sobre_el_tope_se_trunca_sin_romper_caracteres(tmp_path) -> None:
    # "🙂" is one Python character but four UTF-8 bytes -- a byte-based
    # truncation at the cap would split it. Python string slicing never
    # does, since it counts characters.
    asunto = "é" * (LIMITE_ASUNTO - 1) + "🙂🙂"
    assert len(asunto) == LIMITE_ASUNTO + 1
    mensajes = [
        MensajeFalso(id="m1", remitente="notificaciones@banco-ejemplo.com", asunto=asunto)
    ]
    cliente_graph = GraphClient("token", transport=_transporte_graph(mensajes))

    capturado: dict[str, bytes] = {}

    def finanzas_capturar(request: httpx.Request) -> httpx.Response:
        capturado["cuerpo"] = request.read()
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente_finanzas = _cliente_finanzas(tmp_path, finanzas_capturar)

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=_reglas(),
        cursor_inicial=_CURSOR_INICIAL,
        etiqueta_fuente="microsoft:hotmail-carlos",
    )

    assert resultado.contadores.reenviados == 1
    meta_enviada = _meta_json_desde_multipart(capturado["cuerpo"])
    asunto_enviado = meta_enviada["asunto"]
    assert len(asunto_enviado) == LIMITE_ASUNTO
    assert asunto_enviado == "é" * (LIMITE_ASUNTO - 1) + "🙂"
    asunto_enviado.encode("utf-8")  # no character was split
