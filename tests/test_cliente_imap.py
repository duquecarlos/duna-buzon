"""`ClienteImap` against an in-process fake IMAP server -- never a real
network, never a real mailbox. The fake server records every command it
receives, so these tests can prove from the outside that this client only
ever sends `EXAMINE` (never `SELECT`), `BODY.PEEK[...]` (never
`BODY[...]`), and no `STORE`/`COPY`/`MOVE`/`EXPUNGE` command at all.
"""

from __future__ import annotations

import datetime
import re
import socket
import threading
from dataclasses import dataclass, field

import pytest

from duna_buzon.cliente_imap import (
    ClienteImap,
    ClienteImapAutenticacionError,
    ClienteImapErrorTransitorio,
)
from duna_buzon.graph import AdjuntoMetadata

_MESES_INDICE = {
    mes: i + 1
    for i, mes in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    )
}


@dataclass
class _ParteFalsa:
    tipo: str
    subtipo: str
    contenido: bytes
    parametros: dict[str, str] = field(default_factory=dict)
    disposicion: tuple[str, dict[str, str]] | None = None
    codificacion: str = "7BIT"


@dataclass
class _MensajeFalso:
    uid: int
    internaldate: str
    headers: bytes
    partes: list[_ParteFalsa]


def _mensaje_simple(
    uid: int,
    *,
    internaldate: str = "01-Mar-2026 10:00:00 +0000",
    remitente: str = "a@b.com",
    asunto: str = "hola",
    message_id: str | None = None,
    cuerpo: bytes = b"hola mundo",
) -> _MensajeFalso:
    mid = message_id or f"<m{uid}@mail.example>"
    headers = f"From: {remitente}\r\nSubject: {asunto}\r\nMessage-Id: {mid}\r\n\r\n".encode("utf-8")
    return _MensajeFalso(
        uid=uid,
        internaldate=internaldate,
        headers=headers,
        partes=[_ParteFalsa("TEXT", "PLAIN", cuerpo, parametros={"CHARSET": "utf-8"})],
    )


def _mensaje_con_adjunto(
    uid: int,
    *,
    internaldate: str = "01-Mar-2026 10:00:00 +0000",
    remitente: str = "a@b.com",
    asunto: str = "factura",
    nombre_adjunto: str = "factura.zip",
    contenido_adjunto: bytes = b"PK\x03\x04contenido-zip",
    cuerpo: bytes = b"ver adjunto",
    adjunto_en_linea: bool = False,
) -> _MensajeFalso:
    headers = f"From: {remitente}\r\nSubject: {asunto}\r\nMessage-Id: <m{uid}@mail.example>\r\n\r\n".encode(
        "utf-8"
    )
    disposicion = ("INLINE" if adjunto_en_linea else "ATTACHMENT", {"FILENAME": nombre_adjunto})
    return _MensajeFalso(
        uid=uid,
        internaldate=internaldate,
        headers=headers,
        partes=[
            _ParteFalsa("TEXT", "PLAIN", cuerpo, parametros={"CHARSET": "utf-8"}),
            _ParteFalsa(
                "APPLICATION",
                "ZIP",
                contenido_adjunto,
                parametros={"NAME": nombre_adjunto},
                disposicion=disposicion,
                codificacion="BASE64",
            ),
        ],
    )


def _bodystructure_parte(parte: _ParteFalsa) -> str:
    if parte.parametros:
        params_str = "(" + " ".join(f'"{k}" "{v}"' for k, v in parte.parametros.items()) + ")"
    else:
        params_str = "NIL"
    disp_str = "NIL"
    if parte.disposicion:
        disp_tipo, disp_params = parte.disposicion
        disp_params_str = " ".join(f'"{k}" "{v}"' for k, v in disp_params.items())
        disp_str = f'("{disp_tipo}" ({disp_params_str}))'
    lineas = " 1" if parte.tipo.upper() == "TEXT" else ""
    return (
        f'("{parte.tipo}" "{parte.subtipo}" {params_str} NIL NIL "{parte.codificacion}" '
        f"{len(parte.contenido)}{lineas} NIL {disp_str} NIL NIL)"
    )


def _bodystructure_mensaje(partes: list[_ParteFalsa]) -> str:
    if len(partes) == 1:
        return _bodystructure_parte(partes[0])
    hijos = "".join(_bodystructure_parte(p) for p in partes)
    return f'({hijos} "MIXED")'


