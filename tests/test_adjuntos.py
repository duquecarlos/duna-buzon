from __future__ import annotations

import json

from duna_buzon.adjuntos import (
    LIMITE_ADJUNTOS_POR_ENTRADA,
    LIMITE_CUERPO_BYTES,
    LIMITE_TAMANO_ARCHIVO_BYTES,
    AdjuntoCandidato,
    empacar,
    sniff_tipo_contenido,
    tamano_cuerpo_multipart,
)

_META_JSON = json.dumps(
    {
        "fuente": "correo",
        "id_externo": "ext-1",
        "remitente": "banco@ejemplo.com",
        "asunto": "alerta",
        "recibido_en": "2026-03-01T10:00:00+00:00",
        "cuerpo_texto": "cuerpo",
    }
)


def _pdf(tamano: int) -> bytes:
    return b"%PDF-1.4\n" + (b"0" * tamano)


# ---------------------------------------------------------------------------
# Sniffing
# ---------------------------------------------------------------------------


def test_sniff_pdf() -> None:
    assert sniff_tipo_contenido(b"%PDF-1.4\n...") == "application/pdf"


def test_sniff_jpeg() -> None:
    assert sniff_tipo_contenido(b"\xff\xd8\xff\xe0resto") == "image/jpeg"


def test_sniff_png() -> None:
    assert sniff_tipo_contenido(b"\x89PNG\r\n\x1a\nresto") == "image/png"


def test_sniff_webp() -> None:
    contenido = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"resto"
    assert sniff_tipo_contenido(contenido) == "image/webp"


def test_sniff_xml() -> None:
    assert sniff_tipo_contenido(b"<?xml version='1.0'?><a/>") == "application/xml"


def test_sniff_xml_con_bom() -> None:
    assert sniff_tipo_contenido(b"\xef\xbb\xbf<?xml version='1.0'?>") == "application/xml"


def test_sniff_zip() -> None:
    assert sniff_tipo_contenido(b"PK\x03\x04resto") == "application/zip"


def test_sniff_desconocido_es_none() -> None:
    assert sniff_tipo_contenido(b"esto no es nada reconocido") is None


def test_sniff_vacio_es_none() -> None:
    assert sniff_tipo_contenido(b"") is None


# ---------------------------------------------------------------------------
# Packing: the per-file 10 MB cap, the 10-attachment cap, and the exact
# 12,582,912-byte whole-request ceiling -- MEASURED, via the real multipart
# encoding, never estimated.
# ---------------------------------------------------------------------------


def test_adjunto_mayor_a_10mb_se_descarta() -> None:
    grande = AdjuntoCandidato(nombre="grande.pdf", contenido=_pdf(LIMITE_TAMANO_ARCHIVO_BYTES + 1))
    resultado = empacar(_META_JSON, [grande])
    assert resultado.incluidos == ()
    assert resultado.descartados == 1


def test_adjunto_de_exactamente_10mb_se_incluye() -> None:
    # The file's own content is exactly at the per-file ceiling -- still
    # accepted on that ground; it may still be dropped by the whole-body
    # ceiling, which this candidate alone does not reach.
    justo = AdjuntoCandidato(nombre="justo.pdf", contenido=_pdf(LIMITE_TAMANO_ARCHIVO_BYTES - 9))
    assert len(justo.contenido) == LIMITE_TAMANO_ARCHIVO_BYTES
    resultado = empacar(_META_JSON, [justo])
    assert len(resultado.incluidos) == 1
    assert resultado.descartados == 0


def test_tipo_no_reconocido_se_descarta() -> None:
    desconocido = AdjuntoCandidato(nombre="misterio.bin", contenido=b"nada reconocible")
    resultado = empacar(_META_JSON, [desconocido])
    assert resultado.incluidos == ()
    assert resultado.descartados == 1


def test_maximo_10_adjuntos_por_entrada() -> None:
    candidatos = [AdjuntoCandidato(nombre=f"a{i}.pdf", contenido=_pdf(10)) for i in range(12)]
    resultado = empacar(_META_JSON, candidatos)
    assert len(resultado.incluidos) == LIMITE_ADJUNTOS_POR_ENTRADA
    assert resultado.descartados == 2


