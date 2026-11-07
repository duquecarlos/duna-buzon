"""The Microsoft Graph client. NEVER CHANGE THE MAILBOX: this module's ONLY
HTTP calls are `self._cliente.get(...)` -- no `post`, `patch`, or `delete`
exists anywhere in this class, so there is no method here that COULD move,
flag, mark-read or send anything. `tests/test_graph_get_only.py` proves it
from the outside, with a transport that fails the test on any non-GET
request.

`/me/messages` (never `/me/mailFolders/inbox/messages`) reaches every
folder in the mailbox, since Carlos's own inbox rules move messages out of
the Inbox before this connector ever sees them.
"""

from __future__ import annotations

import base64
import datetime
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

URL_BASE = "https://graph.microsoft.com/v1.0"

_CAMPOS_RESUMEN = "id,internetMessageId,from,subject,receivedDateTime,hasAttachments"
_TAMANO_PAGINA = "50"


class GraphError(Exception):
    """Base for every failure this client raises."""


class GraphAutenticacionError(GraphError):
    """Graph answered 401 -- the access token is no longer good."""


class GraphErrorTransitorio(GraphError):
    """Any other failed Graph call: a non-200/401 status, or a connection
    failure. Treated as transient -- the pass stops, the cursor does not
    advance past it, the next timer run tries again."""


@dataclass(frozen=True)
class MensajeResumen:
    id: str
    internet_message_id: str
    remitente: str
    asunto: str | None
    recibido_en: datetime.datetime
    tiene_adjuntos: bool


@dataclass(frozen=True)
class AdjuntoMetadata:
    id: str
    nombre: str
    tamano: int


@dataclass(frozen=True)
class AdjuntoDescargado:
    nombre: str
    contenido: bytes


@dataclass(frozen=True)
class MensajeCompleto:
    cuerpo_html: str
    adjuntos: tuple[AdjuntoDescargado, ...]


def _formato_fecha_filtro(momento: datetime.datetime) -> str:
    momento_utc = (
        momento.astimezone(datetime.UTC) if momento.tzinfo else momento.replace(tzinfo=datetime.UTC)
    )
    return momento_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsear_fecha(valor: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(valor.replace("Z", "+00:00"))


def _mensaje_resumen_desde_json(fila: dict) -> MensajeResumen:
    remitente_obj = (fila.get("from") or {}).get("emailAddress") or {}
    return MensajeResumen(
        id=fila["id"],
        internet_message_id=fila.get("internetMessageId") or fila["id"],
        remitente=remitente_obj.get("address") or "",
        asunto=fila.get("subject"),
        recibido_en=_parsear_fecha(fila["receivedDateTime"]),
        tiene_adjuntos=bool(fila.get("hasAttachments")),
    )


class GraphClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._cliente = httpx.Client(
            base_url=URL_BASE,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    def cerrar(self) -> None:
        self._cliente.close()

    def _get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            return self._cliente.get(url, params=params)
        except httpx.TransportError as exc:
            raise GraphErrorTransitorio(type(exc).__name__) from exc

    @staticmethod
    def _exigir_ok(respuesta: httpx.Response) -> None:
        if respuesta.status_code == 200:
            return
        if respuesta.status_code == 401:
            raise GraphAutenticacionError("graph answered 401")
        raise GraphErrorTransitorio(f"graph answered {respuesta.status_code}")

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]:
        """Ascending order, `receivedDateTime` filtered from `desde` (the
        caller applies the 10-minute overlap before calling this), every
        folder, following `@odata.nextLink` to exhaustion."""
        parametros: dict[str, str] | None = {
            "$filter": f"receivedDateTime ge {_formato_fecha_filtro(desde)}",
            "$orderby": "receivedDateTime asc",
            "$select": _CAMPOS_RESUMEN,
            "$top": _TAMANO_PAGINA,
        }
        url = "/me/messages"
        while True:
            respuesta = self._get(url, params=parametros)
            self._exigir_ok(respuesta)
            cuerpo = respuesta.json()
            for fila in cuerpo.get("value", []):
                yield _mensaje_resumen_desde_json(fila)
            siguiente = cuerpo.get("@odata.nextLink")
            if not siguiente:
                return
            url = siguiente
            parametros = None  # the nextLink already carries its own query

    def listar_adjuntos_metadata(self, id_mensaje: str) -> list[AdjuntoMetadata]:
        """Metadata only -- names and sizes, never `contentBytes` -- so a
        message that turns out not to match a rule is never read past
        this."""
        respuesta = self._get(
            f"/me/messages/{id_mensaje}/attachments",
            params={"$select": "name,size"},
        )
        self._exigir_ok(respuesta)
        cuerpo = respuesta.json()
        return [
            AdjuntoMetadata(id=fila.get("id", ""), nombre=fila.get("name") or "", tamano=fila.get("size", 0))
            for fila in cuerpo.get("value", [])
        ]

    def obtener_mensaje_completo(self, id_mensaje: str) -> MensajeCompleto:
        """The body and every non-inline attachment's bytes -- called ONLY
        once a message is already known to match a rule."""
        respuesta = self._get(
            f"/me/messages/{id_mensaje}",
            params={
                "$select": "body",
                "$expand": "attachments($select=name,contentBytes,isInline)",
            },
        )
        self._exigir_ok(respuesta)
        cuerpo = respuesta.json()
        cuerpo_html = (cuerpo.get("body") or {}).get("content") or ""
        adjuntos: list[AdjuntoDescargado] = []
        for fila in cuerpo.get("attachments") or []:
            if fila.get("isInline"):
                continue
            contenido_b64 = fila.get("contentBytes")
            if not contenido_b64:
                continue
            adjuntos.append(
                AdjuntoDescargado(
                    nombre=fila.get("name") or "adjunto",
                    contenido=base64.b64decode(contenido_b64),
                )
            )
        return MensajeCompleto(cuerpo_html=cuerpo_html, adjuntos=tuple(adjuntos))
