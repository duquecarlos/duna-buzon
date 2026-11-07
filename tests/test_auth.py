from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from duna_buzon import auth, cripto
from duna_buzon.config import Config

from .conftest import CLAVE_VALIDA_BASE64


def _config(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente-publico",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


class _CacheFalsa:
    """A stand-in for `msal.SerializableTokenCache`: enough to round-trip
    through `serialize`/`deserialize`, nothing that touches a real token
    store."""

    def __init__(self) -> None:
        self.has_state_changed = False
        self._contenido = "{}"

    def serialize(self) -> str:
        return self._contenido

    def deserialize(self, datos: str) -> None:
        self._contenido = datos


class _AppMSALFalsa:
    """The seam: implements exactly the four calls `auth.py` makes, never
    reaching MSAL's real HTTP client or the network."""

    def __init__(
        self,
        token_cache: _CacheFalsa,
        *,
        cuentas: list[dict[str, Any]] | None = None,
        resultado_device_flow: dict[str, Any] | None = None,
        resultado_silencioso: dict[str, Any] | None = None,
    ) -> None:
        self.token_cache = token_cache
        self._cuentas = cuentas or []
        self._resultado_device_flow = resultado_device_flow or {
            "access_token": "token-de-acceso-falso"
        }
        self._resultado_silencioso = resultado_silencioso

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]:
        assert scopes == auth.ALCANCES
        return {"user_code": "ABC123", "message": "go to https://microsoft.com/devicelogin and enter ABC123"}

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> dict[str, Any]:
        self.token_cache.has_state_changed = True
        self._cuentas = [{"username": "carlos@hotmail.com"}]
        return self._resultado_device_flow

    def get_accounts(self) -> list[dict[str, Any]]:
        return self._cuentas

    def acquire_token_silent(
        self, scopes: list[str], account: dict[str, Any]
    ) -> dict[str, Any] | None:
        assert scopes == auth.ALCANCES
        self.token_cache.has_state_changed = True
        return self._resultado_silencioso


def test_entrar_imprime_la_url_y_guarda_la_cache_cifrada(tmp_path: Path, no_es_windows: None) -> None:
    config = _config(tmp_path)
    mensajes: list[str] = []

    def construir_app(_config: Config, cache: _CacheFalsa) -> _AppMSALFalsa:
        return _AppMSALFalsa(cache)

    auth.entrar(
        config,
        construir_app=construir_app,
        nueva_cache=_CacheFalsa,
        imprimir=mensajes.append,
    )

    assert any("devicelogin" in m for m in mensajes)
    ruta_cache = Path(config.directorio_datos) / "cache.bin"
    assert ruta_cache.is_file()
    assert cripto.modo_octal(ruta_cache) == 0o600

    # Decrypts under the real key, to the same cache content.
    descifrado = cripto.descifrar(ruta_cache.read_bytes(), config.clave_cifrado)
    assert descifrado.decode("utf-8") == "{}"


def test_obtener_token_sin_cache_pide_entrar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(auth.SinCuentaError, match="entrar"):
        auth.obtener_token(config, construir_app=lambda c, cache: _AppMSALFalsa(cache))


def test_obtener_token_usa_silencioso_y_no_pide_contrasena(tmp_path: Path) -> None:
    config = _config(tmp_path)

    # Seed a cache as if `entrar` had already run.
    def construir_app_para_entrar(_config: Config, cache: _CacheFalsa) -> _AppMSALFalsa:
        return _AppMSALFalsa(cache)

    auth.entrar(config, construir_app=construir_app_para_entrar, nueva_cache=_CacheFalsa, imprimir=lambda m: None)

    def construir_app_silencioso(_config: Config, cache: _CacheFalsa) -> _AppMSALFalsa:
        return _AppMSALFalsa(
            cache,
            cuentas=[{"username": "carlos@hotmail.com"}],
            resultado_silencioso={"access_token": "token-renovado"},
        )

    token = auth.obtener_token(config, construir_app=construir_app_silencioso, nueva_cache=_CacheFalsa)
    assert token == "token-renovado"


def test_obtener_token_sin_cuentas_pide_entrar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    auth.entrar(
        config,
        construir_app=lambda c, cache: _AppMSALFalsa(cache),
        nueva_cache=_CacheFalsa,
        imprimir=lambda m: None,
    )

    def construir_app_sin_cuentas(_config: Config, cache: _CacheFalsa) -> _AppMSALFalsa:
        return _AppMSALFalsa(cache, cuentas=[])

    with pytest.raises(auth.SinCuentaError, match="entrar"):
        auth.obtener_token(config, construir_app=construir_app_sin_cuentas, nueva_cache=_CacheFalsa)


def test_obtener_token_sin_resultado_silencioso_pide_entrar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    auth.entrar(
        config,
        construir_app=lambda c, cache: _AppMSALFalsa(cache),
        nueva_cache=_CacheFalsa,
        imprimir=lambda m: None,
    )

    def construir_app_sin_refresh(_config: Config, cache: _CacheFalsa) -> _AppMSALFalsa:
        return _AppMSALFalsa(
            cache, cuentas=[{"username": "carlos@hotmail.com"}], resultado_silencioso=None
        )

    with pytest.raises(auth.SinCuentaError, match="entrar"):
        auth.obtener_token(config, construir_app=construir_app_sin_refresh, nueva_cache=_CacheFalsa)


def test_cache_cargable_con_clave_equivocada_es_falso(tmp_path: Path) -> None:
    config = _config(tmp_path)
    auth.entrar(
        config,
        construir_app=lambda c, cache: _AppMSALFalsa(cache),
        nueva_cache=_CacheFalsa,
        imprimir=lambda m: None,
    )

    otra_clave = bytes((b + 1) % 256 for b in config.clave_cifrado)
    config_con_otra_clave = Config(
        graph_client_id=config.graph_client_id,
        clave_cifrado=otra_clave,
        finanzas_token=config.finanzas_token,
        directorio_datos=config.directorio_datos,
        url_finanzas=config.url_finanzas,
    )
    assert auth.cache_cargable(config_con_otra_clave) is False
    assert auth.cache_cargable(config) is True


def test_cache_cargable_sin_archivo_es_verdadero(tmp_path: Path) -> None:
    # No cache yet is not itself a decrypt failure -- `estado`'s own health
    # check is "the file could be loaded", and an absent file loads as
    # empty, cleanly.
    config = _config(tmp_path)
    assert auth.cache_cargable(config) is True


def test_authority_es_consumers_no_common() -> None:
    assert auth.AUTORIDAD == "https://login.microsoftonline.com/consumers"


def test_alcances_son_exactamente_mail_read_y_offline_access() -> None:
    assert auth.ALCANCES == ["Mail.Read", "offline_access"]
