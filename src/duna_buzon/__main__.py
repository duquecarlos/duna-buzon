"""`uv run python -m duna_buzon <comando>`. Config comes from the real
process environment only -- `os.environ`, read once, here.

`entrar` is the one command that takes an argument -- `--fuente <id>`,
optional when exactly one source is configured (today's common case right
after the automatic migration), required the moment a second source
exists. `agregar-fuente` is the other one that takes arguments, to write a
new IMAP source's `fuente.json` -- it never signs in; `entrar --fuente
<id>` is the separate next step. Every other command touches every
configured source on its own, `fuentes.py`'s own job to enumerate."""

from __future__ import annotations

import os
import sys

from duna_buzon import cli
from duna_buzon.config import Config, ConfigError

_COMANDOS = ("entrar", "sincronizar", "estado", "fuentes", "agregar-fuente")

_OPCIONES_AGREGAR_FUENTE = ("--id", "--direccion", "--host", "--token-finanzas", "--port", "--etiqueta")
_OPCIONES_AGREGAR_FUENTE_REQUERIDAS = ("--id", "--direccion", "--host", "--token-finanzas")
_PUERTO_IMAP_PREDETERMINADO = "993"

_USO = (
    f"usage: python -m duna_buzon {{{'|'.join(_COMANDOS)}}} [--fuente <id>]\n"
    "       python -m duna_buzon agregar-fuente imap --id <id> --direccion <direccion> "
    "--host <host> --token-finanzas <ref> [--port <puerto>] [--etiqueta <etiqueta>]"
)


def _parsear_agregar_fuente(resto: list[str]) -> dict[str, str] | None:
    if not resto or resto[0] != "imap":
        return None
    opciones = {"port": _PUERTO_IMAP_PREDETERMINADO}
    pendientes = resto[1:]
    vistas: set[str] = set()
    i = 0
    while i < len(pendientes):
        clave = pendientes[i]
        if clave not in _OPCIONES_AGREGAR_FUENTE or i + 1 >= len(pendientes):
            return None
        opciones[clave.lstrip("-")] = pendientes[i + 1]
        vistas.add(clave)
        i += 2
    if not set(_OPCIONES_AGREGAR_FUENTE_REQUERIDAS).issubset(vistas):
        return None
    if not opciones["port"].isdigit():
        return None
    return opciones


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if not argumentos or argumentos[0] not in _COMANDOS:
        print(_USO, file=sys.stderr)
        return 2

    comando, resto = argumentos[0], argumentos[1:]

    id_fuente: str | None = None
    opciones_agregar_fuente: dict[str, str] | None = None
    if comando == "entrar" and len(resto) == 2 and resto[0] == "--fuente":
        id_fuente = resto[1]
        resto = []
    elif comando == "agregar-fuente":
        opciones_agregar_fuente = _parsear_agregar_fuente(resto)
        if opciones_agregar_fuente is None:
            print(_USO, file=sys.stderr)
            return 2
        resto = []
    if resto:
        print(_USO, file=sys.stderr)
        return 2

    try:
        config = Config.desde_entorno(os.environ)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if comando == "entrar":
        return cli.comando_entrar(config, id_fuente)
    if comando == "sincronizar":
        return cli.comando_sincronizar(config)
    if comando == "estado":
        return cli.comando_estado(config)
    if comando == "agregar-fuente":
        assert opciones_agregar_fuente is not None
        return cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente=opciones_agregar_fuente["id"],
            direccion=opciones_agregar_fuente["direccion"],
            host=opciones_agregar_fuente["host"],
            puerto=int(opciones_agregar_fuente["port"]),
            token_finanzas=opciones_agregar_fuente["token-finanzas"],
            etiqueta=opciones_agregar_fuente.get("etiqueta"),
        )
    return cli.comando_fuentes(config)


if __name__ == "__main__":
    raise SystemExit(main())