class _ServidorImapFalso:
    """A real TCP server speaking just enough IMAP4 to drive `ClienteImap`
    -- one connection, one thread, closed with the `with` block. Every
    command line it receives is recorded in `comandos`, in order."""

    def __init__(
        self,
        mensajes: list[_MensajeFalso],
        *,
        uidvalidity: int = 1000,
        uidnext: int | None = None,
        fallar_login: bool = False,
    ) -> None:
        self.mensajes = {m.uid: m for m in mensajes}
        self.uidvalidity = uidvalidity
        self.uidnext = uidnext if uidnext is not None else (max(self.mensajes, default=0) + 1)
        self.fallar_login = fallar_login
        self.comandos: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self.puerto = self._sock.getsockname()[1]
        self._sock.listen(1)
        self._hilo = threading.Thread(target=self._atender, daemon=True)
        self._hilo.start()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    def __enter__(self) -> "_ServidorImapFalso":
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._hilo.join(timeout=2)

    def _atender(self) -> None:
        try:
            conexion, _ = self._sock.accept()
        except OSError:
            return
        archivo = conexion.makefile("rwb")

        def enviar_texto(linea: str) -> None:
            archivo.write(linea.encode("utf-8") + b"\r\n")
            archivo.flush()

        def enviar_bytes(datos: bytes) -> None:
            archivo.write(datos)
            archivo.flush()

        try:
            enviar_texto("* OK fake imap ready")
            while True:
                linea = archivo.readline()
                if not linea:
                    break
                texto = linea.decode("utf-8", errors="replace").rstrip("\r\n")
                self.comandos.append(texto)
                tag, _, resto = texto.partition(" ")
                comando, _, resto2 = resto.partition(" ")
                comando = comando.upper()

                if comando == "CAPABILITY":
                    enviar_texto("* CAPABILITY IMAP4rev1")
                    enviar_texto(f"{tag} OK CAPABILITY completed")
                elif comando == "LOGIN":
                    if self.fallar_login:
                        enviar_texto(f"{tag} NO LOGIN failed")
                    else:
                        enviar_texto(f"{tag} OK LOGIN completed")
                elif comando in ("EXAMINE", "SELECT"):
                    enviar_texto(f"* {len(self.mensajes)} EXISTS")
                    enviar_texto(f"* OK [UIDVALIDITY {self.uidvalidity}] UIDs valid")
                    enviar_texto(f"* OK [UIDNEXT {self.uidnext}] Predicted next UID")
                    etiqueta = "READ-ONLY" if comando == "EXAMINE" else "READ-WRITE"
                    enviar_texto(f"{tag} OK [{etiqueta}] {comando} completed")
                elif comando == "UID":
                    self._manejar_uid(tag, resto2, enviar_texto, enviar_bytes)
                elif comando == "LOGOUT":
                    enviar_texto("* BYE logout")
                    enviar_texto(f"{tag} OK LOGOUT completed")
                    break
                else:
                    enviar_texto(f"{tag} BAD unknown command")
        finally:
            conexion.close()

    def _manejar_uid(self, tag: str, resto: str, enviar_texto, enviar_bytes) -> None:
        sub, _, resto2 = resto.partition(" ")
        sub = sub.upper()
        if sub == "SEARCH":
            uids = self._buscar(resto2)
            enviar_texto("* SEARCH " + " ".join(str(u) for u in uids))
            enviar_texto(f"{tag} OK UID SEARCH completed")
        elif sub == "FETCH":
            uid_str, _, items = resto2.partition(" ")
            self._fetch(tag, int(uid_str), items, enviar_texto, enviar_bytes)
        else:
            enviar_texto(f"{tag} BAD unknown UID command")

    def _buscar(self, criterio: str) -> list[int]:
        primero, _, resto = criterio.partition(" ")
        primero = primero.upper()
        if primero == "ALL":
            return sorted(self.mensajes)
        if primero == "SINCE":
            fecha = self._parsear_fecha(resto.strip())
            return sorted(
                uid
                for uid, mensaje in self.mensajes.items()
                if self._fecha_de(mensaje.internaldate) >= fecha
            )
        if primero == "UID":
            inicio_str = resto.split(":")[0]
            inicio = int(inicio_str)
            return sorted(uid for uid in self.mensajes if uid >= inicio)
        return []

    @staticmethod
    def _parsear_fecha(cadena: str) -> datetime.date:
        dia, mes_str, anio = cadena.split("-")
        return datetime.date(int(anio), _MESES_INDICE[mes_str], int(dia))

    @classmethod
    def _fecha_de(cls, internaldate: str) -> datetime.date:
        return cls._parsear_fecha(internaldate.split(" ")[0])

    def _fetch(self, tag: str, uid: int, items: str, enviar_texto, enviar_bytes) -> None:
        mensaje = self.mensajes.get(uid)
        if mensaje is None:
            enviar_texto(f"{tag} OK UID FETCH completed")
            return

        quiere_uid = "UID" in items
        quiere_internaldate = "INTERNALDATE" in items
        quiere_bodystructure = "BODYSTRUCTURE" in items
        quiere_header = "HEADER.FIELDS" in items
        coincidencia_seccion = re.search(r"BODY\.PEEK\[([0-9.]+)\]", items)

        prefijo_partes = []
        if quiere_uid:
            prefijo_partes.append(f"UID {uid}")
        if quiere_internaldate:
            prefijo_partes.append(f'INTERNALDATE "{mensaje.internaldate}"')
        prefijo = f"* {uid} FETCH (" + " ".join(prefijo_partes)

        if quiere_header:
            literal = mensaje.headers
            if prefijo_partes:
                prefijo += " "
            prefijo += f"BODY[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)] {{{len(literal)}}}"
            enviar_bytes(prefijo.encode("utf-8") + b"\r\n")
            enviar_bytes(literal)
            sufijo = (
                f" BODYSTRUCTURE {_bodystructure_mensaje(mensaje.partes)})"
                if quiere_bodystructure
                else ")"
            )
            enviar_bytes(sufijo.encode("utf-8") + b"\r\n")
        elif coincidencia_seccion:
            literal = self._contenido_parte(mensaje, coincidencia_seccion.group(1))
            if prefijo_partes:
                prefijo += " "
            prefijo += f"BODY[{coincidencia_seccion.group(1)}] {{{len(literal)}}}"
            enviar_bytes(prefijo.encode("utf-8") + b"\r\n")
            enviar_bytes(literal)
            enviar_bytes(b")\r\n")
        else:
            if quiere_bodystructure:
                if prefijo_partes:
                    prefijo += " "
                prefijo += f"BODYSTRUCTURE {_bodystructure_mensaje(mensaje.partes)}"
            enviar_bytes((prefijo + ")").encode("utf-8") + b"\r\n")

        enviar_texto(f"{tag} OK UID FETCH completed")

    @staticmethod
    def _contenido_parte(mensaje: _MensajeFalso, numero: str) -> bytes:
        parte = mensaje.partes[0] if len(mensaje.partes) == 1 else mensaje.partes[int(numero) - 1]
        if parte.codificacion == "BASE64":
            import base64

            return base64.b64encode(parte.contenido)
        return parte.contenido


