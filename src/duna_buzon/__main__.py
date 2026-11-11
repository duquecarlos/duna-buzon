"""`uv run python -m duna_buzon <comando>`. Config comes from the real
process environment only -- `os.environ`, read once, here.

`entrar` is the one command that takes an argument -- `--fuente <id>`,
optional when exactly one source is configured (today's common case right
after the automatic migration), required the moment a second source
exists. Every other command touches every configured source on its own,
`fuentes.py`'s own job to enumerate."""

from __future__ import annotations

import os
import sys

from duna_buzon import cli
from duna_buzon.config import Config, ConfigError

_COMANDOS = ("entrar", "sincronizar", "estado", "fuentes")

_USO = f"usage: python -m duna_buzon {{{'|'.join(_COMANDOS)}}} [--fuente <id>]"


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if not argumentos or argumentos[0] not in _COMANDOS:
        print(_USO, file=sys.stderr)
        return 2

    comando, resto = argumentos[0], argumentos[1:]

    id_fuente: str | None = None
    if comando == "entrar" and len(resto) == 2 and resto[0] == "--fuente":
        id_fuente = resto[1]
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
    return cli.comando_fuentes(config)


if __name__ == "__main__":
    raise SystemExit(main())
