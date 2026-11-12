from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from duna_buzon import cripto
from duna_buzon.fuentes import (
    FuenteConfig,
    FuenteInvalidaError,
    cargar_fuente,
    directorio_fuente,
    fuentes_activas,
    guardar_fuente,
    id_fuente_cuenta,
    listar_fuentes,
    migrar_fuente_unica_si_necesario,
)

_CLAVE = base64.b64decode(base64.b64encode(b"\x02" * 32))


def _fuente(**over) -> FuenteConfig:
    base = dict(
        id="hotmail-carlos",
        proveedor="microsoft",
        direccion="carlos@hotmail.com",
        etiqueta="Hotmail",
        token_finanzas_ref="carlos",
        activa=True,
    )
    base.update(over)
    return FuenteConfig(**base)


def test_etiqueta_registro_combina_proveedor_e_id() -> None:
    assert _fuente().etiqueta_registro == "microsoft:hotmail-carlos"


def test_guardar_y_cargar_ida_y_vuelta(tmp_path: Path) -> None:
    guardar_fuente(str(tmp_path), _fuente())
    cargada = cargar_fuente(str(tmp_path), "hotmail-carlos")
    assert cargada == _fuente()


def test_origen_por_defecto_es_local() -> None:
    assert _fuente().origen == "local"


def test_fuente_json_sin_campo_origen_se_lee_como_local(tmp_path: Path) -> None:
    # A `fuente.json` written before BUZON-FUENTES-PULL-1 never had an
    # `origen` key at all -- it must keep meaning exactly what it always
    # meant.
    ruta = tmp_path / "fuentes" / "hotmail-carlos"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "proveedor": "microsoft",
                "direccion": "carlos@hotmail.com",
                "etiqueta": "Hotmail",
                "token_finanzas_ref": "carlos",
                "activa": True,
            }
        ),
        encoding="utf-8",
    )
    assert cargar_fuente(str(tmp_path), "hotmail-carlos").origen == "local"


def test_guardar_y_cargar_una_fuente_de_cuenta_ida_y_vuelta(tmp_path: Path) -> None:
    fuente_cuenta = _fuente(
        id="cuenta-abc123", proveedor="google", direccion="carlos@gmail.com", origen="cuenta"
    )
    guardar_fuente(str(tmp_path), fuente_cuenta)
    assert cargar_fuente(str(tmp_path), "cuenta-abc123") == fuente_cuenta


def test_id_fuente_cuenta_antepone_el_prefijo() -> None:
    assert id_fuente_cuenta("abc123") == "cuenta-abc123"


def test_cargar_fuente_origen_desconocido_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "rara"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "proveedor": "microsoft",
                "direccion": "a@b.com",
                "etiqueta": "x",
                "token_finanzas_ref": "carlos",
                "activa": True,
                "origen": "remoto",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "rara")


def test_cargar_fuente_google_sin_origen_cuenta_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "rara"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "proveedor": "google",
                "direccion": "a@b.com",
                "etiqueta": "x",
                "token_finanzas_ref": "carlos",
                "activa": True,
                "origen": "local",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "rara")


def test_cargar_fuente_imap_con_origen_cuenta_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "rara"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "proveedor": "imap",
                "direccion": "a@b.com",
                "etiqueta": "x",
                "token_finanzas_ref": "carlos",
                "activa": True,
                "origen": "cuenta",
                "host": "imap.zoho.com",
                "port": 993,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "rara")


def test_archivo_de_fuente_es_modo_0600(tmp_path: Path, no_es_windows: None) -> None:
    guardar_fuente(str(tmp_path), _fuente())
    ruta = tmp_path / "fuentes" / "hotmail-carlos" / "fuente.json"
    assert cripto.modo_octal(ruta) == 0o600


def test_cargar_fuente_inexistente(tmp_path: Path) -> None:
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "no-existe")


def test_cargar_fuente_json_invalido(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "rota"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text("esto no es json", encoding="utf-8")
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "rota")


def test_cargar_fuente_campo_faltante(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "incompleta"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps({"proveedor": "microsoft", "direccion": "a@b.com"}), encoding="utf-8"
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "incompleta")


def test_cargar_fuente_proveedor_desconocido(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "rara"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "proveedor": "pop3",
                "direccion": "a@b.com",
                "etiqueta": "x",
                "token_finanzas_ref": "carlos",
                "activa": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "rara")


