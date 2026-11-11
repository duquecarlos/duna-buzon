"""The provider interface every mailbox source implements -- list messages
since a cursor, fetch one message's body and attachments, list an
attachment's metadata before downloading it, close the underlying client.
`GraphClient` (`graph.py`) is the first implementation, structurally, with
no change to its own shape: this module only names the shape so the next
slice's IMAP client can be written against it directly.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator, Sequence
from typing import Protocol, runtime_checkable

import httpx

from duna_buzon.fuentes import FuenteConfig
from duna_buzon.graph import AdjuntoMetadata, GraphClient, MensajeCompleto, MensajeResumen


class ProveedorDesconocidoError(Exception):
    """A `fuente.json` names a provider this connector does not yet
    implement (e.g. `imap`, before the slice that builds it)."""


@runtime_checkable
class Proveedor(Protocol):
    """Structural, not a base class: `GraphClient` satisfies this without
    inheriting from it. A future IMAP client only needs to match this same
    shape."""

    def listar_mensajes(self, desde: datetime.datetime) -> Iterator[MensajeResumen]: ...

    def listar_adjuntos_metadata(self, id_mensaje: str) -> list[AdjuntoMetadata]: ...

    def obtener_mensaje_completo(
        self, id_mensaje: str, metadatos_adjuntos: Sequence[AdjuntoMetadata]
    ) -> MensajeCompleto: ...

    def cerrar(self) -> None: ...


def construir_proveedor(
    fuente: FuenteConfig, token: str, *, transport: httpx.BaseTransport | None = None
) -> Proveedor:
    if fuente.proveedor == "microsoft":
        return GraphClient(token, transport=transport)
    raise ProveedorDesconocidoError(
        f"source {fuente.id!r} names provider {fuente.proveedor!r}, not yet implemented"
    )
