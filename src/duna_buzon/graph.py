"""The Microsoft Graph client. NEVER CHANGE THE MAILBOX: this module's ONLY
HTTP calls are `self._cliente.get(...)` -- no `post`, `patch`, or `delete`
exists anywhere in this class, so there is no method here that COULD move,
flag, mark-read or send anything. `tests/test_graph_get_only.py` proves it
from the outside, with a transport that fails the test on any non-GET
request.

`/me/messages` (never `/me/mailFolders/inbox/messages`) reaches every
folder in the mailbox, since Carlos's own inbox rules move messages out of
the Inbox before this connector ever sees them -- UNLESS this source's own
`carpetas` setting narrows it (see below).

BUZON-GRAPH-TIMEOUT-1: every call carries `temporizadores.TIMEOUT_HTTPX`
(never httpx's own 5-second default), and `_get` retries the TRANSIENT
class only -- a transport failure (timeout, connection reset, ...) or one
of `_CODIGOS_REINTENTABLES` -- at most twice, with backoff
`_ESPERAS_BASE_SEGUNDOS` (1s, then 4s), capped at `_ESPERA_MAXIMA_SEGUNDOS`
and overridden by the response's own `Retry-After` when one is present.
Every other status (401, every other 4xx) still fails on the first
attempt, exactly as before this slice. A pass that exhausts both retries
stops exactly like today: cursor unchanged, stop reason recorded, service
exits non-zero -- `_get` still only ever raises `GraphAutenticacionError`/
`GraphErrorTransitorio`, never anything new.

BUZON-GRAPH-502-1: `_CODIGOS_REINTENTABLES` now also carries 500, 502 and
504 -- the production stop this slice fixes (`parada=graph listar
http=502`) showed that a consumer Outlook.com mailbox answers `/me/
messages` with the whole Graph 5xx "transient" class, not only 503, once
the query is expensive enough (every folder, `$top=50`). Treated exactly
like 503: same retry budget, same backoff, same `Retry-After` honouring.

When the LIST call keeps failing with one of those codes even after its
retry budget is spent, `_listar_pagina` degrades the PAGE SIZE before
giving up: one single further attempt (no backoff sleep -- this is an
escalation, not the same transient retry) at `$top=10`, then one more at
`$top=1`, each only tried if the page's current size is still larger than
it. `top_lista_usado` carries whichever `$top` was last in use -- the one
that finally got a page through, or, if every size still failed, the
smallest one tried -- for `cli.py` to put in `estado.json` next to the
stop reason. The cursor and dedup semantics never change: a degraded page
still asks for the exact same `$filter`/`$orderby`, just fewer rows per
response, so the same messages come back in the same order, only spread
over more pages.

`carpetas` (mirroring `cliente_imap.py`'s own IMAP-only setting, extended
here to this provider -- BUZON-GRAPH-502-1), when given, narrows this
provider to reading ONLY those folders, one at a time in the order
configured, via `/me/mailFolders/{carpeta}/messages` instead of `/me/
messages` -- `carpeta` is whatever Graph itself accepts there: a
well-known folder name (`inbox`, `archive`, ...) or a folder id. Each
folder's own listing keeps every bit of today's shape (same `$filter`/
`$orderby`/`$select`/`$top`, same pagination, same retry/degrade
handling) -- this only changes WHERE the request points. `None` (the
default, and every source before this slice) means today's behaviour
exactly: `/me/messages`, reaching every folder in one call, byte for byte
the same request this client has always sent.

BUZON-CARPETAS-MICROSOFT-CLI-1: `listar_carpetas` is a separate, read-only
operator helper (`cli.py`'s `carpetas-graph <id>`) -- every top-level mail
folder's display name, id, and well-known name where one applies -- so an
operator can pick the right `carpetas` entry above without guessing. Never
called from `listar_mensajes`'s own sync path.
"""

from __future__ import annotations

import base64
import datetime
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

import httpx

from duna_buzon import temporizadores
from duna_buzon.proveedor_errores import ProveedorAutenticacionError, ProveedorErrorTransitorio

