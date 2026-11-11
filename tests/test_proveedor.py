from __future__ import annotations

import pytest

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


def test_proveedor_desconocido_se_rechaza() -> None:
    with pytest.raises(ProveedorDesconocidoError):
        construir_proveedor(_fuente(proveedor="imap"), "un-token")