def _conectar_a(servidor: _ServidorImapFalso):
    import imaplib

    def construir(host: str, puerto: int):
        return imaplib.IMAP4(servidor.host, servidor.puerto)

    return construir


def _cliente(servidor: _ServidorImapFalso, *, cursor_proveedor_inicial: str | None = None) -> ClienteImap:
    return ClienteImap(
        host=servidor.host,
        puerto=servidor.puerto,
        usuario="legal@duna.solutions",
        password="app-password",
        cursor_proveedor_inicial=cursor_proveedor_inicial,
        construir_conexion=_conectar_a(servidor),
    )


_DESDE = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Read-only, by construction: EXAMINE never SELECT, BODY.PEEK never BODY,
# and no STORE/COPY/MOVE/EXPUNGE anywhere this client talks.
# ---------------------------------------------------------------------------


def test_examine_nunca_select_y_ningun_comando_de_escritura() -> None:
    mensajes = [_mensaje_con_adjunto(1)]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        list(cliente.listar_mensajes(_DESDE))
        metadatos = cliente.listar_adjuntos_metadata("1")
        cliente.obtener_mensaje_completo("1", metadatos)
        cliente.cerrar()

    verbos = [c.partition(" ")[2].split(" ")[0].upper() for c in servidor.comandos]
    assert "EXAMINE" in verbos
    assert "SELECT" not in verbos
    for prohibido in ("STORE", "COPY", "MOVE", "EXPUNGE"):
        assert prohibido not in verbos
    # Every content fetch uses BODY.PEEK[...], never a bare BODY[...].
    comandos_fetch = [c for c in servidor.comandos if " UID FETCH " in c]
    assert comandos_fetch, "no UID FETCH command was ever sent"
    for comando in comandos_fetch:
        if "BODY.PEEK[" in comando or "BODY[" not in comando:
            continue
        pytest.fail(f"a FETCH asked for BODY[...] without .PEEK: {comando}")


