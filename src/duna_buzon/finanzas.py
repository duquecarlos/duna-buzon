"""The Finanzas client -- `POST /buzon/entradas`, buzon-v1. THE WIRE IS
FINANZAS' CODE, NOT THIS MODULE: every field name and status code below was
read from `../finanzas/duna-finanzas-api` at `main` `9b9464e`
(`docs/buzon.md`, `src/duna_finanzas/web/buzon/router.py`,
`src/duna_finanzas/web/buzon/schemas.py`), never guessed.

Multipart: a `meta` form part (a JSON string -- `fuente`, `id_externo`,
`remitente`, `asunto`, `recibido_en`, `cuerpo_texto`, no other key, since
`EntradaBuzonMeta.model_config = {"extra": "forbid"}` answers `422` for one)
plus up to 10 `archivos` file parts, `Authorization: Bearer
<FINANZAS_BUZON_TOKEN>`. `httpx.Client` is constructed once, over `base_url`
from `Config.url_finanzas`, already refused non-loopback by `config.py`.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from duna_buzon.adjuntos import ArchivoMultipart, construir_partes_multipart
from duna_buzon.config import Config

RUTA_ENTRADAS = "/buzon/entradas"

Estado = Literal[
    "forwarded", "duplicated", "unauthorized", "retry", "rejected", "rechazo_permanente"
]

# 401, 413 and 422 are handled on their own; every other 4xx is a permanent
# rejection (`rechazo_permanente`), never retried. 408 and 429 stay transient,
# alongside every 5xx and a transport failure.
_ESTADOS_4XX_TRANSITORIOS = frozenset({408, 429})


@dataclass(frozen=True)
class ResultadoEnvio:
    estado: Estado
    id_entrada: str | None = None
    adjuntos_omitidos: int | None = None
    codigo_http: int | None = None
    clase_excepcion: str | None = None


@dataclass(frozen=True)
class MetaEntrada:
    """The `meta` form part's own fields -- exactly these six keys, in
    `EntradaBuzonMeta`'s own names; no other key, or the wire answers
    `422`.

    `fuente` carried the one constant value `"correo"` before
    `BUZON-FUENTES-ESQUEMA-1`; it is now `"<proveedor>:<id>"` (e.g.
    `"microsoft:hotmail-carlos"`), built by `fuentes.FuenteConfig.etiqueta_wire`
    -- the SAME wire key Finanzas already accepts, no new field added, and
    well inside every length cap `limites.py` mirrors from Finanzas (ids are
    short, hand-chosen source names)."""

    fuente: str
    id_externo: str
    remitente: str
    asunto: str | None
    recibido_en: datetime.datetime
    cuerpo_texto: str | None

    def como_json(self) -> str:
        return json.dumps(
            {
                "fuente": self.fuente,
                "id_externo": self.id_externo,
                "remitente": self.remitente,
                "asunto": self.asunto,
                "recibido_en": self.recibido_en.isoformat(),
                "cuerpo_texto": self.cuerpo_texto,
            }
        )


class FinanzasClient:
    def __init__(self, config: Config, *, transport: httpx.BaseTransport | None = None) -> None:
        self._token = config.finanzas_token
        self._cliente = httpx.Client(base_url=config.url_finanzas, transport=transport)

    def cerrar(self) -> None:
        self._cliente.close()

    def enviar_entrada(
        self, meta: MetaEntrada, archivos: Sequence[ArchivoMultipart]
    ) -> ResultadoEnvio:
        try:
            respuesta = self._cliente.post(
                RUTA_ENTRADAS,
                data={"meta": meta.como_json()},
                files=construir_partes_multipart(archivos),
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.TransportError as exc:
            return ResultadoEnvio(estado="retry", clase_excepcion=type(exc).__name__)

        if respuesta.status_code in (200, 201):
            cuerpo = respuesta.json()
            return ResultadoEnvio(
                estado="duplicated" if respuesta.status_code == 200 else "forwarded",
                id_entrada=cuerpo["id"],
                adjuntos_omitidos=cuerpo["adjuntos_omitidos"],
                codigo_http=respuesta.status_code,
            )
        if respuesta.status_code == 401:
            return ResultadoEnvio(estado="unauthorized", codigo_http=401)
        if respuesta.status_code in (413, 422):
            return ResultadoEnvio(estado="rejected", codigo_http=respuesta.status_code)
        if (
            400 <= respuesta.status_code < 500
            and respuesta.status_code not in _ESTADOS_4XX_TRANSITORIOS
        ):
            # Any other 4xx (400, 403, 404, 405, ...): Finanzas rejected the
            # request outright, not a transient condition -- never retried.
            return ResultadoEnvio(estado="rechazo_permanente", codigo_http=respuesta.status_code)
        # 408, 429, 503, any other 5xx: a transient server condition -- the
        # next timer run retries, cursor unmoved.
        return ResultadoEnvio(estado="retry", codigo_http=respuesta.status_code)
