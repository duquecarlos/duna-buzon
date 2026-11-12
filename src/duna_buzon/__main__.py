"""`uv run python -m duna_buzon <comando>`. Config comes from the real
process environment only -- `os.environ`, read once, here.

`entrar` is the one command that takes an argument -- `--fuente <id>`,
optional when exactly one source is configured (today's common case right
after the automatic migration), required the moment a second source
exists. `agregar-fuente` is the other one that takes arguments, to write a
new IMAP source's `fuente.json` -- it never signs in; `entrar --fuente
<id>` is the separate next step. `carpetas <id>` takes the source's id and
prints every folder that source's own server exposes
(`BUZON-IMAP-CARPETAS-INCLUIDAS-1`). `carpetas-graph <id>` is its
microsoft-source twin (`BUZON-CARPETAS-MICROSOFT-CLI-1`). `fijar-carpetas
<id> [--carpeta X ...] [--todas]` sets or removes an EXISTING imap or
microsoft source's `carpetas` setting (same slice). Every other command
touches every configured source on its own, `fuentes.py`'s own job to
enumerate."""

from __future__ import annotations

import os
import sys

from duna_buzon import cli
from duna_buzon.config import Config, ConfigError

_COMANDOS = (
    "entrar",
    "sincronizar",
    "estado",
    "fuentes",
    "agregar-fuente",
    "carpetas",
    "carpetas-graph",
    "fijar-carpetas",
)

_OPCIONES_AGREGAR_FUENTE = (
    "--id",
    "--direccion",
    "--host",
    "--token-finanzas",
    "--port",
    "--etiqueta",
    "--carpeta",
)
_OPCIONES_AGREGAR_FUENTE_REQUERIDAS = ("--id", "--direccion", "--host", "--token-finanzas")
_PUERTO_IMAP_PREDETERMINADO = "993"

_USO = (
    f"usage: python -m duna_buzon {{{'|'.join(_COMANDOS)}}} [--fuente <id>]\n"
    "       python -m duna_buzon agregar-fuente imap --id <id> --direccion <direccion> "
    "--host <host> --token-finanzas <ref> [--port <puerto>] [--etiqueta <etiqueta>] "
    "[--carpeta <nombre> ...]\n"
    "       python -m duna_buzon carpetas <id>\n"
    "       python -m duna_buzon carpetas-graph <id>\n"
    "       python -m duna_buzon fijar-carpetas <id> (--carpeta <nombre> ... | --todas)"
)


def _parsear_agregar_fuente(resto: list[str]) -> dict[str, object] | None:
    if not resto or resto[0] != "imap":
        return None
    opciones: dict[str, object] = {"port": _PUERTO_IMAP_PREDETERMINADO}
    carpetas: list[str] = []
    pendientes = resto[1:]
    vistas: set[str] = set()
    i = 0
    while i < len(pendientes):
        clave = pendientes[i]
        if clave not in _OPCIONES_AGREGAR_FUENTE or i + 1 >= len(pendientes):
            return None
        if clave == "--carpeta":
            carpetas.append(pendientes[i + 1])
        else:
            opciones[clave.lstrip("-")] = pendientes[i + 1]
            vistas.add(clave)
        i += 2
    if not set(_OPCIONES_AGREGAR_FUENTE_REQUERIDAS).issubset(vistas):
        return None
    if not str(opciones["port"]).isdigit():
        return None
    if carpetas:
        opciones["carpetas"] = carpetas
    return opciones


def _parsear_fijar_carpetas(resto: list[str]) -> tuple[str, list[str] | None, bool] | None:
    """`<id> (--carpeta <nombre> ... | --todas)` -- exactly one of the two,
    at least one `--carpeta` when not `--todas`. `None` on any other
    shape, same convention as `_parsear_agregar_fuente`."""
    if len(resto) < 2:
        return None
    id_fuente, pendientes = resto[0], resto[1:]
    carpetas: list[str] = []
    todas = False
    i = 0
    while i < len(pendientes):
        clave = pendientes[i]
        if clave == "--todas":
            todas = True
            i += 1
        elif clave == "--carpeta" and i + 1 < len(pendientes):
            carpetas.append(pendientes[i + 1])
            i += 2
        else:
            return None
    if todas and carpetas:
        return None
    if not todas and not carpetas:
        return None
    return id_fuente, (carpetas if carpetas else None), todas


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if not argumentos or argumentos[0] not in _COMANDOS:
        print(_USO, file=sys.stderr)
        return 2

    comando, resto = argumentos[0], argumentos[1:]

    id_fuente: str | None = None
    id_fuente_carpetas: str | None = None
    id_fuente_carpetas_graph: str | None = None
    opciones_agregar_fuente: dict[str, object] | None = None
    opciones_fijar_carpetas: tuple[str, list[str] | None, bool] | None = None
    if comando == "entrar" and len(resto) == 2 and resto[0] == "--fuente":
        id_fuente = resto[1]
        resto = []
    elif comando == "agregar-fuente":
        opciones_agregar_fuente = _parsear_agregar_fuente(resto)
        if opciones_agregar_fuente is None:
            print(_USO, file=sys.stderr)
            return 2
        resto = []
    elif comando == "carpetas":
        if len(resto) != 1:
            print(_USO, file=sys.stderr)
            return 2
        id_fuente_carpetas = resto[0]
        resto = []
    elif comando == "carpetas-graph":
        if len(resto) != 1:
            print(_USO, file=sys.stderr)
            return 2
        id_fuente_carpetas_graph = resto[0]
        resto = []
    elif comando == "fijar-carpetas":
        opciones_fijar_carpetas = _parsear_fijar_carpetas(resto)
        if opciones_fijar_carpetas is None:
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
            puerto=int(str(opciones_agregar_fuente["port"])),
            token_finanzas=opciones_agregar_fuente["token-finanzas"],
            etiqueta=opciones_agregar_fuente.get("etiqueta"),
            carpetas=opciones_agregar_fuente.get("carpetas"),
        )
    if comando == "carpetas":
        assert id_fuente_carpetas is not None
        return cli.comando_carpetas(config, id_fuente_carpetas)
    if comando == "carpetas-graph":
        assert id_fuente_carpetas_graph is not None
        return cli.comando_carpetas_graph(config, id_fuente_carpetas_graph)
    if comando == "fijar-carpetas":
        assert opciones_fijar_carpetas is not None
        id_fuente_fijar, carpetas_fijar, todas_fijar = opciones_fijar_carpetas
        return cli.comando_fijar_carpetas(
            config, id_fuente_fijar, carpetas=carpetas_fijar, todas=todas_fijar
        )
    return cli.comando_fuentes(config)


if __name__ == "__main__":
    raise SystemExit(main())
