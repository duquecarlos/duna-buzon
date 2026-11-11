from __future__ import annotations

import pytest

from duna_buzon.cliente_imap import ClienteImap
from duna_buzon.fuentes import FuenteConfig
from duna_buzon.graph import GraphClient
from duna_buzon.proveedor import Proveedor, ProveedorDesconocidoError, construir_proveedor


def _fuente(**over) -> FuenteConfig:
    base = dict(
        id="hotmail-carlos",
        proveedor="microsoft",
        direccion="carlos@hotmail.com",
        etiqueta="Hotmail",
        token_finanzas_ref="carlos",
        activa=True,
        host=None,
        port=None,
    )
    base.update(over)
    return FuenteConfig(**base)


def test_microsoft_construye_un_graph_client() -> None:
    proveedor = construir_proveedor(_fuente(), "un-token")
    assert isinstance(proveedor, GraphClient)
    proveedor.cerrar()


def test_graph_client_satisface_el_protocolo_estructuralmente() -> None:
    proveedor = construir_proveedor(_fuente(), "un-token")
    assert isinstance(proveedor, Proveedor)
    proveedor.cerrar()


def test_imap_construye_un_cliente_imap() -> None:
    fuente = _fuente(
        id="legal-duna", proveedor="imap", direccion="legal@duna.solutions", host="imap.zoho.com", port=993
    )
    proveedor = construir_proveedor(fuente, "app-password")
    assert isinstance(proveedor, ClienteImap)
    # Lazy: building the client does no I/O at all, so closing it right
    # away (never connected) must be a safe no-op.
    proveedor.cerrar()


def test_cliente_imap_satisface_el_protocolo_estructuralmente() -> None:
    fuente = _fuente(
        id="legal-duna", proveedor="imap", direccion="legal@duna.solutions", host="imap.zoho.com", port=993
    )
    proveedor = construir_proveedor(fuente, "app-password")
    assert isinstance(proveedor, Proveedor)
    proveedor.cerrar()


def test_imap_lleva_el_cursor_del_proveedor_al_cliente() -> None:
    fuente = _fuente(
        id="legal-duna", proveedor="imap", direccion="legal@duna.solutions", host="imap.zoho.com", port=993
    )
    proveedor = construir_proveedor(fuente, "app-password", cursor_proveedor_inicial="123:45")
    assert isinstance(proveedor, ClienteImap)
    assert proveedor._cursor_inicial == "123:45"
    proveedor.cerrar()


def test_proveedor_desconocido_se_rechaza() -> None:
    with pytest.raises(ProveedorDesconocidoError):
        construir_proveedor(_fuente(proveedor="otro-no-implementado"), "un-token")
