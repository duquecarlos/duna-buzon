"""AES-256-GCM at rest, for the MSAL token cache file --
`contracts/host-deploy-v1-amendment-sensitive-data.md` rule 2. A fresh nonce
on every write; the nonce travels with the ciphertext (prefix) since GCM
needs it again to decrypt, and reusing a key never reusing a nonce is the
one rule this module exists to enforce.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LONGITUD_NONCE_BYTES = 12
_MODO_DIRECTORIO = 0o700
_MODO_ARCHIVO = 0o600


class ClaveInvalidaError(Exception):
    """The configured key could not decrypt this ciphertext -- a wrong key,
    or data corrupted at rest."""


def cifrar(datos_claros: bytes, clave: bytes) -> bytes:
    """`nonce || ciphertext_con_tag`, a fresh `nonce` every call."""
    nonce = os.urandom(_LONGITUD_NONCE_BYTES)
    aesgcm = AESGCM(clave)
    return nonce + aesgcm.encrypt(nonce, datos_claros, None)


def descifrar(datos_cifrados: bytes, clave: bytes) -> bytes:
    nonce, texto_cifrado = (
        datos_cifrados[:_LONGITUD_NONCE_BYTES],
        datos_cifrados[_LONGITUD_NONCE_BYTES:],
    )
    aesgcm = AESGCM(clave)
    try:
        return aesgcm.decrypt(nonce, texto_cifrado, None)
    except InvalidTag as exc:
        raise ClaveInvalidaError(
            "BUZON_CLAVE_CIFRADO could not decrypt this file: wrong key, or the file is corrupted"
        ) from exc


def _asegurar_permisos_soportados() -> bool:
    # chmod is a no-op approximation on Windows; the mode bits below are
    # meaningless there, so tests skip the exact-mode assertion on that
    # platform, with a reason, per the slice's own test list.
    return os.name == "posix"


def asegurar_directorio_datos(ruta: Path) -> None:
    ruta.mkdir(parents=True, exist_ok=True)
    if _asegurar_permisos_soportados():
        os.chmod(ruta, _MODO_DIRECTORIO)


def escribir_archivo_sensible(ruta: Path, contenido: bytes) -> None:
    """Writes `contenido` to `ruta`, creating it mode 0600 from the start
    (never a window where a default-mode file briefly exists) where the OS
    supports it."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODO_ARCHIVO if os.name == "posix" else 0o666
    )
    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(contenido)
    finally:
        if _asegurar_permisos_soportados():
            os.chmod(ruta, _MODO_ARCHIVO)


def modo_octal(ruta: Path) -> int:
    return stat.S_IMODE(ruta.stat().st_mode)
