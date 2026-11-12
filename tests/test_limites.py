from __future__ import annotations

import typing

from duna_buzon.finanzas import MetaEntrada
from duna_buzon.limites import (
    FUENTE_CORREO,
    LIMITE_ASUNTO,
    LIMITE_FUENTE,
    LIMITE_ID_EXTERNO,
    LIMITE_ORIGEN,
    LIMITE_REMITENTE,
    FuenteWire,
    excede_limites_no_truncables,
    truncar_asunto,
)


def test_id_externo_exactamente_en_el_tope_no_excede() -> None:
    id_externo = "m" * LIMITE_ID_EXTERNO
    assert not excede_limites_no_truncables(id_externo=id_externo, remitente="a@b.com")


def test_id_externo_un_caracter_sobre_el_tope_excede() -> None:
    id_externo = "m" * (LIMITE_ID_EXTERNO + 1)
    assert excede_limites_no_truncables(id_externo=id_externo, remitente="a@b.com")


def test_remitente_exactamente_en_el_tope_no_excede() -> None:
    remitente = "a" * (LIMITE_REMITENTE - len("@b.com")) + "@b.com"
    assert len(remitente) == LIMITE_REMITENTE
    assert not excede_limites_no_truncables(id_externo="m1", remitente=remitente)


def test_remitente_un_caracter_sobre_el_tope_excede() -> None:
    remitente = "a" * (LIMITE_REMITENTE - len("@b.com") + 1) + "@b.com"
    assert len(remitente) == LIMITE_REMITENTE + 1
    assert excede_limites_no_truncables(id_externo="m1", remitente=remitente)


def test_asunto_exactamente_en_el_tope_no_se_trunca() -> None:
    asunto = "a" * LIMITE_ASUNTO
    resultado = truncar_asunto(asunto)
    assert resultado == asunto
    assert len(resultado) == LIMITE_ASUNTO


def test_asunto_un_caracter_sobre_el_tope_se_trunca() -> None:
    asunto = "a" * (LIMITE_ASUNTO + 1)
    resultado = truncar_asunto(asunto)
    assert len(resultado) == LIMITE_ASUNTO
    assert resultado == "a" * LIMITE_ASUNTO


def test_asunto_multibyte_sobre_el_tope_se_trunca_en_frontera_de_caracter() -> None:
    # "é" is one Python character but two UTF-8 bytes; "🙂" is one Python
    # character but four UTF-8 bytes. A byte-based truncation would split
    # one of these. Python string slicing counts characters, never bytes.
    asunto = "é" * (LIMITE_ASUNTO - 1) + "🙂🙂"
    assert len(asunto) == LIMITE_ASUNTO + 1
    resultado = truncar_asunto(asunto)
    assert len(resultado) == LIMITE_ASUNTO
    assert resultado == "é" * (LIMITE_ASUNTO - 1) + "🙂"
    # The result re-encodes cleanly -- no character was split.
    resultado.encode("utf-8")


# ---------------------------------------------------------------------------
# Contract test (BUZON-FUENTE-WIRE-CORREO-1): the wire's own `fuente` field,
# restated verbatim from Finanzas and never guessed.
#
#   src/duna_finanzas/web/buzon/schemas.py:37
#       Fuente = Literal["correo", "sms", "notificacion"]
#   src/duna_finanzas/storage/schema.py:6560 (repeated at 6709, 6725)
#       a CHECK constraint `fuente_conocida` on String(16)
# ---------------------------------------------------------------------------

_FUENTE_PERMITIDA_POR_FINANZAS = ("correo", "sms", "notificacion")
_LIMITE_FUENTE_SEGUN_FINANZAS = 16


def test_fuentewire_restablece_el_literal_de_finanzas() -> None:
    assert typing.get_args(FuenteWire) == _FUENTE_PERMITIDA_POR_FINANZAS


def test_limite_fuente_restablece_el_tope_de_finanzas() -> None:
    assert LIMITE_FUENTE == _LIMITE_FUENTE_SEGUN_FINANZAS


def test_meta_entrada_declara_fuente_como_fuentewire() -> None:
    # `MetaEntrada.fuente`'s own type IS `limites.FuenteWire`, not a bare
    # `str` -- a value outside Finanzas' three accepted strings is a type
    # error at every call site, not just a runtime risk.
    anotaciones = typing.get_type_hints(MetaEntrada, include_extras=True)
    assert anotaciones["fuente"] == FuenteWire


def test_fuente_correo_es_la_unica_que_el_codigo_construye_y_pasa_el_contrato() -> None:
    # `FUENTE_CORREO` is the one value `sincronizar.ejecutar_pasada` ever
    # puts in `MetaEntrada.fuente`, for every provider (`microsoft`, `imap`
    # alike) -- every `MetaEntrada` this codebase can build passes both
    # halves of Finanzas' own contract.
    assert FUENTE_CORREO in typing.get_args(FuenteWire)
    assert len(FUENTE_CORREO) <= LIMITE_FUENTE


# ---------------------------------------------------------------------------
# Contract test (BUZON-ORIGEN-WIRE-1): `origen`'s own wire cap, restated
# verbatim from Finanzas and never guessed.
#
#   duna-finanzas-api main 38a9e6d1, migration 530cb968d327
#       EntradaBuzonMeta.origen (web/buzon/schemas.py:66): optional string,
#       max 64 characters -- DB CHECK `origen_acotado`; 65+ is a 422.
# ---------------------------------------------------------------------------

_LIMITE_ORIGEN_SEGUN_FINANZAS = 64


def test_limite_origen_restablece_el_tope_de_finanzas() -> None:
    assert LIMITE_ORIGEN == _LIMITE_ORIGEN_SEGUN_FINANZAS
