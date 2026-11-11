"""The IMAP provider -- read-only, UID-addressed, satisfying `proveedor.Proveedor`
exactly the way `GraphClient` does, with no change to that shape.

NEVER CHANGE THE MAILBOX: this module opens the mailbox with `EXAMINE`
(via `select(..., readonly=True)`), never `SELECT`; every fetch of a
message's content uses `BODY.PEEK[...]`, never `BODY[...]`, so nothing is
ever marked `\\Seen`; and there is no method anywhere in this class that
sends `STORE`, `COPY`, `MOVE` or `EXPUNGE` -- `tests/test_cliente_imap.py`
proves both from the outside, against a fake server that records every
command it receives.

The cursor within one mailbox is `(UIDVALIDITY, UID)`: UID is the only
message identifier that stays valid for the rest of a pass no matter how
many new messages arrive meanwhile (IMAP sequence numbers shift), and
UIDVALIDITY changing means the server reset UID numbering, so a UID this
provider remembers from before must be treated as unusable. That resume
token travels as `graph.MensajeResumen.cursor_proveedor` (`"<uidvalidity>:
<uid>"`) through `sincronizar.ejecutar_pasada` exactly like the datetime
cursor already does, and is persisted ONLY at the same success point --
never advanced on its own, so a pass that stops early never skips a
message the next one would have sent.

Connecting is LAZY: `__init__` does no I/O at all (mirrors `GraphClient`,
whose `httpx.Client` does not connect until the first request) --
`construir_proveedor` can build this client without risking an exception
outside `sincronizar.py`'s own try/except, so one source's bad credential
never stops another source's pass.
"""

from __future__ import annotations

import datetime
import email
import email.header
import email.utils
import quopri
import re
from base64 import b64decode
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from duna_buzon.graph import AdjuntoDescargado, AdjuntoMetadata, MensajeCompleto, MensajeResumen
from duna_buzon.proveedor_errores import ProveedorAutenticacionError, ProveedorErrorTransitorio

BUZON_PREDETERMINADO = "INBOX"

_MESES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MESES_INDICE = {mes: i + 1 for i, mes in enumerate(_MESES)}

_RE_LITERAL_FINAL = re.compile(r"\{\d+\}\s*$")
_RE_UID = re.compile(r"\bUID (\d+)")
_RE_INTERNALDATE = re.compile(
    r'INTERNALDATE "(\d{2})-(\w{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"'
)


class ClienteImapError(Exception):
    """Base for every failure this client raises. Carries only the step
    and, for a protocol-level problem, the server's own response code --
    never a header, a body, or the password."""

    def __init__(self, paso: str, *, detalle: str | None = None) -> None:
        self.paso = paso
        self.detalle = detalle
        mensaje = f"imap {paso}" + (f" detalle={detalle}" if detalle else "")
        super().__init__(mensaje)


class ClienteImapAutenticacionError(ClienteImapError, ProveedorAutenticacionError):
    """LOGIN failed -- the stored app password is no longer good."""


class ClienteImapErrorTransitorio(ClienteImapError, ProveedorErrorTransitorio):
    """Any other failure talking to the server: a connection problem, or a
    command the server did not answer `OK` to. Treated as transient."""


class ConexionImapProtocolo(Protocol):
    """The handful of `imaplib.IMAP4`/`IMAP4_SSL` calls this module makes
    -- the seam tests inject a fake behind, connected to an in-process
    fake server instead of a real one."""

    def login(self, usuario: str, password: str) -> tuple[str, list]: ...

    def select(self, mailbox: str, readonly: bool) -> tuple[str, list]: ...

    def response(self, name: str) -> tuple[str | None, list]: ...

    def uid(self, command: str, *args: str) -> tuple[str, list]: ...

    def logout(self) -> tuple[str, list]: ...


def _construir_conexion_ssl_real(host: str, puerto: int) -> ConexionImapProtocolo:
    import imaplib

    return imaplib.IMAP4_SSL(host, puerto)


