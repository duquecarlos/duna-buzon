"""Per-source layout and config -- `$BUZON_DATOS/fuentes/<id>/{fuente.json,
credencial.bin, cursor.json, reglas.json}`. Each source is independent: its
own credential, its own cursor, its own rules. `fuente.json` carries no
secret (the credential lives apart, in `credencial.bin`, encrypted exactly
like today's single `cache.bin` was) -- only the source's own identity and
whether it is active.

`token_finanzas_ref` names which Finanzas token this source posts with, for
a future where more than one person's sources share this connector; today
there is one person and one `FINANZAS_BUZON_TOKEN`, so every source's
`fuente.json` carries the same ref and `FinanzasClient` keeps reading the
token straight from `Config` (`BUZON-FUENTES-ESQUEMA-1`).

`origen` (`BUZON-FUENTES-PULL-1`) tells `cli.py` where this source's own
credential comes from: `"local"` (default, every source before this slice)
means the usual `entrar`/`credencial.bin` dance this module's own sibling
modules (`auth.py`, `credencial_imap.py`) already do; `"cuenta"` means this
source's identity and a fresh access token are PULLED from Cuenta Duna
every `sincronizar` pass (`cli.py`'s own `_pull_fuentes_cuenta`, against
`cuenta.py`) -- such a source has NO `credencial.bin` at all, since its
token is never written to disk, and its `fuente.json` is rewritten on every
successful pull rather than written once by `agregar-fuente`. Only
`"microsoft"` and `"google"` can ever be `origen="cuenta"` (the two
providers Cuenta Duna's own wire names); only `"microsoft"` and `"imap"`
can be `origen="local"` -- Carlos never signs this connector into a Google
account directly, only through Cuenta Duna's OAuth.

`carpetas` (`BUZON-IMAP-CARPETAS-INCLUIDAS-1`) narrows which folders an `imap`
source reads to a named allow-list, for the shared-mailbox case: a Zoho
login that exposes several SHARED MAILBOXES (not separate logins) alongside
the signed-in account's own personal folders, where only the shared ones
belong to Finanzas. Absent (the default, and every `fuente.json` written
before this slice) means today's behaviour exactly -- every selectable
folder minus Trash/Junk/Sent/Drafts/All-Mail, Carlos's own personal mail
included. See `cliente_imap.py`'s own docstring for how an entry matches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from duna_buzon import cripto

NOMBRE_SUBDIRECTORIO = "fuentes"
NOMBRE_ARCHIVO_FUENTE = "fuente.json"
NOMBRE_ARCHIVO_CREDENCIAL = "credencial.bin"

# "imap" is any generic IMAP mailbox (legal@duna.solutions on Zoho today);
# "google" is Gmail, reached ONLY through a Cuenta Duna-pulled token
# (origen="cuenta", never a local sign-in) -- the field accepts only these
# three spellings so a typo in a hand-edited fuente.json is caught here,
# not three steps later as "unknown provider".
ProveedorId = Literal["microsoft", "imap", "google"]
_PROVEEDORES_VALIDOS = frozenset({"microsoft", "imap", "google"})
_PROVEEDORES_CUENTA = frozenset({"microsoft", "google"})
_PROVEEDORES_LOCAL = frozenset({"microsoft", "imap"})

OrigenFuente = Literal["local", "cuenta"]
_ORIGENES_VALIDOS = frozenset({"local", "cuenta"})

ID_FUENTE_MIGRADA = "hotmail-carlos"


class FuenteInvalidaError(Exception):
    """A `fuente.json` is missing, malformed, or missing a required field,
    or names an id that does not exist under `fuentes/`."""


@dataclass(frozen=True)
class FuenteConfig:
    id: str
    proveedor: ProveedorId
    direccion: str
    etiqueta: str
    token_finanzas_ref: str
    activa: bool
    # Only meaningful for `proveedor == "imap"` -- `None` for every
    # Microsoft source, and required (validated below) for every IMAP one.
    host: str | None = None
    port: int | None = None
    # See this module's own docstring. Defaults to "local" so every
    # `fuente.json` written before this slice (no `origen` key at all)
    # keeps meaning exactly what it always meant.
    origen: OrigenFuente = "local"
    # Only meaningful for `proveedor == "imap"`. `None` (absent from
    # `fuente.json`) means today's behaviour exactly: every selectable
    # folder, minus Trash/Junk/Sent/Drafts/All-Mail. Present means ONLY
    # folders whose LIST name equals one of these entries or sits under one
    # of them in the server's own hierarchy -- still minus those same
    # excluded kinds (BUZON-IMAP-CARPETAS-INCLUIDAS-1, so a shared-mailbox
    # IMAP source never pulls in the signed-in account's own personal
    # folders alongside it).
    carpetas: tuple[str, ...] | None = None

    @property
    def etiqueta_registro(self) -> str:
        """`"<proveedor>:<id>"` -- this source's own identity for LOGS and
        status output only (`fuentes`, `estado`, a future per-source log
        line). Never sent to Finanzas: buzon-v1's wire `fuente` field is the
        CHANNEL, always `"correo"` for every mail source, never this value
        (`BUZON-FUENTE-WIRE-CORREO-1`, correcting `BUZON-FUENTES-ESQUEMA-1`,
        which sent this as the wire `fuente` and got it wrong)."""
        return f"{self.proveedor}:{self.id}"


def directorio_fuentes(directorio_datos: str) -> Path:
    return Path(directorio_datos) / NOMBRE_SUBDIRECTORIO


def directorio_fuente(directorio_datos: str, id_fuente: str) -> Path:
    return directorio_fuentes(directorio_datos) / id_fuente


def _ruta_fuente_json(directorio_datos: str, id_fuente: str) -> Path:
    return directorio_fuente(directorio_datos, id_fuente) / NOMBRE_ARCHIVO_FUENTE


def _fuente_desde_json(id_fuente: str, datos: dict) -> FuenteConfig:
    try:
        proveedor = datos["proveedor"]
        direccion = datos["direccion"]
        etiqueta = datos["etiqueta"]
        token_finanzas_ref = datos["token_finanzas_ref"]
        activa = datos["activa"]
    except KeyError as exc:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} is missing required field: {exc.args[0]}"
        ) from exc

    if proveedor not in _PROVEEDORES_VALIDOS:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} names an unknown provider: {proveedor!r}"
        )
    if "id" in datos and datos["id"] != id_fuente:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} carries a different 'id' inside it: {datos['id']!r}"
        )

    origen = datos.get("origen", "local")
    if origen not in _ORIGENES_VALIDOS:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} names an unknown origen: {origen!r}"
        )
    if origen == "cuenta" and proveedor not in _PROVEEDORES_CUENTA:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} has origen='cuenta' but proveedor {proveedor!r}, "
            "which Cuenta Duna never returns"
        )
    if origen == "local" and proveedor not in _PROVEEDORES_LOCAL:
        raise FuenteInvalidaError(
            f"fuente.json for {id_fuente!r} names proveedor {proveedor!r}, which only ever "
            "comes from Cuenta Duna (origen='cuenta')"
        )

    host: str | None = None
    port: int | None = None
    carpetas: tuple[str, ...] | None = None
    if proveedor == "imap":
        host = datos.get("host")
        port = datos.get("port")
        if not isinstance(host, str) or not host:
            raise FuenteInvalidaError(
                f"fuente.json for {id_fuente!r} is missing required field: host"
            )
        if not isinstance(port, int) or isinstance(port, bool):
            raise FuenteInvalidaError(
                f"fuente.json for {id_fuente!r} is missing required field: port"
            )
        if "carpetas" in datos and datos["carpetas"] is not None:
            valor_carpetas = datos["carpetas"]
            if (
                not isinstance(valor_carpetas, list)
                or not valor_carpetas
                or not all(isinstance(c, str) and c for c in valor_carpetas)
            ):
                raise FuenteInvalidaError(
                    f"fuente.json for {id_fuente!r} names an invalid carpetas: {valor_carpetas!r}"
                )
            carpetas = tuple(valor_carpetas)

    return FuenteConfig(
        id=id_fuente,
        proveedor=proveedor,
        direccion=str(direccion),
        etiqueta=str(etiqueta),
        token_finanzas_ref=str(token_finanzas_ref),
        activa=bool(activa),
        host=host,
        port=port,
        origen=origen,
        carpetas=carpetas,
    )


def cargar_fuente(directorio_datos: str, id_fuente: str) -> FuenteConfig:
    ruta = _ruta_fuente_json(directorio_datos, id_fuente)
    if not ruta.is_file():
        raise FuenteInvalidaError(f"no such source: {id_fuente}")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FuenteInvalidaError(f"fuente.json for {id_fuente!r} is not valid JSON") from exc
    return _fuente_desde_json(id_fuente, datos)


def guardar_fuente(directorio_datos: str, fuente: FuenteConfig) -> None:
    """Writes `fuente.json` under the same 0700/0600 discipline as every
    other file in the data directory, even though this one file carries no
    secret -- one uniform permission story is simpler than two."""
    ruta = _ruta_fuente_json(directorio_datos, fuente.id)
    contenido = json.dumps(
        {
            "id": fuente.id,
            "proveedor": fuente.proveedor,
            "direccion": fuente.direccion,
            "etiqueta": fuente.etiqueta,
            "token_finanzas_ref": fuente.token_finanzas_ref,
            "activa": fuente.activa,
            "host": fuente.host,
            "port": fuente.port,
            "origen": fuente.origen,
            "carpetas": list(fuente.carpetas) if fuente.carpetas is not None else None,
        }
    ).encode("utf-8")
    cripto.escribir_archivo_sensible(ruta, contenido)


def listar_fuentes(directorio_datos: str) -> list[FuenteConfig]:
    """Every source under `fuentes/`, sorted by id. An absent or empty
    `fuentes/` directory is zero sources, never an error."""
    base = directorio_fuentes(directorio_datos)
    if not base.is_dir():
        return []
    fuentes = []
    for hijo in sorted(base.iterdir(), key=lambda p: p.name):
        if not hijo.is_dir():
            continue
        if not (hijo / NOMBRE_ARCHIVO_FUENTE).is_file():
            continue
        fuentes.append(cargar_fuente(directorio_datos, hijo.name))
    return fuentes


def fuentes_activas(directorio_datos: str) -> list[FuenteConfig]:
    return [f for f in listar_fuentes(directorio_datos) if f.activa]


def id_fuente_cuenta(id_cuenta: str) -> str:
    """The local source id a Cuenta Duna source `id_cuenta` is kept under
    (`fuentes/cuenta-<id_cuenta>/`) -- keyed by Cuenta Duna's own id, per
    this slice's build instructions, so a revoked-then-reconnected source
    resumes the SAME cursor and rules rather than starting over under a
    different id."""
    return f"cuenta-{id_cuenta}"


def _direccion_desde_cache_msal(contenido: bytes) -> str:
    """Best-effort only: MSAL's own serialized cache is plain JSON with an
    `Account` map whose entries carry `username`. Never raises -- a cache
    that cannot be parsed this way just migrates with an empty `direccion`,
    which Carlos can fill in by hand; it is a record field, never read by
    any provider logic."""
    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(datos, dict):
        return ""
    cuentas = datos.get("Account")
    if not isinstance(cuentas, dict):
        return ""
    for cuenta in cuentas.values():
        if isinstance(cuenta, dict):
            username = cuenta.get("username")
            if isinstance(username, str) and username:
                return username
    return ""


def migrar_fuente_unica_si_necesario(directorio_datos: str, clave_cifrado: bytes) -> bool:
    """Idempotent, automatic: moves today's single-mailbox layout
    (`cache.bin`/`cursor.json`/`reglas.json`/`estado.json` directly under
    `$BUZON_DATOS`) into `fuentes/hotmail-carlos/`, preserving the SAME
    cursor so no mail is re-sent and none is skipped. Returns True only when
    it actually moved something.

    Never runs twice: it is a no-op the moment `fuentes/` exists at all --
    migrated already, or a fresh multi-source install that never had the
    old layout, look identical from here and both mean "nothing to do".
    Also a no-op on a brand-new install where neither `fuentes/` nor any
    legacy file exists.
    """
    raiz = Path(directorio_datos)
    if directorio_fuentes(directorio_datos).is_dir():
        return False

    legado_credencial = raiz / "cache.bin"
    legado_cursor = raiz / "cursor.json"
    legado_reglas = raiz / "reglas.json"
    legado_estado = raiz / "estado.json"

    if not any(p.is_file() for p in (legado_credencial, legado_cursor, legado_reglas, legado_estado)):
        return False

    destino = directorio_fuente(directorio_datos, ID_FUENTE_MIGRADA)
    cripto.asegurar_directorio_datos(destino)

    direccion = ""
    if legado_credencial.is_file():
        cifrado = legado_credencial.read_bytes()
        if cifrado:
            try:
                direccion = _direccion_desde_cache_msal(cripto.descifrar(cifrado, clave_cifrado))
            except cripto.ClaveInvalidaError:
                direccion = ""
        legado_credencial.replace(destino / NOMBRE_ARCHIVO_CREDENCIAL)

    if legado_cursor.is_file():
        legado_cursor.replace(destino / "cursor.json")
    if legado_reglas.is_file():
        legado_reglas.replace(destino / "reglas.json")
    if legado_estado.is_file():
        legado_estado.replace(destino / "estado.json")

    guardar_fuente(
        directorio_datos,
        FuenteConfig(
            id=ID_FUENTE_MIGRADA,
            proveedor="microsoft",
            direccion=direccion,
            etiqueta="Hotmail",
            token_finanzas_ref="carlos",
            activa=True,
        ),
    )
    return True
