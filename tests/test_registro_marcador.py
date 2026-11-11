"""Logs say counts, never content: a full pass runs over a message whose
sender, subject, body and attachment filename all carry the SAME unique
marker, and that marker must appear in NEITHER captured stdout NOR any
captured log record."""

from __future__ import annotations

import base64
import datetime
import logging

import httpx
import pytest

from duna_buzon.config import Config
from duna_buzon.finanzas import FinanzasClient
from duna_buzon.graph import GraphClient
from duna_buzon.reglas import Reglas
from duna_buzon.registro import linea_resumen
from duna_buzon.sincronizar import ejecutar_pasada

from .conftest import CLAVE_VALIDA_BASE64

_MARCADOR = "MARCADOR-UNICO-7f3c9a21"


def _config(tmp_path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


def _transporte_graph_con_marcador() -> httpx.MockTransport:
    contenido_adjunto = f"PK\x03\x04{_MARCADOR}".encode("utf-8")
    nombre_adjunto = f"{_MARCADOR}.zip"

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        if segmentos[-2] == "attachments":
            return httpx.Response(
                200,
                json={"contentBytes": base64.b64encode(contenido_adjunto).decode("ascii")},
            )
        if segmentos[-1] == "attachments":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "a1",
                            "name": nombre_adjunto,
                            "size": len(contenido_adjunto),
                            "isInline": False,
                            "@odata.type": "#microsoft.graph.fileAttachment",
                        }
                    ]
                },
            )
        if segmentos[-1] != "messages":
            return httpx.Response(
                200,
                json={"body": {"content": f"<p>{_MARCADOR} cuerpo secreto</p>"}},
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "internetMessageId": f"<{_MARCADOR}@mail.example>",
                        "from": {"emailAddress": {"address": f"{_MARCADOR}@sin-relacion.com"}},
                        "subject": f"asunto {_MARCADOR}",
                        "receivedDateTime": "2026-03-01T10:00:00Z",
                        "hasAttachments": True,
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def test_el_marcador_no_aparece_en_stdout_ni_en_los_logs(tmp_path, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)

    cliente_graph = GraphClient("token", transport=_transporte_graph_con_marcador())

    def finanzas_201(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "e1", "duplicada": False, "adjuntos_omitidos": 0})

    cliente_finanzas = FinanzasClient(_config(tmp_path), transport=httpx.MockTransport(finanzas_201))

    reglas = Reglas(
        desde=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC), remitentes=(), palabras=()
    )

    resultado = ejecutar_pasada(
        proveedor=cliente_graph,
        cliente_finanzas=cliente_finanzas,
        reglas=reglas,
        cursor_inicial=datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC),
        etiqueta_fuente="microsoft:hotmail-carlos",
    )
    assert resultado.contadores.reenviados == 1  # rule (c): a ZIP attachment, on its own

    print(linea_resumen(resultado.contadores))

    salida = capsys.readouterr()
    assert _MARCADOR not in salida.out
    assert _MARCADOR not in salida.err
    for registro in caplog.records:
        assert _MARCADOR not in registro.getMessage()
