"""Environment-only configuration. Every value comes from `os.environ`, never
from a config file or an argument -- a missing variable is refused by NAME,
never by printing what (if anything) was actually set for it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

_VAR_CLIENT_ID = "BUZON_GRAPH_CLIENT_ID"
_VAR_CLAVE_CIFRADO = "BUZON_CLAVE_CIFRADO"
_VAR_FINANZAS_TOKEN = "FINANZAS_BUZON_TOKEN"
_VAR_DATOS = "BUZON_DATOS"
_VAR_FINANZAS_URL = "BUZON_FINANZAS_URL"

# BUZON-ORIGEN-WIRE-1 -- OFF unless set to exactly "1" or "true": Finanzas'
# `origen` field (`EntradaBuzonMeta`, `extra="forbid"`) is not live until
# Finanzas Release 37 deploys, and sending it before then gets every entry
# refused with a 422. See README.md / deploy/host/README.md.
_VAR_ENVIAR_ORIGEN = "BUZON_ENVIAR_ORIGEN"
_VALORES_ENVIAR_ORIGEN_VERDADEROS = frozenset({"1", "true"})

# Cuenta Duna (CUENTA-API-FUENTES-CONECTADAS-1) -- OPTIONAL as a group: a
# deployment that has not yet connected any source through Cuenta Duna sets
# none of these four, and this connector works exactly as before, with only
# its local (`entrar`-signed) sources. The moment ANY one of them is set,
# all four are required -- a half-configured group is refused by name,
# never silently treated as "not configured" (that would hide a real
# operator mistake behind a connector that quietly never pulls anything).
_VAR_CUENTA_URL = "BUZON_CUENTA_URL"
_VAR_CUENTA_CLIENT_ID = "BUZON_CUENTA_CLIENT_ID"
_VAR_CUENTA_CLIENT_SECRET = "BUZON_CUENTA_CLIENT_SECRET"
_VAR_CUENTA_SUB = "BUZON_CUENTA_SUB"
_VARS_CUENTA = (_VAR_CUENTA_URL, _VAR_CUENTA_CLIENT_ID, _VAR_CUENTA_CLIENT_SECRET, _VAR_CUENTA_SUB)

_DATOS_PREDETERMINADO = "/srv/duna/data/buzon"
_FINANZAS_URL_PREDETERMINADA = "http://127.0.0.1:8010"

_LONGITUD_CLAVE_BYTES = 32

_NOMBRES_HOST_LOOPBACK = {"localhost"}


class ConfigError(Exception):
    """Raised with a message naming the missing or invalid variable, never
    its value."""


def _requerido(entorno: Mapping[str, str], nombre: str) -> str:
    valor = entorno.get(nombre)
    if valor is None or valor == "":
        raise ConfigError(f"missing required environment variable: {nombre}")
    return valor


def _decodificar_clave(valor_base64: str) -> bytes:
    import base64
    import binascii

    try:
        clave = base64.b64decode(valor_base64, validate=True)
    except binascii.Error as exc:
        raise ConfigError(
            f"{_VAR_CLAVE_CIFRADO} is not valid base64"
        ) from exc
    if len(clave) != _LONGITUD_CLAVE_BYTES:
        raise ConfigError(
            f"{_VAR_CLAVE_CIFRADO} must decode to exactly {_LONGITUD_CLAVE_BYTES} bytes"
        )
    return clave


def _es_host_loopback(host: str) -> bool:
    host_normalizado = host.strip("[]").lower()
    if host_normalizado in _NOMBRES_HOST_LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host_normalizado).is_loopback
    except ValueError:
        return False


def _validar_url_finanzas(url: str) -> str:
    partes = urlsplit(url)
    if partes.scheme != "http":
        raise ConfigError(
            f"{_VAR_FINANZAS_URL} must be an http:// URL on loopback, got scheme {partes.scheme!r}"
        )
    if not partes.hostname or not _es_host_loopback(partes.hostname):
        raise ConfigError(
            f"{_VAR_FINANZAS_URL} must point at loopback (127.0.0.1/::1/localhost), "
            f"got host {partes.hostname!r}"
        )
    return url


def _validar_url_cuenta(url: str) -> str:
    # Cuenta Duna is a separate service, not this connector's own loopback
    # peer -- no loopback requirement here, only a sane scheme. Unlike
    # Finanzas (invariant 3), nothing in this slice's build instructions
    # asked for this URL to be loopback-only.
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ConfigError(f"{_VAR_CUENTA_URL} must be an http:// or https:// URL, got {url!r}")
    return url


def _cuenta_desde_entorno(entorno: Mapping[str, str]) -> tuple[str | None, str | None, str | None, str | None]:
    valores = {nombre: entorno.get(nombre) or None for nombre in _VARS_CUENTA}
    presentes = {nombre: valor for nombre, valor in valores.items() if valor}
    if not presentes:
        return None, None, None, None
    if len(presentes) != len(_VARS_CUENTA):
        faltante = next(nombre for nombre in _VARS_CUENTA if not valores[nombre])
        raise ConfigError(f"missing required environment variable: {faltante}")
    return (
        _validar_url_cuenta(valores[_VAR_CUENTA_URL]),  # type: ignore[arg-type]
        valores[_VAR_CUENTA_CLIENT_ID],
        valores[_VAR_CUENTA_CLIENT_SECRET],
        valores[_VAR_CUENTA_SUB],
    )


@dataclass(frozen=True)
class Config:
    """Everything the connector reads from the environment."""

    graph_client_id: str
    clave_cifrado: bytes
    finanzas_token: str
    directorio_datos: str
    url_finanzas: str
    # Cuenta Duna -- all four `None` when not configured at all; see
    # `_VARS_CUENTA` above. `cuenta_configurada` is the one thing callers
    # should check before building a `CuentaClient`.
    cuenta_url: str | None = None
    cuenta_client_id: str | None = None
    cuenta_client_secret: str | None = None
    cuenta_sub: str | None = None
    # BUZON-ORIGEN-WIRE-1 -- see `_VAR_ENVIAR_ORIGEN` above. False for every
    # deployment that never set the variable, which is every deployment
    # before this slice.
    enviar_origen: bool = False

    @property
    def cuenta_configurada(self) -> bool:
        return self.cuenta_url is not None

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        graph_client_id = _requerido(entorno, _VAR_CLIENT_ID)
        clave_cifrado = _decodificar_clave(_requerido(entorno, _VAR_CLAVE_CIFRADO))
        finanzas_token = _requerido(entorno, _VAR_FINANZAS_TOKEN)
        directorio_datos = entorno.get(_VAR_DATOS, _DATOS_PREDETERMINADO)
        url_finanzas = _validar_url_finanzas(
            entorno.get(_VAR_FINANZAS_URL, _FINANZAS_URL_PREDETERMINADA)
        )
        cuenta_url, cuenta_client_id, cuenta_client_secret, cuenta_sub = _cuenta_desde_entorno(entorno)
        enviar_origen = entorno.get(_VAR_ENVIAR_ORIGEN) in _VALORES_ENVIAR_ORIGEN_VERDADEROS
        return cls(
            graph_client_id=graph_client_id,
            clave_cifrado=clave_cifrado,
            finanzas_token=finanzas_token,
            directorio_datos=directorio_datos,
            url_finanzas=url_finanzas,
            cuenta_url=cuenta_url,
            cuenta_client_id=cuenta_client_id,
            cuenta_client_secret=cuenta_client_secret,
            cuenta_sub=cuenta_sub,
            enviar_origen=enviar_origen,
        )
