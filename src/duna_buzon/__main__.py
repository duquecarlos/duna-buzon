"""`uv run python -m duna_buzon <comando>`. Config comes from the real
process environment only -- `os.environ`, read once, here."""

from __future__ import annotations

import os
import sys

from duna_buzon import cli
from duna_buzon.config import Config, ConfigError

_NOMBRES_DE_FUNCION = {
    "entrar": "comando_entrar",
    "sincronizar": "comando_sincronizar",
    "estado": "comando_estado",
}


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if len(argumentos) != 1 or argumentos[0] not in _NOMBRES_DE_FUNCION:
        print(f"usage: python -m duna_buzon {{{'|'.join(_NOMBRES_DE_FUNCION)}}}", file=sys.stderr)
        return 2

    try:
        config = Config.desde_entorno(os.environ)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # A live attribute lookup, not a reference captured at import time, so
    # a test can swap `cli.comando_estado` for a fake after this module
    # has already been imported.
    funcion = getattr(cli, _NOMBRES_DE_FUNCION[argumentos[0]])
    return funcion(config)


if __name__ == "__main__":
    raise SystemExit(main())