def test_cargar_fuente_id_interno_distinto_del_directorio(tmp_path: Path) -> None:
    ruta = tmp_path / "fuentes" / "hotmail-carlos"
    ruta.mkdir(parents=True)
    (ruta / "fuente.json").write_text(
        json.dumps(
            {
                "id": "otro-id",
                "proveedor": "microsoft",
                "direccion": "a@b.com",
                "etiqueta": "x",
                "token_finanzas_ref": "carlos",
                "activa": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FuenteInvalidaError):
        cargar_fuente(str(tmp_path), "hotmail-carlos")


def test_listar_fuentes_vacio_sin_directorio(tmp_path: Path) -> None:
    assert listar_fuentes(str(tmp_path)) == []


def test_listar_fuentes_ordenadas_por_id(tmp_path: Path) -> None:
    guardar_fuente(str(tmp_path), _fuente(id="z-fuente"))
    guardar_fuente(str(tmp_path), _fuente(id="a-fuente"))
    (tmp_path / "fuentes" / "sin-json").mkdir(parents=True)

    fuentes = listar_fuentes(str(tmp_path))
    assert [f.id for f in fuentes] == ["a-fuente", "z-fuente"]


def test_fuentes_activas_filtra_las_inactivas(tmp_path: Path) -> None:
    guardar_fuente(str(tmp_path), _fuente(id="activa", activa=True))
    guardar_fuente(str(tmp_path), _fuente(id="inactiva", activa=False))

    activas = fuentes_activas(str(tmp_path))
    assert [f.id for f in activas] == ["activa"]


# ---------------------------------------------------------------------------
# Migration from the single-mailbox layout
# ---------------------------------------------------------------------------


def test_migracion_no_hace_nada_sin_nada_legado(tmp_path: Path) -> None:
    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is False
    assert listar_fuentes(str(tmp_path)) == []


def test_migracion_no_hace_nada_si_fuentes_ya_existe(tmp_path: Path) -> None:
    guardar_fuente(str(tmp_path), _fuente(id="ya-migrada"))
    (tmp_path / "cursor.json").write_text('{"cursor": "2026-01-01T00:00:00+00:00"}', encoding="utf-8")

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is False
    # The legacy file is left untouched -- migration never runs once
    # `fuentes/` exists, so it never even looks at it.
    assert (tmp_path / "cursor.json").is_file()
    assert [f.id for f in listar_fuentes(str(tmp_path))] == ["ya-migrada"]


def test_migracion_preserva_el_cursor_exacto(tmp_path: Path) -> None:
    (tmp_path / "cursor.json").write_text('{"cursor": "2026-03-01T10:30:00+00:00"}', encoding="utf-8")
    (tmp_path / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": ["a@b.com"], "palabras": []}',
        encoding="utf-8",
    )
    (tmp_path / "estado.json").write_bytes(b"vistos=3 reenviados=2")

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is True

    destino = directorio_fuente(str(tmp_path), "hotmail-carlos")
    assert json.loads((destino / "cursor.json").read_text())["cursor"] == "2026-03-01T10:30:00+00:00"
    assert json.loads((destino / "reglas.json").read_text())["remitentes"] == ["a@b.com"]
    assert (destino / "estado.json").read_bytes() == b"vistos=3 reenviados=2"

    # Nothing legacy is left behind at the root.
    assert not (tmp_path / "cursor.json").exists()
    assert not (tmp_path / "reglas.json").exists()
    assert not (tmp_path / "estado.json").exists()

    fuente = cargar_fuente(str(tmp_path), "hotmail-carlos")
    assert fuente.proveedor == "microsoft"
    assert fuente.activa is True


def test_migracion_es_idempotente(tmp_path: Path) -> None:
    (tmp_path / "cursor.json").write_text('{"cursor": "2026-03-01T10:30:00+00:00"}', encoding="utf-8")

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is True
    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is False

    destino = directorio_fuente(str(tmp_path), "hotmail-carlos")
    assert json.loads((destino / "cursor.json").read_text())["cursor"] == "2026-03-01T10:30:00+00:00"


def test_migracion_recupera_la_direccion_de_la_cache_msal(tmp_path: Path) -> None:
    contenido_cache = json.dumps(
        {"Account": {"clave-1": {"username": "carlos@hotmail.com", "home_account_id": "x"}}}
    ).encode("utf-8")
    (tmp_path / "cache.bin").write_bytes(cripto.cifrar(contenido_cache, _CLAVE))

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is True

    fuente = cargar_fuente(str(tmp_path), "hotmail-carlos")
    assert fuente.direccion == "carlos@hotmail.com"

    destino = directorio_fuente(str(tmp_path), "hotmail-carlos")
    descifrado = cripto.descifrar((destino / "credencial.bin").read_bytes(), _CLAVE)
    assert descifrado == contenido_cache
    assert not (tmp_path / "cache.bin").exists()


def test_migracion_con_clave_equivocada_no_falla_direccion_vacia(tmp_path: Path) -> None:
    otra_clave = bytes((b + 1) % 256 for b in _CLAVE)
    (tmp_path / "cache.bin").write_bytes(cripto.cifrar(b"{}", otra_clave))

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is True

    fuente = cargar_fuente(str(tmp_path), "hotmail-carlos")
    assert fuente.direccion == ""
    # The ciphertext still moved -- migration never loses the credential,
    # even when it cannot read an address out of it under this key.
    destino = directorio_fuente(str(tmp_path), "hotmail-carlos")
    assert (destino / "credencial.bin").is_file()
    assert not (tmp_path / "cache.bin").exists()


def test_migracion_solo_con_reglas_json(tmp_path: Path) -> None:
    # A fresh install where the operator wrote reglas.json by hand but
    # nobody has run `entrar` yet -- still migrates, with no credential to
    # carry.
    (tmp_path / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
    )

    assert migrar_fuente_unica_si_necesario(str(tmp_path), _CLAVE) is True
    destino = directorio_fuente(str(tmp_path), "hotmail-carlos")
    assert (destino / "reglas.json").is_file()
    assert not (destino / "credencial.bin").exists()
