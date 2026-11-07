"""Sign-in and the encrypted token cache -- Carlos only, device-code flow,
NOTHING a password ever passes through. `authority` is the `consumers`
endpoint, since Hotmail is a personal Microsoft account, never `common` or
a tenant GUID -- a work/school account could otherwise sign in here, which
this connector is not built to hold.

`construir_app` is the ONE seam: production passes the real
`msal.PublicClientApplication`; tests pass a fake with the same four calls
this module makes (`initiate_device_flow`, `acquire_token_by_device_flow`,
`get_accounts`, `acquire_token_silent`) and a `token_cache` carrying
`has_state_changed`/`serialize` -- never real MSAL reaching the network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from duna_buzon import cripto
from duna_buzon.config import Config

AUTORIDAD = "https://login.microsoftonline.com/consumers"
ALCANCES = ["Mail.Read", "offline_access"]

_NOMBRE_ARCHIVO_CACHE = "cache.bin"

_logger = logging.getLogger(__name__)


class SinCuentaError(Exception):
    """No signed-in account, or a refresh that failed: `run entrar`."""


class AplicacionMSAL(Protocol):
    """The four MSAL calls this module makes -- the seam tests inject a fake
    behind."""

    token_cache: Any

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]: ...

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> dict[str, Any]: ...

    def get_accounts(self) -> list[dict[str, Any]]: ...

    def acquire_token_silent(
        self, scopes: list[str], account: dict[str, Any]
    ) -> dict[str, Any] | None: ...


def _ruta_cache(config: Config) -> Path:
    return Path(config.directorio_datos) / _NOMBRE_ARCHIVO_CACHE


def _cargar_cache_serializada(config: Config) -> str | None:
    ruta = _ruta_cache(config)
    if not ruta.is_file():
        return None
    cifrado = ruta.read_bytes()
    if not cifrado:
        return None
    return cripto.descifrar(cifrado, config.clave_cifrado).decode("utf-8")


def _guardar_cache_si_cambio(config: Config, cache: Any) -> None:
    if not cache.has_state_changed:
        return
    cripto.asegurar_directorio_datos(Path(config.directorio_datos))
    serializado = cache.serialize().encode("utf-8")
    cripto.escribir_archivo_sensible(_ruta_cache(config), cripto.cifrar(serializado, config.clave_cifrado))


def _construir_app_msal_real(config: Config, cache: Any) -> AplicacionMSAL:
    import msal

    return msal.PublicClientApplication(
        config.graph_client_id, authority=AUTORIDAD, token_cache=cache
    )


def _nueva_cache_vacia() -> Any:
    import msal

    return msal.SerializableTokenCache()


def entrar(
    config: Config,
    *,
    construir_app: Callable[[Config, Any], AplicacionMSAL] = _construir_app_msal_real,
    nueva_cache: Callable[[], Any] = _nueva_cache_vacia,
    imprimir: Callable[[str], None] = print,
) -> None:
    """Device-code sign-in. Prints Microsoft's own URL and code for Carlos to
    complete in his own browser -- no password reaches this process."""
    cache = nueva_cache()
    serializado = _cargar_cache_serializada(config)
    if serializado is not None:
        cache.deserialize(serializado)

    app = construir_app(config, cache)
    flujo = app.initiate_device_flow(scopes=ALCANCES)
    if "user_code" not in flujo:
        raise RuntimeError("failed to start the device-code flow")
    imprimir(flujo["message"])

    resultado = app.acquire_token_by_device_flow(flujo)
    if "access_token" not in resultado:
        descripcion = resultado.get("error_description", "sign-in failed")
        raise RuntimeError(descripcion)

    _guardar_cache_si_cambio(config, cache)


def obtener_token(
    config: Config,
    *,
    construir_app: Callable[[Config, Any], AplicacionMSAL] = _construir_app_msal_real,
    nueva_cache: Callable[[], Any] = _nueva_cache_vacia,
) -> str:
    """`sincronizar`'s own entry point: silent acquisition ONLY -- never a
    flow that could prompt or touch a password. Raises `SinCuentaError` with
    "run entrar" when no account is cached or the refresh fails."""
    cache = nueva_cache()
    serializado = _cargar_cache_serializada(config)
    if serializado is None:
        raise SinCuentaError("no account is signed in: run entrar")
    cache.deserialize(serializado)

    app = construir_app(config, cache)
    cuentas = app.get_accounts()
    if not cuentas:
        raise SinCuentaError("no account is signed in: run entrar")

    resultado = app.acquire_token_silent(ALCANCES, account=cuentas[0])
    _guardar_cache_si_cambio(config, cache)
    if resultado is None or "access_token" not in resultado:
        raise SinCuentaError("token refresh failed: run entrar")
    return resultado["access_token"]


def cache_cargable(config: Config) -> bool:
    """`estado`'s own health signal: the cache file decrypts under the
    configured key. Says nothing about whether a usable account is inside."""
    try:
        _cargar_cache_serializada(config)
    except cripto.ClaveInvalidaError:
        return False
    return True