def test_empaque_mide_el_cuerpo_real_en_el_limite_exacto() -> None:
    # Raw bytes added to a candidate's content add exactly that many bytes
    # to the encoded body (multipart carries them verbatim) -- confirmed
    # here by MEASURING, not assuming, before relying on it to locate the
    # exact boundary.
    base = tamano_cuerpo_multipart(_META_JSON, [("a.pdf", _pdf(0), "application/pdf")])
    con_mil_mas = tamano_cuerpo_multipart(_META_JSON, [("a.pdf", _pdf(1000), "application/pdf")])
    assert con_mil_mas - base == 1000

    # A single attachment can never reach the 12,582,912-byte whole-body
    # ceiling without first tripping the 10 MB per-file ceiling (10 MB <
    # 12 MiB), so the boundary is exercised with TWO: a first one
    # comfortably under 10 MB, and a second sized so the combined body
    # lands exactly at the ceiling.
    primero = _pdf(9_000_000)
    assert len(primero) <= LIMITE_TAMANO_ARCHIVO_BYTES

    base_con_primero = tamano_cuerpo_multipart(
        _META_JSON, [("primero.pdf", primero, "application/pdf"), ("segundo.pdf", _pdf(0), "application/pdf")]
    )
    segundo_en_el_limite = _pdf(LIMITE_CUERPO_BYTES - base_con_primero)
    assert len(segundo_en_el_limite) <= LIMITE_TAMANO_ARCHIVO_BYTES
    assert (
        tamano_cuerpo_multipart(
            _META_JSON,
            [
                ("primero.pdf", primero, "application/pdf"),
                ("segundo.pdf", segundo_en_el_limite, "application/pdf"),
            ],
        )
        == LIMITE_CUERPO_BYTES
    )

    resultado_en_el_limite = empacar(
        _META_JSON,
        [
            AdjuntoCandidato(nombre="primero.pdf", contenido=primero),
            AdjuntoCandidato(nombre="segundo.pdf", contenido=segundo_en_el_limite),
        ],
    )
    assert len(resultado_en_el_limite.incluidos) == 2
    assert resultado_en_el_limite.descartados == 0

    segundo_un_byte_mas = segundo_en_el_limite + b"0"
    resultado_un_byte_mas = empacar(
        _META_JSON,
        [
            AdjuntoCandidato(nombre="primero.pdf", contenido=primero),
            AdjuntoCandidato(nombre="segundo.pdf", contenido=segundo_un_byte_mas),
        ],
    )
    assert [nombre for nombre, _, _ in resultado_un_byte_mas.incluidos] == ["primero.pdf"]
    assert resultado_un_byte_mas.descartados == 1


def test_lo_que_no_cabe_se_descarta_y_se_sigue_intentando_con_el_resto() -> None:
    base = tamano_cuerpo_multipart(_META_JSON, [])
    espacio_disponible = LIMITE_CUERPO_BYTES - base

    primero = AdjuntoCandidato(nombre="primero.pdf", contenido=_pdf(espacio_disponible // 2))
    base_tras_primero = tamano_cuerpo_multipart(
        _META_JSON, [("primero.pdf", primero.contenido, "application/pdf")]
    )
    espacio_restante = LIMITE_CUERPO_BYTES - base_tras_primero

    # Deliberately too big to follow "primero" -- this one is dropped.
    segundo = AdjuntoCandidato(nombre="segundo.pdf", contenido=_pdf(espacio_restante + 1000))
    # Comfortably small enough to still fit after "segundo" was skipped.
    tercero = AdjuntoCandidato(nombre="tercero.pdf", contenido=_pdf(10))

    resultado = empacar(_META_JSON, [primero, segundo, tercero])
    nombres_incluidos = [nombre for nombre, _, _ in resultado.incluidos]
    assert nombres_incluidos == ["primero.pdf", "tercero.pdf"]
    assert resultado.descartados == 1
