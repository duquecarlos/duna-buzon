from __future__ import annotations

import base64

import pytest

from duna_buzon.config import Config, ConfigError


def test_configuracion_valida_se_construye(entorno_valido: dict[str, str]) -> None:
    config = Config.desde_entorno(entorno_valido)
    assert config.graph_client_id == entorno_valido["BUZON_GRAPH_CLIENT_ID"]
    assert config.finanzas_token == entorno_valido["FINANZAS_BUZON_TOKEN"]
    assert config.url_finanzas == "http://127.0.0.1:8010"


def test_variable_faltante_se_nombra_sin_valor(entorno_valido: dict[str, str]) -> None:
    del entorno_valido["BUZON_GRAPH_CLIENT_ID"]
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_GRAPH_CLIENT_ID" in str(excinfo.value)


@pytest.mark.parametrize(
    "variable",
    ["BUZON_GRAPH_CLIENT_ID", "BUZON_CLAVE_CIFRADO", "FINANZAS_BUZON_TOKEN"],
)
def test_cada_variable_requerida_se_nombra_al_faltar(
    entorno_valido: dict[str, str], variable: str
) -> None:
    del entorno_valido[variable]
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert variable in str(excinfo.value)
    # Never the value of any OTHER variable still present, and certainly
    # never a secret value of its own (there is none left to leak).
    assert entorno_valido.get("FINANZAS_BUZON_TOKEN", "") not in str(excinfo.value) or variable == (
        "FINANZAS_BUZON_TOKEN"
    )


def test_clave_cifrado_no_base64_es_rechazada(entorno_valido: dict[str, str]) -> None:
    entorno_valido["BUZON_CLAVE_CIFRADO"] = "no es base64 valido!!"
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_CLAVE_CIFRADO" in str(excinfo.value)


def test_clave_cifrado_de_longitud_incorrecta_es_rechazada(entorno_valido: dict[str, str]) -> None:
    entorno_valido["BUZON_CLAVE_CIFRADO"] = base64.b64encode(b"\x01" * 16).decode("ascii")
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_CLAVE_CIFRADO" in str(excinfo.value)


def test_directorio_datos_usa_el_valor_predeterminado(entorno_valido: dict[str, str]) -> None:
    del entorno_valido["BUZON_DATOS"]
    config = Config.desde_entorno(entorno_valido)
    assert config.directorio_datos == "/srv/duna/data/buzon"


def test_url_finanzas_usa_el_valor_predeterminado(entorno_valido: dict[str, str]) -> None:
    del entorno_valido["BUZON_FINANZAS_URL"]
    config = Config.desde_entorno(entorno_valido)
    assert config.url_finanzas == "http://127.0.0.1:8010"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8010",
        "https://127.0.0.1:8010",
        "http://10.0.0.5:8010",
        "ftp://127.0.0.1:8010",
    ],
)
def test_url_finanzas_no_loopback_es_rechazada(entorno_valido: dict[str, str], url: str) -> None:
    entorno_valido["BUZON_FINANZAS_URL"] = url
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_FINANZAS_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8010", "http://localhost:8010", "http://[::1]:8010"],
)
def test_url_finanzas_loopback_es_aceptada(entorno_valido: dict[str, str], url: str) -> None:
    entorno_valido["BUZON_FINANZAS_URL"] = url
    config = Config.desde_entorno(entorno_valido)
    assert config.url_finanzas == url


# ---------------------------------------------------------------------------
# Cuenta Duna -- optional as a GROUP (BUZON-FUENTES-PULL-1): absent entirely
# is valid (no Cuenta sources yet), but a half-set group is refused by name.
# ---------------------------------------------------------------------------


def test_cuenta_ausente_por_completo_es_valida(entorno_valido: dict[str, str]) -> None:
    config = Config.desde_entorno(entorno_valido)
    assert config.cuenta_configurada is False
    assert config.cuenta_url is None
    assert config.cuenta_client_id is None
    assert config.cuenta_client_secret is None
    assert config.cuenta_sub is None


def test_cuenta_completa_se_construye(entorno_valido: dict[str, str]) -> None:
    entorno_valido["BUZON_CUENTA_URL"] = "https://cuenta.duna.internal"
    entorno_valido["BUZON_CUENTA_CLIENT_ID"] = "buzon"
    entorno_valido["BUZON_CUENTA_CLIENT_SECRET"] = "secreto-cuenta"
    entorno_valido["BUZON_CUENTA_SUB"] = "sub-carlos"

    config = Config.desde_entorno(entorno_valido)
    assert config.cuenta_configurada is True
    assert config.cuenta_url == "https://cuenta.duna.internal"
    assert config.cuenta_client_id == "buzon"
    assert config.cuenta_client_secret == "secreto-cuenta"
    assert config.cuenta_sub == "sub-carlos"


@pytest.mark.parametrize(
    "variable_presente",
    ["BUZON_CUENTA_URL", "BUZON_CUENTA_CLIENT_ID", "BUZON_CUENTA_CLIENT_SECRET", "BUZON_CUENTA_SUB"],
)
def test_cuenta_a_medias_se_rechaza_nombrando_la_faltante(
    entorno_valido: dict[str, str], variable_presente: str
) -> None:
    entorno_valido[variable_presente] = "algo"
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_CUENTA_" in str(excinfo.value)
    # Never reveals the one value that WAS set, only names the missing var.
    assert "algo" not in str(excinfo.value)


@pytest.mark.parametrize("url", ["ftp://cuenta.duna.internal", "not-a-url", ""])
def test_cuenta_url_con_esquema_invalido_se_rechaza(entorno_valido: dict[str, str], url: str) -> None:
    entorno_valido["BUZON_CUENTA_URL"] = url
    entorno_valido["BUZON_CUENTA_CLIENT_ID"] = "buzon"
    entorno_valido["BUZON_CUENTA_CLIENT_SECRET"] = "secreto"
    entorno_valido["BUZON_CUENTA_SUB"] = "sub-carlos"
    with pytest.raises(ConfigError) as excinfo:
        Config.desde_entorno(entorno_valido)
    assert "BUZON_CUENTA_URL" in str(excinfo.value)


def test_cuenta_url_https_es_aceptada(entorno_valido: dict[str, str]) -> None:
    entorno_valido["BUZON_CUENTA_URL"] = "https://cuenta.duna.internal"
    entorno_valido["BUZON_CUENTA_CLIENT_ID"] = "buzon"
    entorno_valido["BUZON_CUENTA_CLIENT_SECRET"] = "secreto"
    entorno_valido["BUZON_CUENTA_SUB"] = "sub-carlos"
    config = Config.desde_entorno(entorno_valido)
    assert config.cuenta_url == "https://cuenta.duna.internal"
