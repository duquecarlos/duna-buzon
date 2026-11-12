"""The Gmail API provider -- read-only, for a source Carlos connected
through Cuenta Duna's own OAuth (`CUENTA-API-FUENTES-CONECTADAS-1`), never
signed into directly by this connector: the access token this client uses
is pulled fresh every pass from Cuenta Duna (`cuenta.py`) and never written
to disk by anything in this module.

NEVER CHANGE THE MAILBOX: this module's only HTTP calls are
`self._cliente.get(...)` -- no `modify`, `trash`, `delete`, `send` or
`batchModify` call exists anywhere in this class, so there is no method
here that COULD move, label, delete or send anything.

`users.messages.list` returns only ids -- Gmail's `format=metadata` cannot
tell this client whether a message has an attachment at all (it returns
headers only, no `payload.parts`), so this client fetches each candidate
message ONCE, in full (`format=full`), and caches the parsed MIME structure
for the rest of the pass. `listar_adjuntos_metadata`/
`obtener_mensaje_completo` reuse that cache instead of re-fetching the
message -- attachment CONTENT is still its own call per attachment
(`users.messages.attachments.get`), fetched only once a message has
already matched a rule, mirroring Graph's own split between metadata and
content.

Gmail's `after:` search operator is not documented to a precision finer
than a day, so this client queries one day EARLIER than `desde` and then
filters exactly in Python (`recibido_en < desde` skipped) -- the identical
discipline `cliente_imap.py`'s own SINCE-date fallback already uses for the
same reason. There is no provider-defined resume cursor here, unlike
IMAP's UID: a Gmail message id is not an incrementing integer, so there is
no cheap "resume from the last one" primitive to build one from -- this
provider, like the Microsoft one, relies entirely on the existing datetime
cursor plus Finanzas' own idempotency on `id_externo` to absorb a
re-scanned overlap window.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import email.header
import email.utils
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from duna_buzon.graph import AdjuntoDescargado, AdjuntoMetadata, MensajeCompleto, MensajeResumen
from duna_buzon.proveedor_errores import ProveedorAutenticacionError, ProveedorErrorTransitorio

URL_BASE = "https://gmail.googleapis.com/gmail/v1"
_TAMANO_PAGINA = 100
_HOLGURA_BUSQUEDA = datetime.timedelta(days=1)

_TIPOS_CUERPO = frozenset({"text/plain", "text/html"})


class GmailError(Exception):
    """Base for every failure this client raises. Carries only the step
    (`listar`/`mensaje`/`adjunto`), the HTTP status if any, and -- for a
    transport failure -- the exception's CLASS name, never its message."""

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
            return f"gmail {self.paso} http={self.http_status}"
        return f"gmail {self.paso} error={self.clase_excepcion}"


class GmailAutenticacionError(GmailError, ProveedorAutenticacionError):
    """Gmail answered 401 -- the pulled access token is no longer good
    (expired mid-pass, or revoked). The pass stops; the next run pulls a
    fresh token from Cuenta Duna."""


class GmailErrorTransitorio(GmailError, ProveedorErrorTransitorio):
    """Any other failed Gmail call: a non-200/401 status, or a connection
    failure. Treated as transient -- the pass stops, the next scheduled run
    retries."""


def _decodificar_base64url(valor: str | None) -> bytes:
    if not valor:
        return b""
    relleno = "=" * (-len(valor) % 4)
    try:
        return base64.urlsafe_b64decode(valor + relleno)
    except (binascii.Error, ValueError):
        return b""


def _decodificar_asunto(valor: str | None) -> str | None:
    if valor is None:
        return None
    trozos: list[str] = []
    for texto, codificacion in email.header.decode_header(valor):
        if isinstance(texto, bytes):
            trozos.append(texto.decode(codificacion or "ascii", errors="replace"))
        else:
            trozos.append(texto)
    return "".join(trozos)