# ---------------------------------------------------------------------------
# Wire-level parsing: IMAP's own parenthesized-list syntax (ENVELOPE-less --
# this module reads headers via BODY.PEEK[HEADER.FIELDS (...)] instead,
# which is plain RFC 822 text, not a structure to parse by hand), plus
# BODYSTRUCTURE and the literal payload a FETCH may carry alongside it.
# ---------------------------------------------------------------------------


def _unir_respuesta_fetch(entradas: Sequence[Any]) -> tuple[str, bytes | None]:
    """One FETCH response, reassembled into one string with any literal's
    `{n}` marker stripped (the structural fields never span across it, so
    this never merges two tokens), plus that literal's own bytes -- a
    header block, or one part's raw content. At most one literal is ever
    requested in a single call this module makes."""
    texto: list[str] = []
    literal: bytes | None = None
    for entrada in entradas:
        if isinstance(entrada, tuple):
            prefijo, datos_literal = entrada
            texto.append(_RE_LITERAL_FINAL.sub("", prefijo.decode("utf-8", errors="replace")))
            literal = datos_literal
        else:
            texto.append(entrada.decode("utf-8", errors="replace"))
    return "".join(texto), literal


def _saltar_espacios(texto: str, pos: int) -> int:
    while pos < len(texto) and texto[pos] == " ":
        pos += 1
    return pos


def _parsear_valor(texto: str, pos: int) -> tuple[Any, int]:
    pos = _saltar_espacios(texto, pos)
    if texto[pos] == "(":
        return _parsear_lista(texto, pos)
    if texto[pos] == '"':
        return _parsear_cadena(texto, pos)
    return _parsear_atomo(texto, pos)


def _parsear_lista(texto: str, pos: int) -> tuple[list, int]:
    pos += 1  # '('
    items: list[Any] = []
    pos = _saltar_espacios(texto, pos)
    while pos < len(texto) and texto[pos] != ")":
        valor, pos = _parsear_valor(texto, pos)
        items.append(valor)
        pos = _saltar_espacios(texto, pos)
    return items, pos + 1  # consume ')'


def _parsear_cadena(texto: str, pos: int) -> tuple[str, int]:
    pos += 1  # '"'
    caracteres: list[str] = []
    while texto[pos] != '"':
        if texto[pos] == "\\":
            pos += 1
        caracteres.append(texto[pos])
        pos += 1
    return "".join(caracteres), pos + 1


def _parsear_atomo(texto: str, pos: int) -> tuple[str | None, int]:
    inicio = pos
    while pos < len(texto) and texto[pos] not in " ()":
        pos += 1
    atomo = texto[inicio:pos]
    return (None if atomo == "NIL" else atomo), pos


def _parsear_bodystructure(texto: str) -> list:
    idx = texto.index("BODYSTRUCTURE") + len("BODYSTRUCTURE")
    valor, _ = _parsear_valor(texto, idx)
    return valor


def _parametros_a_dict(valor: list | None) -> dict[str, str]:
    if not valor:
        return {}
    it = iter(valor)
    return {str(k).upper(): str(v) for k, v in zip(it, it, strict=False) if k is not None}


@dataclass(frozen=True)
class _ParteHoja:
    numero: str
    tipo: str
    subtipo: str
    nombre: str | None
    tamano: int
    codificacion: str
    charset: str
    es_adjunto: bool
    es_en_linea: bool


