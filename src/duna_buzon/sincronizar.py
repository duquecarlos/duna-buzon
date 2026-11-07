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
from duna_buzon.finanzas import FinanzasClient, MetaEntrada
from duna_buzon.graph import GraphAutenticacionError, GraphClient, GraphErrorTransitorio
from duna_buzon.reglas import MensajeCandidato, Reglas, coincide
from duna_buzon.texto import html_a_texto


@dataclass
class Contadores:
    vistos: int = 0
    reenviados: int = 0
    duplicados: int = 0
    omitidos_por_reglas: int = 0
    omitidos_por_413_422: int = 0
    adjuntos_descartados: int = 0


@dataclass(frozen=True)
class ResultadoPasada:
    contadores: Contadores = field(default_factory=Contadores)
    cursor_final: datetime.datetime | None = None
    detenida_por_autenticacion: bool = False
    detenida_por_error_transitorio: bool = False

    @property
    def debe_persistir_cursor(self) -> bool:
        return not (self.detenida_por_autenticacion or self.detenida_por_error_transitorio)


def ejecutar_pasada(
    *,
    cliente_graph: GraphClient,
    cliente_finanzas: FinanzasClient,
    reglas: Reglas,
    cursor_inicial: datetime.datetime,
) -> ResultadoPasada:
    contadores = Contadores()
    cursor_final = cursor_inicial
    desde_con_solape = cursor_inicial - SOLAPE

    try:
        for mensaje in cliente_graph.listar_mensajes(desde_con_solape):
            contadores.vistos += 1

            nombres_adjuntos: list[str] = []
            if mensaje.tiene_adjuntos:
                nombres_adjuntos = [
                    metadato.nombre
                    for metadato in cliente_graph.listar_adjuntos_metadata(mensaje.id)
                ]

            candidato = MensajeCandidato(
                remitente=mensaje.remitente,
                asunto=mensaje.asunto,
                nombres_adjuntos=nombres_adjuntos,
            )
            if not coincide(candidato, reglas):
                contadores.omitidos_por_reglas += 1
                cursor_final = max(cursor_final, mensaje.recibido_en)
                continue

            completo = cliente_graph.obtener_mensaje_completo(mensaje.id)
            cuerpo_texto = html_a_texto(completo.cuerpo_html) if completo.cuerpo_html else None

            meta = MetaEntrada(
                fuente="correo",
                id_externo=mensaje.internet_message_id,
                remitente=mensaje.remitente,
                asunto=mensaje.asunto,
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
                cursor_final = max(cursor_final, mensaje.recibido_en)
            elif resultado.estado == "duplicated":
                contadores.duplicados += 1
                cursor_final = max(cursor_final, mensaje.recibido_en)
            elif resultado.estado == "rejected":
                contadores.omitidos_por_413_422 += 1
                cursor_final = max(cursor_final, mensaje.recibido_en)
            elif resultado.estado == "unauthorized":
                return ResultadoPasada(
                    contadores=contadores, cursor_final=cursor_final, detenida_por_autenticacion=True
                )
            else:  # "retry": a 503, another 5xx, or a connection error
                return ResultadoPasada(
                    contadores=contadores,
                    cursor_final=cursor_final,
                    detenida_por_error_transitorio=True,
                )
    except GraphAutenticacionError:
        return ResultadoPasada(
            contadores=contadores, cursor_final=cursor_final, detenida_por_autenticacion=True
        )
    except GraphErrorTransitorio:
        return ResultadoPasada(
            contadores=contadores, cursor_final=cursor_final, detenida_por_error_transitorio=True
        )

    return ResultadoPasada(contadores=contadores, cursor_final=cursor_final)
