"""The Cuenta Duna client. Two calls, both HTTP Basic client auth (the
`buzon` client's own id/secret):

- `listar_fuentes` -- ONE pull per sync run of this person's own CONNECTED
  mailbox sources: `GET /servicio/fuentes/{sub}`, per
  `CUENTA-API-FUENTES-CONECTADAS-1` (`duna-cuenta-api`'s own
  `src/duna_cuenta/web/fuentes.py`, `domain/fuentes_conectadas.py:fuente_a_wire`,
  `web/servicio.py`, restated verbatim in this slice's build instructions).
  Returns, for every ACTIVE source, a fresh, short-lived provider access
  token -- NEVER the refresh token, and this client never writes it anywhere:
  `CuentaClient.listar_fuentes` returns it in memory only, for the one
  `sincronizar` pass that pulled it (`cli.py`'s own job to never persist it).

- `enviar_estado` -- ONE push per sync pass of the FULL current set of this
  person's own mailbox sources (local and Cuenta-sourced alike):
  `POST /servicio/buzon/estado/{sub}`, per `BUZON-ESTADO-A-CUENTA-1`
  (`../dev-protocol/contracts/buzon-estado-a-cuenta-v1.md`, restated
  verbatim below). Carries only the fields the contract names -- never a
  subject, a sender, a token, or an unmasked address: the address is
  masked (`enmascarar_direccion`) before it is ever placed in the request
  body.

A 404 (`cuenta_no_encontrada` -- an unknown or erased `sub`, or a caller
that is not the `buzon` client) or any transport failure is raised as
`CuentaPullError`, carrying only the step (`fuentes` or `estado`), the HTTP
status, or the exception's class name -- never content, mirroring
`graph.GraphError`'s own shape. The caller (`cli.py`) catches this, logs
one count-only line, and keeps every source working exactly as before
this slice: a failed `enviar_estado` call never fails the pass.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

import httpx

from duna_buzon import registro, temporizadores

RUTA_SERVICIO_FUENTES = "/servicio/fuentes/{sub}"
RUTA_SERVICIO_ESTADO = "/servicio/buzon/estado/{sub}"

ProveedorCuenta = Literal["google", "microsoft"]

# The wire's own `proveedor` enum (`buzon-estado-a-cuenta-v1.md`) is a
# superset of what THIS connector's own `fuentes.FuenteConfig.proveedor`
# ever holds ("microsoft", "imap", "google") -- it also names "zoho" for
# other connectors Cuenta Duna may have. This connector never emits
# "zoho": an `imap` source stays "imap" on the wire, whatever mailbox
# provider it happens to be hosted on.
ProveedorEstadoWire = Literal["microsoft", "google", "zoho", "imap"]
OrigenEstadoWire = Literal["cuenta", "buzon"]
AlertaEstadoWire = Literal["reingreso_requerido", "error"]

_ESTADO_HTTP_OK = 204


class CuentaPullError(Exception):
    """Carries only the step (`paso`: `"fuentes"` or `"estado"`), the HTTP
    status if any, and -- for a transport failure -- the exception's CLASS
    name, never its message. `cli.py` turns this into the one-line,
    count-only log a failed call prints."""

    def __init__(
        self,
        *,
        paso: str = "fuentes",
        http_status: int | None = None,
        clase_excepcion: str | None = None,
    ) -> None:
        self.paso = paso
        self.http_status = http_status
        self.clase_excepcion = clase_excepcion
        super().__init__(self.motivo)

    @property
    def motivo(self) -> str:
        if self.http_status is not None:
            return f"cuenta {self.paso} http={self.http_status}"
        return f"cuenta {self.paso} error={self.clase_excepcion}"


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


@dataclass(frozen=True)
class FuenteEstadoWire:
    """One source's own heartbeat, as `cli.py` builds it for `enviar_estado`
    -- `direccion` is the FULL, unmasked address: `enviar_estado` is the
    only place that ever masks it, right before it goes on the wire, so no
    caller needs to remember to."""

    fuente_id: str
    proveedor: ProveedorEstadoWire
    direccion: str
    etiqueta: str | None
    origen: OrigenEstadoWire
    ultimo_paso_en: str
    vistos: int
    reenviados: int
    alerta: AlertaEstadoWire | None = None


def enmascarar_direccion(direccion: str) -> str:
    """`"carlos@hotmail.com"` -> `"c***@hotmail.com"`, the contract's own
    example (`buzon-estado-a-cuenta-v1.md`): the local part's first
    character, then a fixed mask, then the domain unchanged. An address
    with no `@` (should never happen, but never raises on bad data) masks
    to a bare `"***"`."""
    if "@" not in direccion:
        return "***"
    local, dominio = direccion.split("@", 1)
    primera = local[:1]
    return f"{primera}***@{dominio}"


def _fuente_estado_a_wire(fuente: FuenteEstadoWire) -> dict[str, object]:
    """Exactly the nine fields `buzon-estado-a-cuenta-v1.md` names -- never
    a subject, a sender, a token, or the unmasked address."""
    return {
        "fuente_id": fuente.fuente_id,
        "proveedor": fuente.proveedor,
        "direccion_enmascarada": enmascarar_direccion(fuente.direccion),
        "etiqueta": fuente.etiqueta,
        "origen": fuente.origen,
        "ultimo_paso_en": fuente.ultimo_paso_en,
        "vistos": fuente.vistos,
        "reenviados": fuente.reenviados,
        "alerta": fuente.alerta,
    }


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
            base_url=url_base,
            transport=transport,
            auth=(client_id, client_secret),
            timeout=temporizadores.TIMEOUT_HTTPX,
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

    def enviar_estado(self, fuentes: list[FuenteEstadoWire], *, enviado_en: datetime.datetime) -> None:
        """`POST /servicio/buzon/estado/{sub}` -- one heartbeat carrying
        `fuentes`' FULL current set, per `buzon-estado-a-cuenta-v1.md`.
        Raises `CuentaPullError(paso="estado", ...)` on anything but a 204;
        `cli.py` is the one caller, and it never lets this stop a pass."""
        cuerpo = {
            "fuentes": [_fuente_estado_a_wire(f) for f in fuentes],
            "enviado_en": registro.formato_fecha_utc(enviado_en),
        }
        try:
            respuesta = self._cliente.post(RUTA_SERVICIO_ESTADO.format(sub=self._sub), json=cuerpo)
        except httpx.TransportError as exc:
            raise CuentaPullError(paso="estado", clase_excepcion=type(exc).__name__) from exc
        if respuesta.status_code != _ESTADO_HTTP_OK:
            raise CuentaPullError(paso="estado", http_status=respuesta.status_code)
