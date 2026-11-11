"""One pass: pull candidate messages from Graph, decide which match a rule,
build and post the ones that do, advance the cursor past what was finished.
Never logs or returns anything about a message's own content -- only
counts, in `Contadores`.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from duna_buzon.adjuntos import AdjuntoCandidato, empacar
from duna_buzon.cursor import SOLAPE
from duna_buzon.finanzas import FinanzasClient, MetaEntrada, ResultadoEnvio
from duna_buzon.graph import AdjuntoMetadata, MensajeResumen
from duna_buzon.limites import FUENTE_CORREO, excede_limites_no_truncables, truncar_asunto
from duna_buzon.proveedor import Proveedor
from duna_buzon.proveedor_errores import ProveedorAutenticacionError, ProveedorErrorTransitorio
from duna_buzon.reglas import MensajeCandidato, Reglas, coincide
from duna_buzon.texto import html_a_texto


def _motivo_finanzas(resultado: ResultadoEnvio) -> str:
    """Builds the stop reason for a Finanzas response: side, step (always
    `enviar`, the client's only call) and the HTTP status, or -- for a
    transport failure -- the exception's class name. Never the token, a
    sender or a subject."""
    if resultado.codigo_http is not None:
        return f"finanzas enviar http={resultado.codigo_http}"
    return f"finanzas enviar error={resultado.clase_excepcion}"


@dataclass
class Contadores:
    vistos: int = 0
    reenviados: int = 0
    duplicados: int = 0
    omitidos_por_reglas: int = 0
    # Also counts a message skipped BEFORE posting because `id_externo` or
    # `remitente` is over its own wire cap (`limites.py`) -- the same
    # permanent, cursor-advancing skip a 413/422 from Finanzas gets.
    omitidos_por_413_422: int = 0
    adjuntos_descartados: int = 0


@dataclass(frozen=True)
class ResultadoPasada:
    contadores: Contadores = field(default_factory=Contadores)
    cursor_final: datetime.datetime | None = None
    # The provider's own opaque resume token, carried the same way
    # `cursor_final` is -- see `graph.MensajeResumen.cursor_proveedor`.
    cursor_proveedor_final: str | None = None
    detenida_por_autenticacion: bool = False
    detenida_por_error_transitorio: bool = False
    detenida_por_rechazo_permanente: bool = False
    # Which side (`graph`/`finanzas`), which step, and the HTTP status or
    # exception class -- set whenever any `detenida_por_*` flag above is
    # True. Never content: see `sincronizar.py`'s own `_motivo_finanzas` and
    # `graph.py`'s `GraphError.motivo`.
    motivo: str | None = None

    @property
    def debe_persistir_cursor(self) -> bool:
        return not (
            self.detenida_por_autenticacion
            or self.detenida_por_error_transitorio
            or self.detenida_por_rechazo_permanente
        )


def ejecutar_pasada(
    *,
    proveedor: Proveedor,
    cliente_finanzas: FinanzasClient,
    reglas: Reglas,
    cursor_inicial: datetime.datetime,
    cursor_proveedor_inicial: str | None = None,
) -> ResultadoPasada:
    contadores = Contadores()
    cursor_final = cursor_inicial
    cursor_proveedor_final = cursor_proveedor_inicial
    desde_con_solape = cursor_inicial - SOLAPE

    def _avanzar(mensaje: MensajeResumen) -> None:
        nonlocal cursor_final, cursor_proveedor_final
        cursor_final = max(cursor_final, mensaje.recibido_en)
        if mensaje.cursor_proveedor is not None:
            cursor_proveedor_final = mensaje.cursor_proveedor

    try:
        for mensaje in proveedor.listar_mensajes(desde_con_solape):
            contadores.vistos += 1

            metadatos_adjuntos: list[AdjuntoMetadata] = []
            if mensaje.tiene_adjuntos:
                metadatos_adjuntos = proveedor.listar_adjuntos_metadata(mensaje.id)
            nombres_adjuntos = [metadato.nombre for metadato in metadatos_adjuntos]

            candidato = MensajeCandidato(
                remitente=mensaje.remitente,
                asunto=mensaje.asunto,
                nombres_adjuntos=nombres_adjuntos,
            )
            if not coincide(candidato, reglas):
                contadores.omitidos_por_reglas += 1
                _avanzar(mensaje)
                continue

            if excede_limites_no_truncables(
                id_externo=mensaje.internet_message_id, remitente=mensaje.remitente
            ):
                contadores.omitidos_por_413_422 += 1
                _avanzar(mensaje)
                continue

            completo = proveedor.obtener_mensaje_completo(mensaje.id, metadatos_adjuntos)
            contadores.adjuntos_descartados += completo.adjuntos_descartados
            cuerpo_texto = html_a_texto(completo.cuerpo_html) if completo.cuerpo_html else None
            asunto = truncar_asunto(mensaje.asunto) if mensaje.asunto is not None else None

            meta = MetaEntrada(
                fuente=FUENTE_CORREO,
                id_externo=mensaje.internet_message_id,
                remitente=mensaje.remitente,
                asunto=asunto,
                recibido_en=mensaje.recibido_en,
                cuerpo_texto=cuerpo_texto,
            )
            empaque = empacar(
                meta.como_json(),
                [AdjuntoCandidato(nombre=a.nombre, contenido=a.contenido) for a in completo.adjuntos],
            )
            contadores.adjuntos_descartados += empaque.descartados

            resultado = cliente_finanzas.enviar_entrada(meta, empaque.incluidos)

            if resultado.estado == "forwarded":
                contadores.reenviados += 1
                _avanzar(mensaje)
            elif resultado.estado == "duplicated":
                contadores.duplicados += 1
                _avanzar(mensaje)
            elif resultado.estado == "rejected":
                contadores.omitidos_por_413_422 += 1
                _avanzar(mensaje)
            elif resultado.estado == "unauthorized":
                return ResultadoPasada(
                    contadores=contadores,
                    cursor_final=cursor_final,
                    cursor_proveedor_final=cursor_proveedor_final,
                    detenida_por_autenticacion=True,
                    motivo=_motivo_finanzas(resultado),
                )
            elif resultado.estado == "rechazo_permanente":
                return ResultadoPasada(
                    contadores=contadores,
                    cursor_final=cursor_final,
                    cursor_proveedor_final=cursor_proveedor_final,
                    detenida_por_rechazo_permanente=True,
                    motivo=_motivo_finanzas(resultado),
                )
            else:  # "retry": 408, 429, a 5xx, or a connection error
                return ResultadoPasada(
                    contadores=contadores,
                    cursor_final=cursor_final,
                    cursor_proveedor_final=cursor_proveedor_final,
                    detenida_por_error_transitorio=True,
                    motivo=_motivo_finanzas(resultado),
                )
    except ProveedorAutenticacionError as exc:
        return ResultadoPasada(
            contadores=contadores,
            cursor_final=cursor_final,
            cursor_proveedor_final=cursor_proveedor_final,
            detenida_por_autenticacion=True,
            motivo=str(exc),
        )
    except ProveedorErrorTransitorio as exc:
        return ResultadoPasada(
            contadores=contadores,
            cursor_final=cursor_final,
            cursor_proveedor_final=cursor_proveedor_final,
            detenida_por_error_transitorio=True,
            motivo=str(exc),
        )

    return ResultadoPasada(
        contadores=contadores, cursor_final=cursor_final, cursor_proveedor_final=cursor_proveedor_final
    )
