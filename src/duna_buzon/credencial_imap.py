"""Sign-in for one IMAP source -- an app password, read once from STDIN
with no echo (`getpass`), NEVER as a command-line argument, and verified
by a real `LOGIN` against the server before it is ever written to disk.
Mirrors `auth.py`'s `entrar`/`obtener_token` shape for the Microsoft
provider, but there is no token to refresh here: the same app password is
reused, silently, until Carlos rotates it by running `entrar` again.

`_verificar_login_real` is the ONE seam: production opens a real
`imaplib.IMAP4_SSL` connection; tests inject a fake that never reaches the
network, exactly like `auth.py`'s `construir_app`.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from pathlib import Path

from duna_buzon import cripto, temporizadores
from duna_buzon.config import Config
from duna_buzon.fuentes import NOMBRE_ARCHIVO_CREDENCIAL


class SinCredencialError(Exception):
    """No app password is stored yet for this source: run entrar."""


class VerificacionLoginError(Exception):
    """The real LOGIN this module performs before ever saving an app
    password failed -- a wrong password, a wrong host, or no network path
    to it. Carries only the exception's own class name, never the
    password."""


def _ruta_credencial(directorio_fuente: str) -> Path:
    return Path(directorio_fuente) / NOMBRE_ARCHIVO_CREDENCIAL


def _verificar_login_real(host: str, puerto: int, usuario: str, password: str) -> None:
    import imaplib

    try:
        # BUZON-GRAPH-TIMEOUT-1 -- explicit, never imaplib's own default.
        conexion = imaplib.IMAP4_SSL(host, puerto, timeout=temporizadores.TIMEOUT_IMAP_SEGUNDOS)
    except OSError as exc:
        raise VerificacionLoginError(type(exc).__name__) from exc
    try:
        conexion.login(usuario, password)
    except imaplib.IMAP4.error as exc:
        raise VerificacionLoginError(type(exc).__name__) from exc
    finally:
        try:
            conexion.logout()
        except Exception:
            pass


def entrar(
    config: Config,
    directorio_fuente: str,
    *,
    host: str,
    puerto: int,
    usuario: str,
    leer_password: Callable[[], str] = getpass.getpass,
    verificar_login: Callable[[str, int, str, str], None] = _verificar_login_real,
) -> None:
    """Reads the app password with no echo, verifies it with a real LOGIN,
    and only then encrypts and stores it -- a wrong password is never
    written to disk."""
    password = leer_password()
    verificar_login(host, puerto, usuario, password)
    cripto.asegurar_directorio_datos(Path(directorio_fuente))
    cripto.escribir_archivo_sensible(
        _ruta_credencial(directorio_fuente),
        cripto.cifrar(password.encode("utf-8"), config.clave_cifrado),
    )


def obtener_credencial(config: Config, directorio_fuente: str) -> str:
    """`sincronizar`'s own entry point for ONE IMAP source: decrypts and
    returns the stored app password. Raises `SinCredencialError` with "run
    entrar" when none is stored yet."""
    ruta = _ruta_credencial(directorio_fuente)
    if not ruta.is_file():
        raise SinCredencialError("no app password is stored: run entrar")
    cifrado = ruta.read_bytes()
    if not cifrado:
        raise SinCredencialError("no app password is stored: run entrar")
    return cripto.descifrar(cifrado, config.clave_cifrado).decode("utf-8")
