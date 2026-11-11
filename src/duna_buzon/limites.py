"""Buzon-v1's own `meta` field caps -- read from Finanzas at
`../finanzas/duna-finanzas-api` `3d48f65` (`docs/buzon.md`;
`src/duna_finanzas/storage/schema.py`'s `BUZON_ID_EXTERNO_MAXIMO_CARACTERES`,
`BUZON_REMITENTE_MAXIMO_CARACTERES`, `BUZON_ASUNTO_MAXIMO_CARACTERES`), never
guessed. Finanzas set the caps; which side of each cap truncates and which
side skips is this connector's own decision (`BUZON-TOPES-META-1`):

- `asunto` is TRUNCATED, never skipped -- a receipt with a long subject is
  still a receipt.
- `id_externo` and `remitente` are SKIPPED before ever posting, because
  truncating either would break idempotency (`id_externo`) or attribution
  (`remitente`). A skip here is counted and the cursor advances past it,
  exactly like a 413/422 skip from Finanzas itself.

`FuenteWire` and `LIMITE_FUENTE` mirror the wire's own `fuente` field, the
same way: `src/duna_finanzas/web/buzon/schemas.py:37`'s
`Fuente = Literal["correo", "sms", "notificacion"]`, and the CHECK constraint
`fuente_conocida` on `String(16)` at `storage/schema.py:6560` (repeated at
`6709`/`6725`). This connector only ever sends `"correo"` -- the channel, not
a source's own identity (`BUZON-FUENTE-WIRE-CORREO-1`, correcting
`BUZON-FUENTES-ESQUEMA-1`, which sent `"<proveedor>:<id>"` here and Finanzas
would have rejected every entry with a `422`).
"""

from __future__ import annotations

from typing import Final, Literal

LIMITE_ID_EXTERNO: Final[int] = 998
LIMITE_REMITENTE: Final[int] = 320
LIMITE_ASUNTO: Final[int] = 998

FuenteWire = Literal["correo", "sms", "notificacion"]
LIMITE_FUENTE: Final[int] = 16
FUENTE_CORREO: Final[FuenteWire] = "correo"


def excede_limites_no_truncables(*, id_externo: str, remitente: str) -> bool:
    return len(id_externo) > LIMITE_ID_EXTERNO or len(remitente) > LIMITE_REMITENTE


def truncar_asunto(asunto: str) -> str:
    """Character-boundary truncation, the same way `texto.html_a_texto`
    truncates `cuerpo_texto`: Python string indexing counts code points,
    never bytes, so a multi-byte character is never split."""
    return asunto[:LIMITE_ASUNTO]
