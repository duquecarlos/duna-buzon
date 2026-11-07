from __future__ import annotations

from pathlib import Path

import pytest

from duna_buzon.reglas import MensajeCandidato, Reglas, ReglasInvalidasError, cargar_reglas, coincide

import datetime


def _reglas() -> Reglas:
    return Reglas(
        desde=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        remitentes=("notificaciones@banco-ejemplo.com", "ejemplo-empleador.com"),
        palabras=("factura", "comprobante", "nomina"),
    )


def test_regla_a_direccion_exacta_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="notificaciones@banco-ejemplo.com", asunto="hola", nombres_adjuntos=[]
    )
    assert coincide(candidato, _reglas())


def test_regla_a_dominio_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="nomina@ejemplo-empleador.com", asunto="nada relevante", nombres_adjuntos=[]
    )
    assert coincide(candidato, _reglas())


def test_regla_a_no_coincide_con_otro_remitente() -> None:
    candidato = MensajeCandidato(remitente="alguien@otro-dominio.com", asunto=None, nombres_adjuntos=[])
    assert not coincide(candidato, _reglas())


def test_regla_b_adjunto_pdf_mas_palabra_en_asunto_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="Tu factura de este mes",
        nombres_adjuntos=["detalle.pdf"],
    )
    assert coincide(candidato, _reglas())


def test_regla_b_es_insensible_a_mayusculas_y_acentos() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="Tu NÓMINA de este mes",
        nombres_adjuntos=["detalle.PDF"],
    )
    assert coincide(candidato, _reglas())


def test_regla_b_adjunto_sin_palabra_no_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="Foto de las vacaciones",
        nombres_adjuntos=["detalle.pdf"],
    )
    assert not coincide(candidato, _reglas())


def test_regla_b_palabra_sin_adjunto_reconocido_no_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="Tu factura de este mes",
        nombres_adjuntos=["foto.jpg"],
    )
    assert not coincide(candidato, _reglas())


def test_regla_b_palabra_en_remitente_tambien_coincide() -> None:
    candidato = MensajeCandidato(
        remitente="facturas@otro-dominio.com",
        asunto="sin relacion",
        nombres_adjuntos=["adjunto.xml"],
    )
    assert coincide(candidato, _reglas())


def test_regla_c_adjunto_zip_coincide_sin_nada_mas() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com", asunto="asunto cualquiera", nombres_adjuntos=["bundle.zip"]
    )
    assert coincide(candidato, _reglas())


def test_regla_c_adjunto_xml_coincide_sin_nada_mas() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com", asunto="asunto cualquiera", nombres_adjuntos=["factura.xml"]
    )
    assert coincide(candidato, _reglas())


def test_sin_coincidencia_cuando_nada_aplica() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="asunto cualquiera",
        nombres_adjuntos=["foto.jpg"],
    )
    assert not coincide(candidato, _reglas())


def test_adjunto_jpg_no_dispara_regla_b_ni_c() -> None:
    candidato = MensajeCandidato(
        remitente="alguien@otro-dominio.com",
        asunto="factura adjunta",
        nombres_adjuntos=["foto.jpg"],
    )
    assert not coincide(candidato, _reglas())


def test_cargar_reglas_desde_archivo(tmp_path: Path) -> None:
    ruta = tmp_path / "reglas.json"
    ruta.write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": ["a@b.com"], "palabras": ["factura"]}',
        encoding="utf-8",
    )
    reglas = cargar_reglas(ruta)
    assert reglas.remitentes == ("a@b.com",)
    assert reglas.palabras == ("factura",)


def test_cargar_reglas_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ReglasInvalidasError):
        cargar_reglas(tmp_path / "no-existe.json")


def test_cargar_reglas_json_invalido(tmp_path: Path) -> None:
    ruta = tmp_path / "reglas.json"
    ruta.write_text("esto no es json", encoding="utf-8")
    with pytest.raises(ReglasInvalidasError):
        cargar_reglas(ruta)


def test_cargar_reglas_campo_faltante(tmp_path: Path) -> None:
    ruta = tmp_path / "reglas.json"
    ruta.write_text('{"desde": "2026-01-01T00:00:00+00:00", "remitentes": []}', encoding="utf-8")
    with pytest.raises(ReglasInvalidasError):
        cargar_reglas(ruta)


def test_ejemplo_enviado_no_contiene_remitentes_reales() -> None:
    ruta = Path(__file__).resolve().parents[1] / "deploy" / "reglas.example.json"
    reglas = cargar_reglas(ruta)
    # Only generic placeholders -- never a real bank or employer domain.
    assert all("ejemplo" in remitente for remitente in reglas.remitentes)
