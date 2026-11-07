"""Attachment sniffing, and packing under the wire's whole-request budget.

`sniff_tipo_contenido` mirrors duna-finanzas-api's own
`storage/archivos.py::sniff_tipo_contenido` (read at
`../finanzas/duna-finanzas-api` main `9b9464e`) byte for byte: this
connector only uploads what Finanzas would actually keep, rather than
spending budget on bytes it would skip server-side anyway.

`empacar` MEASURES the real encoded multipart body (`httpx.Request.read()`)
on every candidate, never estimates it -- the one way to honour the wire's
12,582,912-byte whole-request ceiling exactly. When an attachment would
push the body over that ceiling, it is counted as dropped and packing
keeps trying the REST of the candidates, rather than giving up at the
first one that doesn't fit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

LIMITE_TAMANO_ARCHIVO_BYTES = 10 * 1024 * 1024
LIMITE_CUERPO_BYTES = 12_582_912
LIMITE_ADJUNTOS_POR_ENTRADA = 10

_NOMBRE_PARTE_FORZAR_MULTIPART = "_forzar_multipart"

ArchivoMultipart = tuple[str, bytes, str]


def sniff_tipo_contenido(contenido: bytes) -> str | None:
    if not contenido:
        return None
    if contenido.startswith(b"%PDF-"):
        return "application/pdf"
    if contenido.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "image/webp"
    sin_bom = contenido[3:] if contenido.startswith(b"\xef\xbb\xbf") else contenido
    if sin_bom.startswith(b"<?xml"):
        return "application/xml"
    if contenido.startswith(b"PK\x03\x04"):
        return "application/zip"
    return None


def construir_partes_multipart(
    archivos: Sequence[ArchivoMultipart],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    """`httpx`'s own `files=` shape for `archivos`, forced to multipart even
    with zero attachments -- `finanzas.py`'s real POST and this module's own
    size measurement below both build the request the identical way, so a
    measured size is also the size actually sent."""
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("archivos", (nombre, contenido, tipo)) for nombre, contenido, tipo in archivos
    ]
    if not files:
        # `data=` alone would encode as `application/x-www-form-urlencoded`;
        # the real request always carries a `File`-bound `archivos`
        # parameter server-side, so it must always be multipart, even with
        # zero attachments. This part binds to nothing Finanzas declares.
        files = [(_NOMBRE_PARTE_FORZAR_MULTIPART, ("_", b"", "application/octet-stream"))]
    return files


def tamano_cuerpo_multipart(meta_json: str, archivos: Sequence[ArchivoMultipart]) -> int:
    """The exact byte length of the multipart body this request would send
    -- built and read the same way `httpx` would send it, never estimated
    from the parts' own lengths (boundaries and headers add overhead)."""
    peticion = httpx.Request(
        "POST",
        "http://buzon.local/",
        data={"meta": meta_json},
        files=construir_partes_multipart(archivos),
    )
    return len(peticion.read())


@dataclass(frozen=True)
class AdjuntoCandidato:
    nombre: str
    contenido: bytes


@dataclass(frozen=True)
class ResultadoEmpaque:
    incluidos: tuple[ArchivoMultipart, ...]
    descartados: int


def empacar(meta_json: str, candidatos: Sequence[AdjuntoCandidato]) -> ResultadoEmpaque:
    incluidos: list[ArchivoMultipart] = []
    descartados = 0

    for candidato in candidatos:
        if len(candidato.contenido) > LIMITE_TAMANO_ARCHIVO_BYTES:
            descartados += 1
            continue

        tipo_contenido = sniff_tipo_contenido(candidato.contenido)
        if tipo_contenido is None:
            descartados += 1
            continue

        if len(incluidos) >= LIMITE_ADJUNTOS_POR_ENTRADA:
            descartados += 1
            continue

        tentativo = [*incluidos, (candidato.nombre, candidato.contenido, tipo_contenido)]
        if tamano_cuerpo_multipart(meta_json, tentativo) > LIMITE_CUERPO_BYTES:
            descartados += 1
            continue

        incluidos.append((candidato.nombre, candidato.contenido, tipo_contenido))

    return ResultadoEmpaque(incluidos=tuple(incluidos), descartados=descartados)
