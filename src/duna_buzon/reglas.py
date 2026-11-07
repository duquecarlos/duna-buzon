"""Which messages get forwarded -- DATA, never code. No sender, bank or
employer name lives in this module or anywhere else in this repository
("template, not tenant"): every real value lives in `reglas.json`, which is
never committed; `deploy/reglas.example.json` ships only placeholder
senders and generic Spanish terms.

A message matches when ANY of:
  (a) its sender address or domain is in `remitentes`;
  (b) it carries a PDF/XML/ZIP attachment (by name before download; the
      real recognition, by bytes, happens once the content is in hand) AND
      its subject or sender matches one of `palabras`;
  (c) it carries a ZIP or XML attachment at all.

Matching text is accent- and case-insensitive throughout, since Spanish
financial mail mixes "nómina"/"nomina" freely.
"""

from __future__ import annotations

import datetime
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_EXTENSIONES_REGLA_B = frozenset({".pdf", ".xml", ".zip"})
_EXTENSIONES_REGLA_C = frozenset({".xml", ".zip"})


class ReglasInvalidasError(Exception):
    """`reglas.json` is missing, malformed, or missing a required field."""


def _normalizar(texto: str) -> str:
    """Casefold plus a strip of combining accents, so "NÓMINA" ==
    "nomina"."""
    sin_acentos = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return sin_acentos.casefold()


@dataclass(frozen=True)
class Reglas:
    desde: datetime.datetime
    remitentes: tuple[str, ...]
    palabras: tuple[str, ...]


@dataclass(frozen=True)
class MensajeCandidato:
    """The lone fields a rule needs -- never a message's body, which this
    module never reads."""

    remitente: str
    asunto: str | None
    nombres_adjuntos: Sequence[str]


def cargar_reglas(ruta: Path) -> Reglas:
    if not ruta.is_file():
        raise ReglasInvalidasError(f"rules file not found: {ruta}")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReglasInvalidasError(f"rules file is not valid JSON: {ruta}") from exc

    try:
        desde_str = datos["desde"]
        remitentes = datos["remitentes"]
        palabras = datos["palabras"]
    except KeyError as exc:
        raise ReglasInvalidasError(f"rules file is missing required field: {exc.args[0]}") from exc

    try:
        desde = datetime.datetime.fromisoformat(desde_str)
    except ValueError as exc:
        raise ReglasInvalidasError("rules file field 'desde' is not a valid ISO date") from exc
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=datetime.UTC)

    return Reglas(
        desde=desde,
        remitentes=tuple(str(r) for r in remitentes),
        palabras=tuple(str(p) for p in palabras),
    )


def _remitente_coincide(remitente: str, remitentes_regla: tuple[str, ...]) -> bool:
    remitente_norm = remitente.strip().casefold()
    _, _, dominio_remitente = remitente_norm.rpartition("@")
    for entrada in remitentes_regla:
        entrada_norm = entrada.strip().casefold().lstrip("@")
        if "@" in entrada_norm:
            if remitente_norm == entrada_norm:
                return True
        elif dominio_remitente == entrada_norm:
            return True
    return False


def _extension(nombre: str) -> str:
    return Path(nombre).suffix.lower()


def _tiene_adjunto_con_extension(nombres_adjuntos: Sequence[str], extensiones: frozenset[str]) -> bool:
    return any(_extension(nombre) in extensiones for nombre in nombres_adjuntos)


def _palabra_coincide(texto: str | None, palabras: tuple[str, ...]) -> bool:
    if texto is None:
        return False
    texto_norm = _normalizar(texto)
    return any(_normalizar(palabra) in texto_norm for palabra in palabras)


def coincide(mensaje: MensajeCandidato, reglas: Reglas) -> bool:
    if _remitente_coincide(mensaje.remitente, reglas.remitentes):
        return True

    if _tiene_adjunto_con_extension(mensaje.nombres_adjuntos, _EXTENSIONES_REGLA_B) and (
        _palabra_coincide(mensaje.asunto, reglas.palabras)
        or _palabra_coincide(mensaje.remitente, reglas.palabras)
    ):
        return True

    if _tiene_adjunto_con_extension(mensaje.nombres_adjuntos, _EXTENSIONES_REGLA_C):
        return True

    return False