URL_BASE = "https://graph.microsoft.com/v1.0"
_TIPO_ODATA_ADJUNTO_ARCHIVO = "#microsoft.graph.fileAttachment"

_CAMPOS_RESUMEN = "id,internetMessageId,from,subject,receivedDateTime,hasAttachments"
_TAMANO_PAGINA = "50"
_TOP_INICIAL = 50

# BUZON-CARPETAS-MICROSOFT-CLI-1 -- the well-known folder names `carpetas-
# graph` probes for, one GET per name, since Graph's own `mailFolder`
# resource carries no field naming a folder's well-known name; this is the
# same small set `deploy/host/README.md` already documents as valid
# `carpetas` entries for a `proveedor: "microsoft"` source. A mailbox that
# lacks one (a 404) simply never appears in the probed map -- not an error.
_CARPETAS_BIEN_CONOCIDAS: tuple[str, ...] = (
    "inbox",
    "archive",
    "sentitems",
    "drafts",
    "deleteditems",
    "junkemail",
    "outbox",
)

# BUZON-GRAPH-502-1 -- the page-size ladder a persistently failing LIST page
# degrades through, smallest last, before giving up: `$top=10`, then
# `$top=1`. Each step is tried only if the page's current size is still
# bigger than it (so a page already at 10 only ever tries 1, never 10
# again).
_TOPES_DEGRADADOS: tuple[int, ...] = (10, 1)

# BUZON-GRAPH-TIMEOUT-1 (429/503), extended by BUZON-GRAPH-502-1 (500/502/
# 504) -- retried, in-pass: a transport failure, or one of these status
# codes, every one of which Graph itself documents or is observed in
# production to mean "back off and try again" (429 throttling, 503
# momentarily unavailable, 500/502/504 the rest of Graph's own transient
# 5xx class, seen in production against a large consumer mailbox). Every
# other status (401, every other 4xx) is never retried -- those are either
# a real auth failure or a defect this connector should stop on
# immediately, not paper over with a retry.
_CODIGOS_REINTENTABLES = frozenset({429, 500, 502, 503, 504})
_REINTENTOS_MAXIMOS = 2
_ESPERAS_BASE_SEGUNDOS: tuple[float, ...] = (1.0, 4.0)
_ESPERA_MAXIMA_SEGUNDOS = 30.0


def _espera_retry_after(respuesta: httpx.Response) -> float | None:
    """The response's own `Retry-After`, in seconds, capped at
    `_ESPERA_MAXIMA_SEGUNDOS` -- `None` when the header is absent or not a
    plain integer (Graph never sends the HTTP-date form on a 429/503, so
    this never needs to parse one)."""
    valor = respuesta.headers.get("Retry-After")
    if valor is None:
        return None
    try:
        segundos = float(valor)
    except ValueError:
        return None
    return min(segundos, _ESPERA_MAXIMA_SEGUNDOS)


class GraphError(Exception):
    """Base for every failure this client raises. Carries only the step
    (`listar`/`adjuntos`/`mensaje`), the HTTP status if any, and -- for a
    transport failure -- the exception's CLASS name, never its message: a
    sincronizar.py caller turns this into the stop reason it logs."""

    def __init__(
        self, paso: str, *, http_status: int | None = None, clase_excepcion: str | None = None
    ) -> None:
        self.paso = paso
        self.http_status = http_status
        self.clase_excepcion = clase_excepcion
        super().__init__(self.motivo)

    @property
    def motivo(self) -> str:
        if self.http_status is not None:
            return f"graph {self.paso} http={self.http_status}"
        return f"graph {self.paso} error={self.clase_excepcion}"


class GraphAutenticacionError(GraphError, ProveedorAutenticacionError):
    """Graph answered 401 -- the access token is no longer good."""


class GraphErrorTransitorio(GraphError, ProveedorErrorTransitorio):
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
    # An opaque, provider-defined resume token -- Microsoft never sets one
    # (the datetime cursor `sincronizar.py` already tracks is precise
    # enough for it); the IMAP provider (`cliente_imap.py`) sets
    # `"<uidvalidity>:<uid>"` so a later pass can resume by UID instead of
    # re-scanning a whole day of mail.
    cursor_proveedor: str | None = None


