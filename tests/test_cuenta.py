from __future__ import annotations

import datetime

import httpx
import pytest

from duna_buzon import temporizadores
from duna_buzon.cuenta import (
    CuentaClient,
    CuentaPullError,
    FuenteCuenta,
    FuenteEstadoWire,
    enmascarar_direccion,
)


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


# ---------------------------------------------------------------------------
# `enviar_estado` (BUZON-ESTADO-A-CUENTA-1)
# ---------------------------------------------------------------------------


def test_enmascarar_direccion_sigue_el_ejemplo_del_contrato() -> None:
    assert enmascarar_direccion("carlos@hotmail.com") == "c***@hotmail.com"


def test_enmascarar_direccion_sin_arroba_no_lanza() -> None:
    assert enmascarar_direccion("no-es-un-correo") == "***"


def _fuente_wire(**overrides: object) -> FuenteEstadoWire:
    base: dict[str, object] = dict(
        fuente_id="hotmail-carlos",
        proveedor="microsoft",
        direccion="carlos@hotmail.com",
        etiqueta="Hotmail",
        origen="buzon",
        ultimo_paso_en="2026-11-26T10:00:00Z",
        vistos=12,
        reenviados=3,
        alerta=None,
    )
    base.update(overrides)
    return FuenteEstadoWire(**base)  # type: ignore[arg-type]


def test_enviar_estado_pega_al_route_y_sub_del_contrato() -> None:
    capturadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(request)
        return httpx.Response(204)

    cliente = _cliente_con(handler, sub="sub-carlos")
    cliente.enviar_estado(
        [_fuente_wire()], enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC)
    )
    cliente.cerrar()

    assert len(capturadas) == 1
    assert capturadas[0].method == "POST"
    assert capturadas[0].url.path == "/servicio/buzon/estado/sub-carlos"
    assert capturadas[0].headers["Authorization"].startswith("Basic ")


def test_enviar_estado_el_cuerpo_tiene_exactamente_los_campos_del_contrato() -> None:
    cuerpos: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        cuerpos.append(json.loads(request.content))
        return httpx.Response(204)

    cliente = _cliente_con(handler)
    cliente.enviar_estado(
        [
            _fuente_wire(
                fuente_id="cuenta-abc123",
                proveedor="google",
                direccion="carlos@gmail.com",
                etiqueta=None,
                origen="cuenta",
                vistos=5,
                reenviados=1,
                alerta="reingreso_requerido",
            )
        ],
        enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC),
    )
    cliente.cerrar()

    assert len(cuerpos) == 1
    cuerpo = cuerpos[0]
    assert set(cuerpo.keys()) == {"fuentes", "enviado_en"}
    assert cuerpo["enviado_en"] == "2026-11-26T10:05:00Z"
    assert len(cuerpo["fuentes"]) == 1
    fila = cuerpo["fuentes"][0]
    assert set(fila.keys()) == {
        "fuente_id",
        "proveedor",
        "direccion_enmascarada",
        "etiqueta",
        "origen",
        "ultimo_paso_en",
        "vistos",
        "reenviados",
        "alerta",
    }
    assert fila == {
        "fuente_id": "cuenta-abc123",
        "proveedor": "google",
        "direccion_enmascarada": "c***@gmail.com",
        "etiqueta": None,
        "origen": "cuenta",
        "ultimo_paso_en": "2026-11-26T10:00:00Z",
        "vistos": 5,
        "reenviados": 1,
        "alerta": "reingreso_requerido",
    }


def test_enviar_estado_nunca_manda_la_direccion_sin_enmascarar() -> None:
    cuerpos_crudos: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cuerpos_crudos.append(request.content)
        return httpx.Response(204)

    cliente = _cliente_con(handler)
    cliente.enviar_estado(
        [_fuente_wire(direccion="carlos@hotmail.com")],
        enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC),
    )
    cliente.cerrar()

    assert b"carlos@hotmail.com" not in cuerpos_crudos[0]
    assert b"c***@hotmail.com" in cuerpos_crudos[0]


def test_enviar_estado_204_no_lanza() -> None:
    cliente = _cliente_con(lambda request: httpx.Response(204))
    cliente.enviar_estado(
        [], enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC)
    )
    cliente.cerrar()


def test_enviar_estado_404_lanza_cuenta_pull_error_con_paso_estado() -> None:
    cliente = _cliente_con(lambda request: httpx.Response(404, json={"detail": "cuenta_no_encontrada"}))
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.enviar_estado(
            [], enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC)
        )
    cliente.cerrar()
    assert excinfo.value.motivo == "cuenta estado http=404"


def test_enviar_estado_error_de_transporte_lanza_cuenta_pull_error_con_paso_estado() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no se pudo conectar")

    cliente = _cliente_con(handler)
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.enviar_estado(
            [], enviado_en=datetime.datetime(2026, 11, 26, 10, 5, tzinfo=datetime.UTC)
        )
    cliente.cerrar()
    assert excinfo.value.motivo == "cuenta estado error=ConnectError"


def test_pull_de_fuentes_sigue_usando_el_paso_fuentes_por_defecto() -> None:
    """Backward compatibility: `listar_fuentes`'s own errors never changed
    shape when `enviar_estado` was added."""
    cliente = _cliente_con(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(CuentaPullError) as excinfo:
        cliente.listar_fuentes()
    cliente.cerrar()
    assert excinfo.value.paso == "fuentes"
    assert excinfo.value.motivo == "cuenta fuentes http=500"
