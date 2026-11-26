from __future__ import annotations

import datetime
from pathlib import Path

from duna_buzon import alerta


def test_cargar_sin_marcador_es_none(tmp_path: Path) -> None:
    assert alerta.cargar_auth_fallando_desde(str(tmp_path)) is None


def test_marcar_escribe_el_momento(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_auth_fallando(str(tmp_path), momento)
    assert alerta.cargar_auth_fallando_desde(str(tmp_path)) == momento


def test_marcar_dos_veces_conserva_el_primer_momento(tmp_path: Path) -> None:
    primero = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    segundo = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_auth_fallando(str(tmp_path), primero)
    alerta.marcar_auth_fallando(str(tmp_path), segundo)
    assert alerta.cargar_auth_fallando_desde(str(tmp_path)) == primero


def test_limpiar_borra_el_marcador(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_auth_fallando(str(tmp_path), momento)
    alerta.limpiar_auth_fallando(str(tmp_path))
    assert alerta.cargar_auth_fallando_desde(str(tmp_path)) is None


def test_limpiar_sin_marcador_no_falla(tmp_path: Path) -> None:
    alerta.limpiar_auth_fallando(str(tmp_path))
    assert alerta.cargar_auth_fallando_desde(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# BUZON-ALERTA-SILENCIO-1 -- `ultimo_visto_en`, a separate marker from the
# auth one above: no "clear" step, since every pass that sees mail simply
# moves it forward to that pass's own moment.
# ---------------------------------------------------------------------------


def test_cargar_ultimo_visto_sin_marcador_es_none(tmp_path: Path) -> None:
    assert alerta.cargar_ultimo_visto_en(str(tmp_path)) is None


def test_marcar_ultimo_visto_escribe_el_momento(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_ultimo_visto(str(tmp_path), momento)
    assert alerta.cargar_ultimo_visto_en(str(tmp_path)) == momento


def test_marcar_ultimo_visto_dos_veces_conserva_el_segundo_momento(tmp_path: Path) -> None:
    # Unlike `marcar_auth_fallando`'s own set-once marker, this one tracks
    # the LATEST sighting -- every call moves it forward.
    primero = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    segundo = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_ultimo_visto(str(tmp_path), primero)
    alerta.marcar_ultimo_visto(str(tmp_path), segundo)
    assert alerta.cargar_ultimo_visto_en(str(tmp_path)) == segundo
