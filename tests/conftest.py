from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

CLAVE_VALIDA_BASE64 = base64.b64encode(b"\x01" * 32).decode("ascii")


@pytest.fixture
def entorno_valido(tmp_path: Path) -> dict[str, str]:
    return {
        "BUZON_GRAPH_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
        "FINANZAS_BUZON_TOKEN": "token-de-prueba",
        "BUZON_DATOS": str(tmp_path / "datos"),
        "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
    }


@pytest.fixture
def no_es_windows() -> None:
    if os.name != "posix":
        pytest.skip("file-mode assertions are meaningless on Windows (no POSIX mode bits)")
