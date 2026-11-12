"""The Cuenta Duna client -- ONE pull per sync run of this person's own
CONNECTED mailbox sources: `GET /servicio/fuentes/{sub}`, HTTP Basic client
auth (client id/secret for the `buzon` client), per
`CUENTA-API-FUENTES-CONECTADAS-1` (`duna-cuenta-api`'s own
`src/duna_cuenta/web/fuentes.py`, `domain/fuentes_conectadas.py:fuente_a_wire`,
`web/servicio.py`, restated verbatim in this slice's build instructions).

Returns, for every ACTIVE source, a fresh, short-lived provider access
token -- NEVER the refresh token, and this client never writes it anywhere:
`CuentaClient.listar_fuentes` returns it in memory only, for the one
`sincronizar` pass that pulled it (`cli.py`'s own job to never persist it).

A 404 (`cuenta_no_encontrada` -- an unknown or erased `sub`, or a caller
that is not the `buzon` client) or any transport failure is raised as
`CuentaPullError`, carrying only the HTTP status or the exception's class
name -- never content, mirroring `graph.GraphError`'s own shape. The caller
(`cli.py`) catches this, logs one count-only line, and keeps every local
source working exactly as before this slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

RUTA_SERVICIO_FUENTES = "/servicio/fuentes/{sub}"

ProveedorCuenta = Literal["google", "microsoft"]


class CuentaPullError(Exception):
    """Carries only the step (`fuentes`), the HTTP status if any, and --
    for a transport failure -- the exception's CLASS name, never its
    message. `cli.py` turns this into the one-line, count-only log a failed
    pull prints."""

    def __init__(self, *, http_status: int | None = None, clase_excepcion: str | None = None) -> None:
        self.http_status = http_status
        self.clase_excepcion = clase_excepcion
        super().__init__(self.motivo)

    @property
    def motivo(self) -> str:
        if self.http_status is not None:
            return f"cuenta fuentes http={self.http_status}"
        return f"cuenta fuentes error={self.clase_excepcion}"


@dataclass(frozen=True)
class FuenteCuenta:
    """One row of `GET /servicio/fuentes/{sub}`'s own `fuentes` array --
    ACTIVE sources only (Cuenta Duna never lists a revoked one), each with
    its own fresh provider token."""

    id: str
    proveedor: ProveedorCuenta
    direccion: str
    access_token: str
    expira_en_segundos: int


def _fuente_cuenta_desde_json(fila: dict) -> FuenteCuenta:
    return FuenteCuenta(
        id=str(fila["id"]),
        proveedor=fila["proveedor"],
        direccion=str(fila["direccion"]),
        access_token=str(fila["access_token"]),
        expira_en_segundos=int(fila["expira_en_segundos"]),
    )


class CuentaClient:
    def __init__(
        self,
        *,
        url_base: str,
        client_id: str,
        client_secret: str,
        sub: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._sub = sub
        self._cliente = httpx.Client(
            base_url=url_base, transport=transport, auth=(client_id, client_secret)
        )

    def cerrar(self) -> None:
        self._cliente.close()

    def listar_fuentes(self) -> list[FuenteCuenta]:
        try:
            respuesta = self._cliente.get(RUTA_SERVICIO_FUENTES.format(sub=self._sub))
        except httpx.TransportError as exc:
            raise CuentaPullError(clase_excepcion=type(exc).__name__) from exc
        if respuesta.status_code != 200:
            raise CuentaPullError(http_status=respuesta.status_code)
        cuerpo = respuesta.json()
        return [_fuente_cuenta_desde_json(fila) for fila in cuerpo.get("fuentes", [])]
