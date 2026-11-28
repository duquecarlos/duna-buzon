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


# ---------------------------------------------------------------------------
# BUZON-CARPETAS-CONTEO-1 -- `primera_pasada_en`: set once, on the FIRST
# pass this connector ever attempted for a source, the silence alert's own
# fallback for a source that has NEVER once seen mail.
# ---------------------------------------------------------------------------


def test_cargar_primera_pasada_sin_marcador_es_none(tmp_path: Path) -> None:
    assert alerta.cargar_primera_pasada_en(str(tmp_path)) is None


def test_marcar_primera_pasada_escribe_el_momento(tmp_path: Path) -> None:
    momento = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_primera_pasada(str(tmp_path), momento)
    assert alerta.cargar_primera_pasada_en(str(tmp_path)) == momento


def test_marcar_primera_pasada_dos_veces_conserva_el_primer_momento(tmp_path: Path) -> None:
    # Set once -- unlike `marcar_ultimo_visto`'s own latest-sighting
    # marker, every later pass is a no-op so this always names when this
    # connector FIRST started watching the source.
    primero = datetime.datetime(2026, 11, 20, 8, 0, 0, tzinfo=datetime.UTC)
    segundo = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=datetime.UTC)
    alerta.marcar_primera_pasada(str(tmp_path), primero)
    alerta.marcar_primera_pasada(str(tmp_path), segundo)
    assert alerta.cargar_primera_pasada_en(str(tmp_path)) == primero


# ---------------------------------------------------------------------------
# BUZON-SILENCIO-SIN-CORREO-NUEVO-1 -- `carpetas_actual`/`carpetas_base`:
# each a `{carpeta: uidnext}` map, `actual` always overwritten,
# `base` overwritten only when the caller (`cli.py`) decides the window
# has restarted.
# ---------------------------------------------------------------------------


def test_cargar_carpetas_actual_sin_marcador_es_none(tmp_path: Path) -> None:
    assert alerta.cargar_carpetas_actual(str(tmp_path)) is None


def test_marcar_carpetas_actual_escribe_el_mapa(tmp_path: Path) -> None:
    alerta.marcar_carpetas_actual(str(tmp_path), {"INBOX": 36, "Newsletter": 2})
    assert alerta.cargar_carpetas_actual(str(tmp_path)) == {"INBOX": 36, "Newsletter": 2}


def test_marcar_carpetas_actual_dos_veces_conserva_el_segundo_mapa(tmp_path: Path) -> None:
    alerta.marcar_carpetas_actual(str(tmp_path), {"INBOX": 36})
    alerta.marcar_carpetas_actual(str(tmp_path), {"INBOX": 40})
    assert alerta.cargar_carpetas_actual(str(tmp_path)) == {"INBOX": 40}


def test_cargar_carpetas_base_sin_marcador_es_none(tmp_path: Path) -> None:
    assert alerta.cargar_carpetas_base(str(tmp_path)) is None


def test_marcar_carpetas_base_escribe_el_mapa(tmp_path: Path) -> None:
    alerta.marcar_carpetas_base(str(tmp_path), {"INBOX": 36, "Newsletter": 2})
    assert alerta.cargar_carpetas_base(str(tmp_path)) == {"INBOX": 36, "Newsletter": 2}


def test_marcar_carpetas_base_dos_veces_conserva_el_segundo_mapa(tmp_path: Path) -> None:
    # Unlike `marcar_primera_pasada`'s own set-once marker, this function
    # always overwrites -- it is the CALLER's job to decide when that
    # should happen, never this one's.
    alerta.marcar_carpetas_base(str(tmp_path), {"INBOX": 36})
    alerta.marcar_carpetas_base(str(tmp_path), {"INBOX": 40})
    assert alerta.cargar_carpetas_base(str(tmp_path)) == {"INBOX": 40}