def test_cerrar_sin_conectar_nunca_es_un_error() -> None:
    # __init__ does no I/O at all -- closing a client that was never used
    # must be a safe no-op (so `construir_proveedor` + `cerrar()` never
    # raises outside `sincronizar.py`'s own try/except).
    cliente = ClienteImap(
        host="127.0.0.1", puerto=1, usuario="x", password="y", construir_conexion=lambda h, p: None
    )
    cliente.cerrar()


# ---------------------------------------------------------------------------
# Listing, metadata and content.
# ---------------------------------------------------------------------------


def test_listar_mensajes_simple_sin_adjuntos() -> None:
    mensajes = [_mensaje_simple(1, remitente="notificaciones@banco-ejemplo.com", asunto="factura")]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert len(resultado) == 1
    mensaje = resultado[0]
    assert mensaje.id == "1"
    assert mensaje.remitente == "notificaciones@banco-ejemplo.com"
    assert mensaje.asunto == "factura"
    assert mensaje.tiene_adjuntos is False
    assert mensaje.recibido_en == datetime.datetime(2026, 3, 1, 10, 0, tzinfo=datetime.UTC)
    assert mensaje.internet_message_id == "<m1@mail.example>"
    assert mensaje.cursor_proveedor == "1000:1"


def test_listar_mensajes_con_adjunto_marca_tiene_adjuntos() -> None:
    mensajes = [_mensaje_con_adjunto(1)]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert resultado[0].tiene_adjuntos is True


def test_listar_adjuntos_metadata_y_obtener_mensaje_completo() -> None:
    mensajes = [
        _mensaje_con_adjunto(
            1, nombre_adjunto="factura.zip", contenido_adjunto=b"PK\x03\x04contenido-zip", cuerpo=b"ver adjunto"
        )
    ]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        metadatos = cliente.listar_adjuntos_metadata("1")
        assert len(metadatos) == 1
        assert metadatos[0].nombre == "factura.zip"
        assert metadatos[0].es_en_linea is False

        completo = cliente.obtener_mensaje_completo("1", metadatos)
        cliente.cerrar()

    assert completo.cuerpo_html == "ver adjunto"
    assert len(completo.adjuntos) == 1
    assert completo.adjuntos[0].nombre == "factura.zip"
    assert completo.adjuntos[0].contenido == b"PK\x03\x04contenido-zip"


def test_adjunto_en_linea_se_lista_pero_nunca_se_descarga() -> None:
    mensajes = [_mensaje_con_adjunto(1, nombre_adjunto="logo.png", adjunto_en_linea=True)]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        metadatos = cliente.listar_adjuntos_metadata("1")
        assert metadatos[0].es_en_linea is True

        completo = cliente.obtener_mensaje_completo("1", metadatos)
        comandos_antes_de_cerrar = len(servidor.comandos)
        cliente.cerrar()

    # The inline part's own BODY.PEEK[<n>] is never fetched.
    assert not completo.adjuntos
    comandos_fetch_seccion = [
        c for c in servidor.comandos[:comandos_antes_de_cerrar] if "BODY.PEEK[2]" in c
    ]
    assert not comandos_fetch_seccion


def test_mensaje_mas_antiguo_que_desde_se_descarta_en_el_filtro_since() -> None:
    # The SINCE fallback only has day granularity -- a message from the
    # SAME day but before the exact `desde` timestamp must still be
    # excluded, exactly like Graph's own precise `$filter` would exclude
    # it.
    desde = datetime.datetime(2026, 3, 1, 9, 0, tzinfo=datetime.UTC)
    mensajes = [_mensaje_simple(1, internaldate="01-Mar-2026 08:00:00 +0000")]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        resultado = list(cliente.listar_mensajes(desde))
        cliente.cerrar()

    assert resultado == []


# ---------------------------------------------------------------------------
# The cursor: (UIDVALIDITY, UID).
# ---------------------------------------------------------------------------


def test_sin_cursor_previo_usa_since_por_fecha() -> None:
    mensajes = [_mensaje_simple(1), _mensaje_simple(2, internaldate="02-Mar-2026 10:00:00 +0000")]
    with _ServidorImapFalso(mensajes, uidvalidity=1000) as servidor:
        cliente = _cliente(servidor, cursor_proveedor_inicial=None)
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert {m.id for m in resultado} == {"1", "2"}
    assert any("UID SEARCH SINCE" in c for c in servidor.comandos)


