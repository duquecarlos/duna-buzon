from __future__ import annotations

from duna_buzon.limites import (
    LIMITE_ASUNTO,
    LIMITE_ID_EXTERNO,
    LIMITE_REMITENTE,
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