def _particionar_bodystructure(valor: list, numero: str = "") -> list[_ParteHoja]:
    """Walks one BODYSTRUCTURE into its leaf parts, numbered the way IMAP
    `FETCH BODY[<n>]` addresses them: a multipart's children are `1`, `2`,
    ...; a nested multipart's own children extend that with another dot
    (`1.1`, `1.2`); a message with no multipart wrapper has exactly one
    leaf, part `1`."""
    es_multipart = isinstance(valor[0], list)
    if es_multipart:
        hojas: list[_ParteHoja] = []
        for i, hijo in enumerate(valor):
            if not isinstance(hijo, list):
                break  # the multipart subtype string, then its own extension fields
            sub_numero = f"{numero}.{i + 1}" if numero else str(i + 1)
            hojas.extend(_particionar_bodystructure(hijo, sub_numero))
        return hojas

    tipo = (valor[0] or "").upper()
    subtipo = (valor[1] or "").upper()
    parametros_tipo = _parametros_a_dict(valor[2] if len(valor) > 2 else None)
    codificacion = (valor[5] or "7BIT").upper() if len(valor) > 5 else "7BIT"
    tamano = int(valor[6]) if len(valor) > 6 and valor[6] is not None else 0

    idx = 7
    if tipo == "TEXT":
        idx += 1  # lines
    elif tipo == "MESSAGE" and subtipo == "RFC822":
        idx += 3  # envelope, bodystructure, lines

    extension = valor[idx:]
    disposicion = extension[1] if len(extension) > 1 else None
    disposicion_tipo = disposicion[0].upper() if disposicion and disposicion[0] else None

    nombre = None
    if disposicion and len(disposicion) > 1:
        nombre = _parametros_a_dict(disposicion[1]).get("FILENAME")
    if not nombre:
        nombre = parametros_tipo.get("NAME")

    es_cuerpo_candidato = (
        tipo == "TEXT" and subtipo in ("PLAIN", "HTML") and disposicion_tipo != "ATTACHMENT"
    )

    return [
        _ParteHoja(
            numero=numero or "1",
            tipo=tipo,
            subtipo=subtipo,
            nombre=nombre,
            tamano=tamano,
            codificacion=codificacion,
            charset=parametros_tipo.get("CHARSET", "utf-8"),
            es_adjunto=not es_cuerpo_candidato,
            es_en_linea=disposicion_tipo == "INLINE",
        )
    ]


def _decodificar_contenido(crudo: bytes, codificacion: str) -> bytes:
    if codificacion == "BASE64":
        try:
            return b64decode(crudo, validate=False)
        except (ValueError, TypeError):
            return b""
    if codificacion == "QUOTED-PRINTABLE":
        return quopri.decodestring(crudo)
    return crudo


def _formato_fecha_imap(momento: datetime.datetime) -> str:
    momento_utc = (
        momento.astimezone(datetime.UTC) if momento.tzinfo else momento.replace(tzinfo=datetime.UTC)
    )
    return f"{momento_utc.day:02d}-{_MESES[momento_utc.month - 1]}-{momento_utc.year:04d}"


def _parsear_internaldate(texto: str) -> datetime.datetime:
    coincidencia = _RE_INTERNALDATE.search(texto)
    if not coincidencia:
        raise ClienteImapErrorTransitorio("internaldate-ausente")
    dia, mes_str, anio, hora, minuto, segundo, offset = coincidencia.groups()
    mes = _MESES_INDICE.get(mes_str)
    if mes is None:
        raise ClienteImapErrorTransitorio("internaldate-mes-desconocido", detalle=mes_str)
    signo = 1 if offset[0] == "+" else -1
    desplazamiento = datetime.timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
    zona = datetime.timezone(signo * desplazamiento)
    return datetime.datetime(
        int(anio), mes, int(dia), int(hora), int(minuto), int(segundo), tzinfo=zona
    )


def _decodificar_asunto(valor: str | None) -> str | None:
    if valor is None:
        return None
    trozos = []
    for texto, codificacion in email.header.decode_header(valor):
        if isinstance(texto, bytes):
            trozos.append(texto.decode(codificacion or "ascii", errors="replace"))
        else:
            trozos.append(texto)
    return "".join(trozos)


def _parsear_cabeceras(crudo: bytes) -> tuple[str, str | None, str | None]:
    mensaje = email.message_from_bytes(crudo)
    remitente = email.utils.parseaddr(mensaje.get("From", ""))[1]
    asunto = _decodificar_asunto(mensaje.get("Subject"))
    message_id = mensaje.get("Message-Id")
    return remitente, asunto, (message_id.strip() if message_id else None)


