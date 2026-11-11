"""The IMAP provider -- read-only, UID-addressed, satisfying `proveedor.Proveedor`
exactly the way `GraphClient` does, with no change to that shape.

NEVER CHANGE THE MAILBOX: this module opens every folder it reads with
`EXAMINE` (via `select(..., readonly=True)`), never `SELECT`; every fetch
of a message's content uses `BODY.PEEK[...]`, never `BODY[...]`, so
nothing is ever marked `\\Seen`; and there is no method anywhere in this
class that sends `STORE`, `COPY`, `MOVE` or `EXPUNGE` -- `tests/
test_cliente_imap.py` proves both from the outside, against a fake server
that records every command it receives, for every folder it examines, not
only `INBOX`.

BUZON-FUENTE-IMAP-CARPETAS-1: this provider reads every selectable folder,
not only `INBOX` -- `legal@duna.solutions` files invoices and contracts
into folders by Zoho rule, and a connector that only looked at `INBOX`
would miss them, unlike the Microsoft source (`graph.py`), whose
`/me/messages` already reaches every folder. A folder is skipped when:
`\\Noselect` is present (the server itself says it cannot be opened), or
one of the SPECIAL-USE attributes `\\Trash`, `\\Junk`, `\\Sent`,
`\\Drafts`, `\\All` (RFC 6154) is present. When a folder carries NO
SPECIAL-USE attribute at all -- not even a different one, like `\\Archive`
or `\\Flagged`, which would prove the server already answered the
question -- `_NOMBRES_EXCLUIDOS` excludes it by name instead. That name
list is NOT mirrored from the
Microsoft source: `graph.py`'s own docstring and `BUZON-FUENTE-IMAP-1`'s
open follow-up (`DECISIONS.md`) both record that `/me/messages` excludes
no folder at all, by name or otherwise, so there was nothing to cite --
`_NOMBRES_EXCLUIDOS` is this provider's own, built from the names Zoho,
Gmail and Outlook actually use for Trash/Junk/Sent/Drafts/All-Mail.

The cursor is now per folder: `(UIDVALIDITY, UID)` for each folder kept,
since UID is only unique within one folder and UIDVALIDITY changing means
the server reset that folder's own UID numbering, never another folder's.
The resume token this provider sets on `graph.MensajeResumen.
cursor_proveedor` is a JSON object mapping folder name to that folder's own
`"<uidvalidity>:<uid>"` pair -- opaque to everything outside this module,
exactly like the single pair was before this slice. `cursor_proveedor_inicial`
also accepts that OLD single-pair format (no folder could use it but
`INBOX`, since that was the only folder this provider ever read before this
slice) and reads it as `{"INBOX": "<uidvalidity>:<uid>"}`, so a source
already synced under `BUZON-FUENTE-IMAP-1` resumes exactly where `INBOX`
left off instead of re-scanning it. A folder whose UIDVALIDITY changes
restarts from the pass's own date cursor, never from UID zero -- the same
`_buscar_uids` fallback as before this slice, now evaluated once per
folder instead of once per connection.

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
import json
import quopri
import re
from base64 import b64decode
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from duna_buzon.graph import AdjuntoDescargado, AdjuntoMetadata, MensajeCompleto, MensajeResumen
from duna_buzon.proveedor_errores import ProveedorAutenticacionError, ProveedorErrorTransitorio

BUZON_PREDETERMINADO = "INBOX"

# RFC 6154 SPECIAL-USE attributes that mean "never read this folder as a
# source of financial mail": the three Microsoft itself names in `graph.py`'s
# own docstring as never containing new mail worth seeing (Sent, Drafts), a
# Trash/Junk folder (never financial mail, and -- for Trash on a server that
# does not purge -- a near-duplicate of whatever moved there from a kept
# folder), and `\All` (a server's own all-mail view, e.g. Gmail's "All Mail",
# which would otherwise re-surface every message a kept folder already
# yields).
_BANDERAS_EXCLUIDAS = frozenset(b.casefold() for b in ("\\Trash", "\\Junk", "\\Sent", "\\Drafts", "\\All"))
_BANDERA_NO_SELECCIONABLE = "\\noselect"

# Every SPECIAL-USE attribute RFC 6154 (plus Gmail's own `\Important`)
# defines, not only the five excluded above -- so that a folder the server
# marks `\Archive` or `\Flagged` is recognised as "SPECIAL-USE IS present,
# just not one of the excluded kinds" and never falls through to the name
# check below, which would otherwise be guessing about a folder the server
# already told this provider the real answer for.
_ATRIBUTOS_SPECIAL_USE_CONOCIDOS = frozenset(
    a.casefold() for a in ("\\All", "\\Archive", "\\Drafts", "\\Flagged", "\\Important", "\\Junk", "\\Sent", "\\Trash")
)

# The SPECIAL-USE fallback: names this provider itself treats as Trash/Junk/
# Sent/Drafts/All-Mail when a folder's LIST response carries none of
# `_BANDERAS_EXCLUIDAS` at all -- NOT mirrored from the Microsoft source,
# which excludes no folder by name (see this module's own docstring). Drawn
# from the default English folder names Zoho (today's one IMAP source),
# Gmail and Outlook/Exchange actually use.
_NOMBRES_EXCLUIDOS = frozenset(
    n.casefold()
    for n in (
        "Trash",
        "Deleted Items",
        "Deleted Messages",
        "Junk",
        "Junk E-mail",
        "Junk Email",
        "Spam",
        "Sent",
        "Sent Items",
        "Sent Messages",
        "Drafts",
        "All Mail",
    )
)

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

    def list(self, directory: str = '""', pattern: str = "*") -> tuple[str, list]: ...

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


def _parsear_linea_list(texto: str) -> tuple[list[str], str] | None:
    """One `LIST` response line: `(<flags>) "<delimiter>" <name>`, where
    `<name>` (and the delimiter) may be a quoted string or an atom (`NIL`
    for the delimiter on a server that has none). Reuses the same
    parenthesized-list/string/atom grammar `_parsear_valor` already speaks
    for `BODYSTRUCTURE`, since IMAP's flag list is exactly that grammar.
    Returns `None` for a line this provider cannot make sense of -- caller
    skips it rather than failing the whole `LIST`."""
    texto = texto.strip()
    if not texto.startswith("("):
        return None
    try:
        banderas, pos = _parsear_lista(texto, 0)
        pos = _saltar_espacios(texto, pos)
        _, pos = _parsear_valor(texto, pos)  # the delimiter -- unused here
        pos = _saltar_espacios(texto, pos)
        if pos >= len(texto):
            return None
        nombre, _ = _parsear_valor(texto, pos)
    except IndexError:
        return None
    if not isinstance(nombre, str):
        return None
    return [str(b) for b in banderas if b is not None], nombre


def _carpeta_excluida(banderas: Sequence[str], nombre: str) -> bool:
    banderas_cf = {b.casefold() for b in banderas}
    if _BANDERA_NO_SELECCIONABLE in banderas_cf:
        return True
    if banderas_cf & _BANDERAS_EXCLUIDAS:
        return True
    if banderas_cf & _ATRIBUTOS_SPECIAL_USE_CONOCIDOS:
        # A DIFFERENT known SPECIAL-USE attribute (e.g. `\Archive`) -- the
        # server already answered the question, so this folder is kept
        # regardless of what it happens to be named.
        return False
    return nombre.casefold() in _NOMBRES_EXCLUIDOS


def _entrecomillar_imap(nombre: str) -> str:
    """A mailbox name as an IMAP quoted string, escaping the two characters
    that grammar reserves (`\\` and `"`) -- always quoted, even for a plain
    name like `INBOX`, since quoting a name that needed none is always
    valid and this provider never needs to tell the two cases apart."""
    escapado = nombre.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escapado}"'


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


def _formato_par(uidvalidity: int, uid: int) -> str:
    return f"{uidvalidity}:{uid}"


def _analizar_par(valor: str) -> tuple[int, int] | None:
    try:
        uidvalidity_str, uid_str = valor.split(":", 1)
        return int(uidvalidity_str), int(uid_str)
    except ValueError:
        return None


def _analizar_cursor_multi(cursor: str | None) -> dict[str, tuple[int, int]]:
    """The provider's own opaque resume token, parsed into one `(UIDVALIDITY,
    UID)` pair per folder. Accepts the JSON object this provider itself
    writes (`_formato_cursor_multi`) and, for a source that synced under
    `BUZON-FUENTE-IMAP-1` before this slice, that earlier single-pair
    `"<uidvalidity>:<uid>"` string too -- read as `INBOX`'s own cursor,
    since `INBOX` was the only folder that format could ever have come
    from. Anything else unreadable is an empty map: every folder falls
    back to the date cursor, exactly like a folder with no prior cursor
    always has."""
    if cursor is None:
        return {}
    try:
        datos = json.loads(cursor)
    except json.JSONDecodeError:
        par = _analizar_par(cursor)
        return {BUZON_PREDETERMINADO: par} if par is not None else {}
    if not isinstance(datos, dict):
        return {}
    resultado: dict[str, tuple[int, int]] = {}
    for carpeta, valor in datos.items():
        if isinstance(valor, str):
            par = _analizar_par(valor)
            if par is not None:
                resultado[str(carpeta)] = par
    return resultado


def _formato_cursor_multi(mapa: dict[str, tuple[int, int]]) -> str:
    return json.dumps({carpeta: _formato_par(*par) for carpeta, par in mapa.items()}, sort_keys=True)


class ClienteImap:
    def __init__(
        self,
        *,
        host: str,
        puerto: int,
        usuario: str,
        password: str,
        cursor_proveedor_inicial: str | None = None,
        construir_conexion: Callable[[str, int], ConexionImapProtocolo] = _construir_conexion_ssl_real,
    ) -> None:
        self._host = host
        self._puerto = puerto
        self._usuario = usuario
        self._password = password
        self._cursor_inicial = cursor_proveedor_inicial
        self._cursor_inicial_mapa = _analizar_cursor_multi(cursor_proveedor_inicial)
        self._construir_conexion = construir_conexion
        self._conexion: ConexionImapProtocolo | None = None
        self._carpeta_actual: str | None = None

    def cerrar(self) -> None:
        if self._conexion is None:
            return
        try:
            self._conexion.logout()
        except Exception:
            pass
        self._conexion = None
        self._carpeta_actual = None

    def _conexion_activa(self) -> ConexionImapProtocolo:
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

        self._conexion = conexion
        return conexion

    def _carpetas_a_examinar(self) -> list[str]:
        """Every folder this pass should `EXAMINE`: the server's own `LIST`
        of selectable folders, minus `\\Noselect` and whichever SPECIAL-USE
        attribute or (failing that) name marks Trash/Junk/Sent/Drafts/
        All-Mail -- see this module's own docstring. Sorted for a
        deterministic, repeatable order across passes; a line this
        provider cannot parse is skipped, not fatal, since one odd entry
        from a server must never stop every other folder from being read."""
        conexion = self._conexion_activa()
        typ, dat = conexion.list()
        if typ != "OK":
            raise ClienteImapErrorTransitorio("list", detalle=typ)
        carpetas: list[str] = []
        for entrada in dat or []:
            if entrada is None:
                continue
            texto = entrada.decode("utf-8", errors="replace") if isinstance(entrada, bytes) else str(entrada)
            analizada = _parsear_linea_list(texto)
            if analizada is None:
                continue
            banderas, nombre = analizada
            if _carpeta_excluida(banderas, nombre):
                continue
            carpetas.append(nombre)
        return sorted(carpetas)

    def _examinar_carpeta(self, nombre: str) -> tuple[int, int | None]:
        conexion = self._conexion_activa()
        typ, _ = conexion.select(_entrecomillar_imap(nombre), readonly=True)
        if typ != "OK":
            raise ClienteImapErrorTransitorio("examinar", detalle=typ)

        typ_uv, dat_uv = conexion.response("UIDVALIDITY")
        if typ_uv != "UIDVALIDITY" or not dat_uv or dat_uv[0] is None:
            raise ClienteImapErrorTransitorio("uidvalidity-ausente")
        typ_un, dat_un = conexion.response("UIDNEXT")
        uidnext = int(dat_un[0]) if typ_un == "UIDNEXT" and dat_un and dat_un[0] is not None else None

        self._carpeta_actual = nombre
        return int(dat_uv[0]), uidnext

    def _asegurar_carpeta_seleccionada(self) -> None:
        """`listar_adjuntos_metadata`/`obtener_mensaje_completo` are called
        either right after `listar_mensajes` yielded a message (the real
        flow -- the right folder is already selected, so this is a no-op),
        or standalone (tests only), which this defaults to `INBOX` for,
        exactly like this provider only ever read before this slice."""
        if self._carpeta_actual is None:
            self._examinar_carpeta(BUZON_PREDETERMINADO)

    def _fetch_uno(self, uid: int, items: str) -> tuple[str, bytes | None]:
        self._asegurar_carpeta_seleccionada()
        conexion = self._conexion_activa()
        typ, dat = conexion.uid("FETCH", str(uid), f"({items})")
        if typ != "OK" or not dat or dat[0] is None:
            raise ClienteImapErrorTransitorio("fetch", detalle=typ)
        return _unir_respuesta_fetch(dat)

    def _buscar_uids(
        self,
        desde: datetime.datetime,
        *,
        cursor_previo: tuple[int, int] | None,
        uidvalidity: int,
        uidnext: int | None,
    ) -> list[int]:
        conexion = self._conexion_activa()

        if cursor_previo is not None and cursor_previo[0] == uidvalidity:
            uid_resumir = cursor_previo[1] + 1
            if uidnext is not None and uid_resumir >= uidnext:
                # Nothing can be new: issuing "N:*" here, with N at or
                # past UIDNEXT, is the well-known IMAP quirk where some
                # servers normalize an empty range by returning the LAST
                # message instead of none -- never issued.
                return []
            criterio: tuple[str, str] = ("UID", f"{uid_resumir}:*")
        else:
            # No usable cursor for THIS folder -- either it has never been
            # scanned, or its UIDVALIDITY changed -- so it restarts from
            # the pass's own date cursor, never from UID zero.
            criterio = ("SINCE", _formato_fecha_imap(desde))

        typ, dat = conexion.uid("SEARCH", *criterio)
        if typ != "OK":
            raise ClienteImapErrorTransitorio("buscar", detalle=typ)
        cadena = dat[0].decode("ascii", errors="replace") if dat and dat[0] else ""
        return sorted({int(tok) for tok in cadena.split()})

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]:
        mapa_cursor = dict(self._cursor_inicial_mapa)

        for nombre_carpeta in self._carpetas_a_examinar():
            uidvalidity, uidnext = self._examinar_carpeta(nombre_carpeta)
            cursor_previo = mapa_cursor.get(nombre_carpeta)

            for uid in self._buscar_uids(
                desde, cursor_previo=cursor_previo, uidvalidity=uidvalidity, uidnext=uidnext
            ):
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

                mapa_cursor[nombre_carpeta] = (uidvalidity, uid)

                yield MensajeResumen(
                    id=str(uid),
                    # Folder-qualified: two different folders can each have
                    # their own UID 1 under the same UIDVALIDITY, so the
                    # bare pair this fallback used before this slice (safe
                    # with only one folder) could collide across folders.
                    internet_message_id=message_id or f"{nombre_carpeta}:{_formato_par(uidvalidity, uid)}",
                    remitente=remitente,
                    asunto=asunto,
                    recibido_en=recibido_en,
                    tiene_adjuntos=tiene_adjuntos,
                    cursor_proveedor=_formato_cursor_multi(mapa_cursor),
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