def _encabezado(headers: Sequence[dict[str, Any]], nombre: str) -> str | None:
    nombre_cf = nombre.casefold()
    for h in headers:
        if str(h.get("name", "")).casefold() == nombre_cf:
            valor = h.get("value")
            return str(valor) if valor is not None else None
    return None


def _es_en_linea(headers: Sequence[dict[str, Any]] | None) -> bool:
    disposicion = _encabezado(headers or [], "Content-Disposition") or ""
    return disposicion.strip().lower().startswith("inline")


@dataclass(frozen=True)
class _ParteGmail:
    """One leaf MIME part of a Gmail message's `payload`, numbered by
    nothing (Gmail addresses attachment content by `attachmentId`, not by
    position) -- an attachment candidate (non-empty `filename`) or a body
    candidate (`text/plain`/`text/html`, no `filename`), never both."""

    id_adjunto: str | None
    nombre: str | None
    tipo_mime: str
    tamano: int
    es_en_linea: bool
    es_cuerpo_candidato: bool
    datos_cuerpo: bytes | None


def _particionar_payload(payload: dict) -> list[_ParteGmail]:
    """Walks one Gmail `payload` (a MessagePart) into its leaf parts -- a
    multipart's own `parts` are walked recursively; a part with no `parts`
    at all is a leaf."""
    hijos = payload.get("parts")
    if hijos:
        hojas: list[_ParteGmail] = []
        for hijo in hijos:
            hojas.extend(_particionar_payload(hijo))
        return hojas

    tipo_mime = payload.get("mimeType") or ""
    nombre = payload.get("filename") or None
    cuerpo = payload.get("body") or {}
    tamano = int(cuerpo.get("size") or 0)
    es_adjunto = bool(nombre)
    es_cuerpo_candidato = not es_adjunto and tipo_mime in _TIPOS_CUERPO

    return [
        _ParteGmail(
            id_adjunto=cuerpo.get("attachmentId"),
            nombre=nombre,
            tipo_mime=tipo_mime,
            tamano=tamano,
            es_en_linea=_es_en_linea(payload.get("headers")),
            es_cuerpo_candidato=es_cuerpo_candidato,
            datos_cuerpo=_decodificar_base64url(cuerpo.get("data")) if es_cuerpo_candidato else None,
        )
    ]


def _mensaje_resumen_desde_json(mensaje_json: dict, hojas: Sequence[_ParteGmail]) -> MensajeResumen:
    headers = (mensaje_json.get("payload") or {}).get("headers") or []
    remitente = email.utils.parseaddr(_encabezado(headers, "From") or "")[1]
    asunto = _decodificar_asunto(_encabezado(headers, "Subject"))
    message_id = _encabezado(headers, "Message-ID") or _encabezado(headers, "Message-Id")
    recibido_en = datetime.datetime.fromtimestamp(
        int(mensaje_json["internalDate"]) / 1000, tz=datetime.UTC
    )
    return MensajeResumen(
        id=mensaje_json["id"],
        internet_message_id=(message_id.strip() if message_id else None) or mensaje_json["id"],
        remitente=remitente,
        asunto=asunto,
        recibido_en=recibido_en,
        tiene_adjuntos=any(hoja.nombre for hoja in hojas),
    )


class GmailClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._cliente = httpx.Client(
            base_url=URL_BASE, transport=transport, headers={"Authorization": f"Bearer {token}"}
        )
        self._cache: dict[str, tuple[dict, list[_ParteGmail]]] = {}

    def cerrar(self) -> None:
        self._cliente.close()

    def _get(
        self, url: str, *, paso: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return self._cliente.get(url, params=params)
        except httpx.TransportError as exc:
            raise GmailErrorTransitorio(paso, clase_excepcion=type(exc).__name__) from exc

    @staticmethod
    def _exigir_ok(respuesta: httpx.Response, *, paso: str) -> None:
        if respuesta.status_code == 200:
            return
        if respuesta.status_code == 401:
            raise GmailAutenticacionError(paso, http_status=401)
        raise GmailErrorTransitorio(paso, http_status=respuesta.status_code)

    def _mensaje_completo_crudo(self, id_mensaje: str) -> tuple[dict, list[_ParteGmail]]:
        if id_mensaje in self._cache:
            return self._cache[id_mensaje]
        respuesta = self._get(
            f"/users/me/messages/{id_mensaje}", params={"format": "full"}, paso="mensaje"
        )
        self._exigir_ok(respuesta, paso="mensaje")
        mensaje_json = respuesta.json()
        hojas = _particionar_payload(mensaje_json.get("payload") or {})
        resultado = (mensaje_json, hojas)
        self._cache[id_mensaje] = resultado
        return resultado

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]:
        desde_utc = desde.astimezone(datetime.UTC) if desde.tzinfo else desde.replace(tzinfo=datetime.UTC)
        consulta_desde = desde_utc - _HOLGURA_BUSQUEDA
        consulta = f"after:{int(consulta_desde.timestamp())}"
        parametros: dict[str, Any] | None = {"q": consulta, "maxResults": _TAMANO_PAGINA}
        url = "/users/me/messages"
        while True:
            respuesta = self._get(url, params=parametros, paso="listar")
            self._exigir_ok(respuesta, paso="listar")
            cuerpo = respuesta.json()
            for fila in cuerpo.get("messages") or []:
                mensaje_json, hojas = self._mensaje_completo_crudo(fila["id"])
                resumen = _mensaje_resumen_desde_json(mensaje_json, hojas)
                if resumen.recibido_en < desde_utc:
                    # Only the day-granularity `after:` fallback can ever
                    # return something earlier than `desde` -- filtered out
                    # exactly, never skipped a day early.
                    continue
                yield resumen
            siguiente = cuerpo.get("nextPageToken")
            if not siguiente:
                return
            parametros = {"q": consulta, "maxResults": _TAMANO_PAGINA, "pageToken": siguiente}

    def listar_adjuntos_metadata(self, id_mensaje: str) -> list[AdjuntoMetadata]:
        _, hojas = self._mensaje_completo_crudo(id_mensaje)
        return [
            AdjuntoMetadata(
                id=hoja.id_adjunto or "",
                nombre=hoja.nombre or "",
                tamano=hoja.tamano,
                es_en_linea=hoja.es_en_linea,
                # No OData type annotation over the Gmail API -- repurposed
                # exactly like `cliente_imap.py` already does, to carry the
                # part's own MIME type instead. Nothing outside this
                # module's own `obtener_mensaje_completo` reads this field
                # for a Gmail-built `AdjuntoMetadata`.
                tipo_odata=hoja.tipo_mime,
            )
            for hoja in hojas
            if hoja.nombre
        ]

    def obtener_mensaje_completo(
        self, id_mensaje: str, metadatos_adjuntos: Sequence[AdjuntoMetadata]
    ) -> MensajeCompleto:
        _, hojas = self._mensaje_completo_crudo(id_mensaje)
        candidatos_cuerpo = [h for h in hojas if h.es_cuerpo_candidato]
        elegido = next((h for h in candidatos_cuerpo if h.tipo_mime == "text/html"), None) or (
            candidatos_cuerpo[0] if candidatos_cuerpo else None
        )
        cuerpo_html = (elegido.datos_cuerpo or b"").decode("utf-8", errors="replace") if elegido else ""

        adjuntos: list[AdjuntoDescargado] = []
        for metadato in metadatos_adjuntos:
            if metadato.es_en_linea or not metadato.id:
                continue
            respuesta = self._get(
                f"/users/me/messages/{id_mensaje}/attachments/{metadato.id}", paso="adjunto"
            )
            self._exigir_ok(respuesta, paso="adjunto")
            datos = respuesta.json().get("data")
            adjuntos.append(
                AdjuntoDescargado(
                    nombre=metadato.nombre or "adjunto", contenido=_decodificar_base64url(datos)
                )
            )

        return MensajeCompleto(cuerpo_html=cuerpo_html, adjuntos=tuple(adjuntos))