@dataclass(frozen=True)
class AdjuntoMetadata:
    id: str
    nombre: str
    tamano: int
    es_en_linea: bool
    tipo_odata: str


@dataclass(frozen=True)
class AdjuntoDescargado:
    nombre: str
    contenido: bytes


@dataclass(frozen=True)
class CarpetaGraph:
    nombre: str
    id: str
    bien_conocida: str | None


@dataclass(frozen=True)
class MensajeCompleto:
    cuerpo_html: str
    adjuntos: tuple[AdjuntoDescargado, ...]
    # Non-inline attachments that are not `fileAttachment` (an
    # `itemAttachment` or `referenceAttachment`) -- `contentBytes` only
    # exists on `fileAttachment`, so these are never fetched, only counted.
    adjuntos_descartados: int = 0


def _top_de(url: str, parametros: dict[str, str] | None) -> int:
    """The `$top` a page's own request is currently using -- read from the
    `parametros` dict for a fresh request, or parsed off `url`'s own query
    string for a `@odata.nextLink` continuation (which already bakes its
    `$top` into the URL). `_TOP_INICIAL` only as a last-resort fallback,
    which a request this client built itself never actually needs."""
    if parametros is not None and "$top" in parametros:
        return int(parametros["$top"])
    valor = httpx.URL(url).params.get("$top")
    return int(valor) if valor is not None else _TOP_INICIAL


