from __future__ import annotations

import httpx
import pytest

from duna_buzon import temporizadores
from duna_buzon.cuenta import CuentaClient, CuentaPullError, FuenteCuenta


def _cliente_con(handler, **kwargs) -> CuentaClient:
    transporte = httpx.MockTransport(handler)
    return CuentaClient(
        url_base="https://cuenta.duna.internal",
        client_id=kwargs.get("client_id", "buzon"),
        client_secret=kwargs.get("client_secret", "secreto"),
        sub=kwargs.get("sub", "sub-carlos"),
        transport=transporte,
    )


def test_pull_exitoso_devuelve_las_fuentes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/servicio/fuentes/sub-carlos"
        return httpx.Response(
            200,
            json={
                "fuentes": [
                    {
                        "id": "abc123",
                        "proveedor": "google",
                        "direccion": "carlos@gmail.com",
                        "access_token": "token-fresco",
                        "expira_en_segundos": 3600,
                    },
                    {
                        "id": "def456",
                        "proveedor": "microsoft",
                        "direccion": "carlos@outlook.com",
                        "access_token": "otro-token",
                        "expira_en_segundos": 1800,
                    },
                ]
            },
        )

    cliente = _cliente_con(handler)
    fuentes = cliente.listar_fuentes()
    cliente.cerrar()

    assert fuentes == [
        FuenteCuenta(
            id="abc123",
            proveedor="google",
            direccion="carlos@gmail.com",
            access_token="token-fresco",
            expira_en_segundos=3600,
        ),
        FuenteCuenta(
            id="def456",
            proveedor="microsoft",
            direccion="carlos@outlook.com",
            access_token="otro-token",
            expira_en_segundos=1800,
        ),
    ]


def test_pull_lista_vacia_no_es_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"fuentes": []})

    cliente = _cliente_con(handler)
    assert cliente.listar_fuentes() == []
    cliente.cerrar()


def test_usa_basic_auth_con_el_client_id_y_secret() -> None:
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        return httpx.Response(200, json={"fuentes": []})

    cliente = _cliente_con(handler, client_id="buzon", client_secret="secreto-xyz")
    cliente.listar_fuentes()
    cliente.cerrar()

    assert len(capturadas) == 1
    assert "Authorization" in capturadas[0].headers
    assert capturadas[0].headers["Authorization"].startswith("Basic ")


def test_404_lanza_cuenta_pull_error_con_el_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "cuenta_no_encontrada"})

    cliente = _cliente_con(handler)
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.listar_fuentes()
    cliente.cerrar()
    assert excinfo.value.http_status == 404
    assert excinfo.value.motivo == "cuenta fuentes http=404"


def test_error_de_transporte_lanza_cuenta_pull_error_con_la_clase() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente = _cliente_con(handler)
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.listar_fuentes()
    cliente.cerrar()
    assert excinfo.value.clase_excepcion == "ConnectError"
    assert excinfo.value.motivo == "cuenta fuentes error=ConnectError"


def test_500_lanza_cuenta_pull_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cliente = _cliente_con(handler)
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.listar_fuentes()
    cliente.cerrar()
    assert excinfo.value.motivo == "cuenta fuentes http=500"


def test_el_cliente_usa_el_timeout_explicito_compartido() -> None:
    cliente = _cliente_con(lambda request: httpx.Response(200, json={"fuentes": []}))
    try:
        assert cliente._cliente.timeout == temporizadores.TIMEOUT_HTTPX
    finally:
        cliente.cerrar()
