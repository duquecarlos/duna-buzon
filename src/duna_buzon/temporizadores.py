"""Explicit, named network timeouts for every outbound client this
connector builds -- BUZON-GRAPH-TIMEOUT-1. Before this slice, every
`httpx.Client` here relied on httpx's own 5-second default for everything
(connect, read, write, pool), which is what let a single slow Graph
listing call wedge an entire pass on `duna-prod-1`: three consecutive
10-minute passes stopped with `parada=graph listar error=ReadTimeout`,
cursor frozen, `duna-buzon.service` failed. Every client now names its own
timeout explicitly, never the library default.
"""

from __future__ import annotations

import httpx

TIMEOUT_CONEXION_SEGUNDOS = 10.0
TIMEOUT_LECTURA_SEGUNDOS = 60.0
TIMEOUT_ESCRITURA_SEGUNDOS = 30.0
TIMEOUT_POOL_SEGUNDOS = 10.0

# Shared by every httpx client this connector builds (Graph, Gmail,
# Finanzas, Cuenta Duna) -- one set of figures, so a future change to any
# of them changes every client at once instead of drifting apart.
TIMEOUT_HTTPX = httpx.Timeout(
    connect=TIMEOUT_CONEXION_SEGUNDOS,
    read=TIMEOUT_LECTURA_SEGUNDOS,
    write=TIMEOUT_ESCRITURA_SEGUNDOS,
    pool=TIMEOUT_POOL_SEGUNDOS,
)

# `imaplib.IMAP4_SSL`'s own `timeout` is one socket-level value, not a
# per-phase `httpx.Timeout` -- the read timeout is the one reused here,
# since a FETCH of a large message is this provider's own slowest phase,
# the same role `TIMEOUT_LECTURA_SEGUNDOS` plays for every httpx client.
TIMEOUT_IMAP_SEGUNDOS = TIMEOUT_LECTURA_SEGUNDOS