def test_cursor_con_misma_uidvalidity_reanuda_por_uid() -> None:
    mensajes = [
        _mensaje_simple(1, internaldate="01-Mar-2026 10:00:00 +0000"),
        _mensaje_simple(2, internaldate="02-Mar-2026 10:00:00 +0000"),
        _mensaje_simple(3, internaldate="03-Mar-2026 10:00:00 +0000"),
    ]
    with _ServidorImapFalso(mensajes, uidvalidity=1000, uidnext=4) as servidor:
        cliente = _cliente(servidor, cursor_proveedor_inicial="1000:1")
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert {m.id for m in resultado} == {"2", "3"}
    assert any("UID SEARCH UID 2:*" in c for c in servidor.comandos)
    assert not any("SINCE" in c for c in servidor.comandos)


def test_cursor_al_dia_no_emite_ninguna_busqueda() -> None:
    mensajes = [_mensaje_simple(1)]
    with _ServidorImapFalso(mensajes, uidvalidity=1000, uidnext=2) as servidor:
        cliente = _cliente(servidor, cursor_proveedor_inicial="1000:1")
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert resultado == []
    assert not any("UID SEARCH" in c for c in servidor.comandos)


def test_uidvalidity_distinta_cae_de_nuevo_a_since() -> None:
    mensajes = [_mensaje_simple(1)]
    with _ServidorImapFalso(mensajes, uidvalidity=2000, uidnext=2) as servidor:
        # The stored cursor names a UIDVALIDITY the server no longer has
        # -- it must be treated as unusable, never blindly resumed from.
        cliente = _cliente(servidor, cursor_proveedor_inicial="1000:50")
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert {m.id for m in resultado} == {"1"}
    assert any("UID SEARCH SINCE" in c for c in servidor.comandos)
    assert resultado[0].cursor_proveedor == "2000:1"


# ---------------------------------------------------------------------------
# Failures: lazy connect, authentication, transient.
# ---------------------------------------------------------------------------


def test_login_fallido_es_autenticacion_y_nunca_conecta_en_init() -> None:
    mensajes = [_mensaje_simple(1)]
    with _ServidorImapFalso(mensajes, fallar_login=True) as servidor:
        cliente = _cliente(servidor)
        # __init__ above did not raise -- the failure only surfaces once
        # the generator is actually iterated.
        with pytest.raises(ClienteImapAutenticacionError):
            list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()


def test_fallo_de_conexion_es_transitorio() -> None:
    def construir_que_falla(host: str, puerto: int):
        raise ConnectionRefusedError("no listener")

    cliente = ClienteImap(
        host="127.0.0.1",
        puerto=1,
        usuario="legal@duna.solutions",
        password="app-password",
        construir_conexion=construir_que_falla,
    )
    with pytest.raises(ClienteImapErrorTransitorio):
        list(cliente.listar_mensajes(_DESDE))
    cliente.cerrar()


def test_asunto_con_palabras_codificadas_se_decodifica() -> None:
    mensajes = [
        _MensajeFalso(
            uid=1,
            internaldate="01-Mar-2026 10:00:00 +0000",
            headers=(
                b"From: a@b.com\r\n"
                b"Subject: =?UTF-8?B?ZmFjdHVyYSBjb24gYWNlbnRvIG9wdGltbw==?=\r\n"
                b"Message-Id: <m1@mail.example>\r\n\r\n"
            ),
            partes=[_ParteFalsa("TEXT", "PLAIN", b"cuerpo")],
        )
    ]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        resultado = list(cliente.listar_mensajes(_DESDE))
        cliente.cerrar()

    assert resultado[0].asunto == "factura con acento optimo"


def test_mensaje_sin_adjuntos_satisface_metadata_vacia() -> None:
    mensajes = [_mensaje_simple(1)]
    with _ServidorImapFalso(mensajes) as servidor:
        cliente = _cliente(servidor)
        metadatos: list[AdjuntoMetadata] = cliente.listar_adjuntos_metadata("1")
        completo = cliente.obtener_mensaje_completo("1", metadatos)
        cliente.cerrar()

    assert metadatos == []
    assert completo.cuerpo_html == "hola mundo"
    assert completo.adjuntos == ()