def _formato_cursor(uidvalidity: int, uid: int) -> str:
    return f"{uidvalidity}:{uid}"


def _analizar_cursor(cursor: str | None) -> tuple[int, int] | None:
    if cursor is None:
        return None
    try:
        uidvalidity_str, uid_str = cursor.split(":", 1)
        return int(uidvalidity_str), int(uid_str)
    except ValueError:
        return None


class ClienteImap:
    def __init__(
        self,
        *,
        host: str,
        puerto: int,
        usuario: str,
        password: str,
        cursor_proveedor_inicial: str | None = None,
        buzon: str = BUZON_PREDETERMINADO,
        construir_conexion: Callable[[str, int], ConexionImapProtocolo] = _construir_conexion_ssl_real,
    ) -> None:
        self._host = host
        self._puerto = puerto
        self._usuario = usuario
        self._password = password
        self._cursor_inicial = cursor_proveedor_inicial
        self._buzon = buzon
        self._construir_conexion = construir_conexion
        self._conexion: ConexionImapProtocolo | None = None
        self._uidvalidity: int | None = None
        self._uidnext: int | None = None

    def cerrar(self) -> None:
        if self._conexion is None:
            return
        try:
            self._conexion.logout()
        except Exception:
            pass
        self._conexion = None

    def _conectar(self) -> ConexionImapProtocolo:
        if self._conexion is not None:
            return self._conexion

        try:
            conexion = self._construir_conexion(self._host, self._puerto)
        except OSError as exc:
            raise ClienteImapErrorTransitorio("conectar", detalle=type(exc).__name__) from exc

        try:
            typ, _ = conexion.login(self._usuario, self._password)
        except Exception as exc:
            raise ClienteImapAutenticacionError("login", detalle=type(exc).__name__) from exc
        if typ != "OK":
            raise ClienteImapAutenticacionError("login", detalle=typ)

        typ, _ = conexion.select(self._buzon, readonly=True)
        if typ != "OK":
            raise ClienteImapErrorTransitorio("examinar", detalle=typ)

        typ_uv, dat_uv = conexion.response("UIDVALIDITY")
        if typ_uv != "UIDVALIDITY" or not dat_uv or dat_uv[0] is None:
            raise ClienteImapErrorTransitorio("uidvalidity-ausente")
        typ_un, dat_un = conexion.response("UIDNEXT")
        uidnext = int(dat_un[0]) if typ_un == "UIDNEXT" and dat_un and dat_un[0] is not None else None

        self._conexion = conexion
        self._uidvalidity = int(dat_uv[0])
        self._uidnext = uidnext
        return conexion

    def _fetch_uno(self, uid: int, items: str) -> tuple[str, bytes | None]:
        conexion = self._conectar()
        typ, dat = conexion.uid("FETCH", str(uid), f"({items})")
        if typ != "OK" or not dat or dat[0] is None:
            raise ClienteImapErrorTransitorio("fetch", detalle=typ)
        return _unir_respuesta_fetch(dat)

    def _buscar_uids(self, desde: datetime.datetime) -> list[int]:
        conexion = self._conectar()
        assert self._uidvalidity is not None

        cursor = _analizar_cursor(self._cursor_inicial)
        if cursor is not None and cursor[0] == self._uidvalidity:
            uid_resumir = cursor[1] + 1
            if self._uidnext is not None and uid_resumir >= self._uidnext:
                # Nothing can be new: issuing "N:*" here, with N at or
                # past UIDNEXT, is the well-known IMAP quirk where some
                # servers normalize an empty range by returning the LAST
                # message instead of none -- never issued.
                return []
            criterio: tuple[str, str] = ("UID", f"{uid_resumir}:*")
        else:
            criterio = ("SINCE", _formato_fecha_imap(desde))

        typ, dat = conexion.uid("SEARCH", *criterio)
        if typ != "OK":
            raise ClienteImapErrorTransitorio("buscar", detalle=typ)
        cadena = dat[0].decode("ascii", errors="replace") if dat and dat[0] else ""
        return sorted({int(tok) for tok in cadena.split()})

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]:
        self._conectar()
        assert self._uidvalidity is not None
        uidvalidity = self._uidvalidity

        for uid in self._buscar_uids(desde):
            texto, literal = self._fetch_uno(
                uid, "UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)] BODYSTRUCTURE"
            )
            recibido_en = _parsear_internaldate(texto)
            if recibido_en < desde:
                # Only the SINCE-date fallback (day granularity) can ever
                # return something earlier than `desde` -- the UID-resume
                # path never does, since every candidate UID is already
                # strictly newer than the last one processed.
                continue

            remitente, asunto, message_id = _parsear_cabeceras(literal or b"")
            hojas = _particionar_bodystructure(_parsear_bodystructure(texto))
            tiene_adjuntos = any(hoja.es_adjunto for hoja in hojas)

            yield MensajeResumen(
                id=str(uid),
                internet_message_id=message_id or _formato_cursor(uidvalidity, uid),
                remitente=remitente,
                asunto=asunto,
                recibido_en=recibido_en,
                tiene_adjuntos=tiene_adjuntos,
                cursor_proveedor=_formato_cursor(uidvalidity, uid),
            )

    def listar_adjuntos_metadata(self, id_mensaje: str) -> list[AdjuntoMetadata]:
        uid = int(id_mensaje)
        texto, _ = self._fetch_uno(uid, "BODYSTRUCTURE")
        hojas = _particionar_bodystructure(_parsear_bodystructure(texto))
        return [
            AdjuntoMetadata(
                id=hoja.numero,
                nombre=hoja.nombre or "",
                tamano=hoja.tamano,
                es_en_linea=hoja.es_en_linea,
                # Repurposed for this provider: there is no OData type
                # annotation over IMAP, so this carries the part's own
                # MIME type (e.g. "application/pdf") instead -- nothing
                # outside this module's own `obtener_mensaje_completo`
                # reads this field for an IMAP-built `AdjuntoMetadata`.
                tipo_odata=f"{hoja.tipo.lower()}/{hoja.subtipo.lower()}",
            )
            for hoja in hojas
            if hoja.es_adjunto
        ]

    def obtener_mensaje_completo(
        self, id_mensaje: str, metadatos_adjuntos: Sequence[AdjuntoMetadata]
    ) -> MensajeCompleto:
        uid = int(id_mensaje)
        texto, _ = self._fetch_uno(uid, "BODYSTRUCTURE")
        hojas = {hoja.numero: hoja for hoja in _particionar_bodystructure(_parsear_bodystructure(texto))}

        cuerpo_html = ""
        candidatos_cuerpo = [h for h in hojas.values() if not h.es_adjunto]
        cuerpo_elegido = next((h for h in candidatos_cuerpo if h.subtipo == "HTML"), None) or (
            candidatos_cuerpo[0] if candidatos_cuerpo else None
        )
        if cuerpo_elegido is not None:
            _, literal = self._fetch_uno(uid, f"BODY.PEEK[{cuerpo_elegido.numero}]")
            crudo = _decodificar_contenido(literal or b"", cuerpo_elegido.codificacion)
            cuerpo_html = crudo.decode(cuerpo_elegido.charset, errors="replace")

        adjuntos: list[AdjuntoDescargado] = []
        for metadato in metadatos_adjuntos:
            if metadato.es_en_linea:
                continue
            hoja = hojas.get(metadato.id)
            if hoja is None:
                continue
            _, literal = self._fetch_uno(uid, f"BODY.PEEK[{hoja.numero}]")
            contenido = _decodificar_contenido(literal or b"", hoja.codificacion)
            adjuntos.append(AdjuntoDescargado(nombre=metadato.nombre or "adjunto", contenido=contenido))

        return MensajeCompleto(cuerpo_html=cuerpo_html, adjuntos=tuple(adjuntos))