def _con_top(
    url: str, parametros: dict[str, str] | None, top_nuevo: int
) -> tuple[str, dict[str, str] | None]:
    """The same request, with `$top` replaced by `top_nuevo` -- every other
    part of the query (`$filter`, `$orderby`, `$select`, or a nextLink's own
    skip token) untouched."""
    if parametros is not None:
        nuevos = dict(parametros)
        nuevos["$top"] = str(top_nuevo)
        return url, nuevos
    return str(httpx.URL(url).copy_set_param("$top", str(top_nuevo))), None


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
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        carpetas: Sequence[str] | None = None,
    ) -> None:
        self._cliente = httpx.Client(
            base_url=URL_BASE,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
            timeout=temporizadores.TIMEOUT_HTTPX,
        )
        self._dormir = dormir
        self._carpetas = tuple(carpetas) if carpetas is not None else None
        # BUZON-GRAPH-TIMEOUT-1 -- total retries this client has performed
        # across every call so far this pass, for `estado.json` (`cli.py`
        # reads this once the pass ends, via `getattr(..., 0)` so a
        # provider with no such counter, e.g. IMAP or Gmail, reports 0).
        self.reintentos_usados = 0
        # BUZON-GRAPH-502-1 -- the `$top` last in use for the LIST call,
        # for `estado.json` the same way (`getattr(..., None)`, omitted for
        # a provider with no such concept).
        self.top_lista_usado = _TOP_INICIAL

    def cerrar(self) -> None:
        self._cliente.close()

    def _get(
        self,
        url: str,
        *,
        paso: str,
        params: dict[str, str] | None = None,
        reintentos_maximos: int = _REINTENTOS_MAXIMOS,
    ) -> httpx.Response:
        intento = 0
        while True:
            try:
                respuesta = self._cliente.get(url, params=params)
            except httpx.TransportError as exc:
                if intento >= reintentos_maximos:
                    raise GraphErrorTransitorio(paso, clase_excepcion=type(exc).__name__) from exc
                self._dormir(_ESPERAS_BASE_SEGUNDOS[intento])
                self.reintentos_usados += 1
                intento += 1
                continue

            if respuesta.status_code in _CODIGOS_REINTENTABLES and intento < reintentos_maximos:
                espera = _espera_retry_after(respuesta)
                if espera is None:
                    espera = _ESPERAS_BASE_SEGUNDOS[intento]
                self._dormir(espera)
                self.reintentos_usados += 1
                intento += 1
                continue

            return respuesta

    @staticmethod
    def _exigir_ok(respuesta: httpx.Response, *, paso: str) -> None:
        if respuesta.status_code == 200:
            return
        if respuesta.status_code == 401:
            raise GraphAutenticacionError(paso, http_status=401)
        raise GraphErrorTransitorio(paso, http_status=respuesta.status_code)

    def _listar_pagina(
        self, url: str, parametros: dict[str, str] | None, *, paso: str
    ) -> httpx.Response:
        """One LIST page, with the `$top`-degrade escalation
        (BUZON-GRAPH-502-1): if the page still answers with one of
        `_CODIGOS_REINTENTABLES` after `_get`'s own retry budget is spent,
        try it again, ONCE per step (no backoff sleep -- this is a
        separate escalation, not the same transient retry), at each size in
        `_TOPES_DEGRADADOS` smaller than the page's current one, stopping
        at the first that gets back anything else (success or a genuine
        non-transient failure). `self.top_lista_usado` always ends up
        naming whichever `$top` that last attempt used."""
        top_actual = _top_de(url, parametros)
        self.top_lista_usado = top_actual
        respuesta = self._get(url, params=parametros, paso=paso)
        if respuesta.status_code not in _CODIGOS_REINTENTABLES:
            return respuesta

        topes_restantes = [t for t in _TOPES_DEGRADADOS if t < top_actual]
        for indice, top_nuevo in enumerate(topes_restantes):
            url_degradada, parametros_degradados = _con_top(url, parametros, top_nuevo)
            self.top_lista_usado = top_nuevo
            es_el_ultimo = indice == len(topes_restantes) - 1
            try:
                respuesta = self._get(
                    url_degradada, params=parametros_degradados, paso=paso, reintentos_maximos=0
                )
            except GraphErrorTransitorio:
                if es_el_ultimo:
                    raise
                continue
            if respuesta.status_code not in _CODIGOS_REINTENTABLES:
                return respuesta
        return respuesta

    def _listar_mensajes_de(
        self, desde: datetime.datetime, carpeta: str | None
    ) -> Iterator[MensajeResumen]:
        parametros: dict[str, str] | None = {
            "$filter": f"receivedDateTime ge {_formato_fecha_filtro(desde)}",
            "$orderby": "receivedDateTime asc",
            "$select": _CAMPOS_RESUMEN,
            "$top": _TAMANO_PAGINA,
        }
        url = f"/me/mailFolders/{carpeta}/messages" if carpeta is not None else "/me/messages"
        while True:
            respuesta = self._listar_pagina(url, parametros, paso="listar")
            self._exigir_ok(respuesta, paso="listar")
            cuerpo = respuesta.json()
            for fila in cuerpo.get("value", []):
                yield _mensaje_resumen_desde_json(fila)
            siguiente = cuerpo.get("@odata.nextLink")
            if not siguiente:
                return
            url = siguiente
            parametros = None  # the nextLink already carries its own query

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]:
        """Ascending order, `receivedDateTime` filtered from `desde` (the
        caller applies the 10-minute overlap before calling this), following
        `@odata.nextLink` to exhaustion. Every folder in one call (`/me/
        messages`) unless `self._carpetas` narrows this source to specific
        ones -- in which case each named folder is read in full, in the
        order configured, via its own `/me/mailFolders/{carpeta}/messages`."""
        carpetas = self._carpetas if self._carpetas is not None else (None,)
        for carpeta in carpetas:
            yield from self._listar_mensajes_de(desde, carpeta)

    def listar_carpetas(self) -> list[CarpetaGraph]:
        """Read-only, for the `carpetas-graph` operator command
        (`BUZON-CARPETAS-MICROSOFT-CLI-1`): every TOP-LEVEL mail folder
        this mailbox has -- display name, id, and the well-known name
        (`inbox`, `archive`, ...) when this folder's id matches one of
        `_CARPETAS_BIEN_CONOCIDAS`'s own lookup, `None` otherwise -- so
        the operator can choose the right `carpetas` entry for
        `fijar-carpetas` without guessing. One `/me/mailFolders` LIST
        call, plus one `/me/mailFolders/{nombre}` GET per well-known
        name this client probes -- every one of them a GET, like every
        other call this client makes."""
        respuesta = self._get(
            "/me/mailFolders", params={"$top": "250", "$select": "displayName,id"}, paso="carpetas"
        )
        self._exigir_ok(respuesta, paso="carpetas")
        filas = respuesta.json().get("value", [])

        id_a_bien_conocida: dict[str, str] = {}
        for nombre_bk in _CARPETAS_BIEN_CONOCIDAS:
            respuesta_bk = self._get(
                f"/me/mailFolders/{nombre_bk}", params={"$select": "id"}, paso="carpetas"
            )
            if respuesta_bk.status_code == 200:
                id_a_bien_conocida[respuesta_bk.json()["id"]] = nombre_bk

        return sorted(
            (
                CarpetaGraph(
                    nombre=fila.get("displayName") or "",
                    id=fila["id"],
                    bien_conocida=id_a_bien_conocida.get(fila["id"]),
                )
                for fila in filas
            ),
            key=lambda carpeta: carpeta.nombre,
        )

    def listar_adjuntos_metadata(self, id_mensaje: str) -> list[AdjuntoMetadata]:
        """Metadata only -- names, sizes and which kind of attachment each
        one is, never `contentBytes` -- so a message that turns out not to
        match a rule is never read past this. `@odata.type` comes back on
        every row as Graph's own type annotation without being named in
        `$select` -- it cannot be, since `$select` only names declared
        properties and `@odata.type` is not one."""
        respuesta = self._get(
            f"/me/messages/{id_mensaje}/attachments",
            params={"$select": "name,size,isInline"},
            paso="adjuntos",
        )
        self._exigir_ok(respuesta, paso="adjuntos")
        cuerpo = respuesta.json()
        return [
            AdjuntoMetadata(
                id=fila.get("id", ""),
                nombre=fila.get("name") or "",
                tamano=fila.get("size", 0),
                es_en_linea=bool(fila.get("isInline")),
                tipo_odata=fila.get("@odata.type") or "",
            )
            for fila in cuerpo.get("value", [])
        ]

    def obtener_mensaje_completo(
        self, id_mensaje: str, metadatos_adjuntos: Sequence[AdjuntoMetadata]
    ) -> MensajeCompleto:
        """The body, plus every non-inline `fileAttachment`'s bytes fetched
        one at a time -- called ONLY once a message is already known to
        match a rule. `metadatos_adjuntos` is the list the caller already
        pulled via `listar_adjuntos_metadata`; this never lists attachments
        again. `contentBytes` exists only on the derived `fileAttachment`
        type, not on the base `attachment` type Graph's attachment list
        returns -- selecting it in a `$select`/`$expand` over that base
        collection is what produced the 400 this replaces. An
        `itemAttachment` or `referenceAttachment` has no `contentBytes` at
        all, so it is skipped and counted, never fetched."""
        respuesta = self._get(f"/me/messages/{id_mensaje}", params={"$select": "body"}, paso="mensaje")
        self._exigir_ok(respuesta, paso="mensaje")
        cuerpo = respuesta.json()
        cuerpo_html = (cuerpo.get("body") or {}).get("content") or ""

        adjuntos: list[AdjuntoDescargado] = []
        descartados = 0
        for metadato in metadatos_adjuntos:
            if metadato.es_en_linea:
                continue
            if metadato.tipo_odata != _TIPO_ODATA_ADJUNTO_ARCHIVO:
                descartados += 1
                continue
            respuesta_adjunto = self._get(
                f"/me/messages/{id_mensaje}/attachments/{metadato.id}", paso="adjunto"
            )
            self._exigir_ok(respuesta_adjunto, paso="adjunto")
            cuerpo_adjunto = respuesta_adjunto.json()
            contenido_b64 = cuerpo_adjunto.get("contentBytes")
            if not contenido_b64:
                continue
            adjuntos.append(
                AdjuntoDescargado(
                    nombre=metadato.nombre or "adjunto",
                    contenido=base64.b64decode(contenido_b64),
                )
            )
        return MensajeCompleto(
            cuerpo_html=cuerpo_html, adjuntos=tuple(adjuntos), adjuntos_descartados=descartados
        )
