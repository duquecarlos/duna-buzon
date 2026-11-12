"""The Finanzas client -- `POST /buzon/entradas`, buzon-v1. THE WIRE IS
FINANZAS' CODE, NOT THIS MODULE: every field name and status code below was
read from `../finanzas/duna-finanzas-api` at `main` `9b9464e`
(`docs/buzon.md`, `src/duna_finanzas/web/buzon/router.py`,
`src/duna_finanzas/web/buzon/schemas.py`), never guessed.

Multipart: a `meta` form part (a JSON string -- `fuente`, `id_externo`,
`remitente`, `asunto`, `recibido_en`, `cuerpo_texto`, and, behind
`Config.enviar_origen` only (`BUZON-ORIGEN-WIRE-1`), `origen` -- no other
key, since `EntradaBuzonMeta.model_config = {"extra": "forbid"}` answers
`422` for one) plus up to 10 `archivos` file parts, `Authorization: Bearer
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

from duna_buzon import temporizadores
from duna_buzon.adjuntos import ArchivoMultipart, construir_partes_multipart
from duna_buzon.config import Config
from duna_buzon.limites import FuenteWire

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
    """The `meta` form part's own fields -- these six keys, in
    `EntradaBuzonMeta`'s own names, plus the optional seventh (`origen`);
    no other key, or the wire answers `422`.

    `fuente` is the CHANNEL, not a source's own identity: always
    `"correo"` (`limites.FUENTE_CORREO`) for every mail source, Microsoft or
    IMAP alike. `BUZON-FUENTES-ESQUEMA-1` briefly sent `"<proveedor>:<id>"`
    here instead, which Finanzas' own `Literal["correo", "sms",
    "notificacion"]` (`limites.FuenteWire`, mirrored from
    `src/duna_finanzas/web/buzon/schemas.py:37`) would answer `422` to on
    every entry -- fixed by `BUZON-FUENTE-WIRE-CORREO-1`.

    `origen` IS `fuentes.FuenteConfig.etiqueta_registro` -- a source's own
    identity -- sent on the wire only when the caller passes it
    (`BUZON-ORIGEN-WIRE-1`, behind `Config.enviar_origen`). `None` (the
    default, and every caller before this slice) omits the key entirely,
    rather than sending it as JSON `null`: `EntradaBuzonMeta`'s
    `extra="forbid"` answers `422` to an unknown key regardless of its
    value, and before Finanzas Release 37 deploys `origen` is an unknown
    key."""

    fuente: FuenteWire
    id_externo: str
    remitente: str
    asunto: str | None
    recibido_en: datetime.datetime
    cuerpo_texto: str | None
    origen: str | None = None

    def como_json(self) -> str:
        datos: dict[str, object] = {
            "fuente": self.fuente,
            "id_externo": self.id_externo,
            "remitente": self.remitente,
            "asunto": self.asunto,
            "recibido_en": self.recibido_en.isoformat(),
            "cuerpo_texto": self.cuerpo_texto,
        }
        if self.origen is not None:
            datos["origen"] = self.origen
        return json.dumps(datos)


class FinanzasClient:
    def __init__(self, config: Config, *, transport: httpx.BaseTransport | None = None) -> None:
        self._token = config.finanzas_token
        self._cliente = httpx.Client(
            base_url=config.url_finanzas, transport=transport, timeout=temporizadores.TIMEOUT_HTTPX
        )

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
