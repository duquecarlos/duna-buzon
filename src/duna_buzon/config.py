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


@dataclass(frozen=True)
class Config:
    """Everything the connector reads from the environment."""

    graph_client_id: str
    clave_cifrado: bytes
    finanzas_token: str
    directorio_datos: str
    url_finanzas: str

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        graph_client_id = _requerido(entorno, _VAR_CLIENT_ID)
        clave_cifrado = _decodificar_clave(_requerido(entorno, _VAR_CLAVE_CIFRADO))
        finanzas_token = _requerido(entorno, _VAR_FINANZAS_TOKEN)
        directorio_datos = entorno.get(_VAR_DATOS, _DATOS_PREDETERMINADO)
        url_finanzas = _validar_url_finanzas(
            entorno.get(_VAR_FINANZAS_URL, _FINANZAS_URL_PREDETERMINADA)
        )
        return cls(
            graph_client_id=graph_client_id,
            clave_cifrado=clave_cifrado,
            finanzas_token=finanzas_token,
            directorio_datos=directorio_datos,
            url_finanzas=url_finanzas,
        )
