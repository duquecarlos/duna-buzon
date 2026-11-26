from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

import httpx

from duna_buzon import alerta as modulo_alerta
from duna_buzon import auth, cli, credencial_imap, cripto
from duna_buzon.config import Config
from duna_buzon.cuenta import CuentaPullError, FuenteCuenta
from duna_buzon.fuentes import FuenteConfig, cargar_fuente, guardar_fuente

from .conftest import CLAVE_VALIDA_BASE64


def _config(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )


def _config_con_origen(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_ENVIAR_ORIGEN": "1",
        }
    )


def _escribir_fuente(
    tmp_path: Path,
    *,
    id: str = "hotmail-carlos",
    proveedor: str = "microsoft",
    direccion: str = "carlos@hotmail.com",
    etiqueta: str = "Hotmail",
    token_finanzas_ref: str = "carlos",
    activa: bool = True,
    host: str | None = None,
    port: int | None = None,
    carpetas: tuple[str, ...] | None = None,
) -> Path:
    if proveedor == "imap":
        host = host or "imap.zoho.com"
        port = port or 993
    guardar_fuente(
        str(tmp_path),
        FuenteConfig(
            id=id,
            proveedor=proveedor,  # type: ignore[arg-type]
            direccion=direccion,
            etiqueta=etiqueta,
            token_finanzas_ref=token_finanzas_ref,
            activa=activa,
            host=host,
            port=port,
            carpetas=carpetas,
        ),
    )
    return tmp_path / "fuentes" / id


def _escribir_reglas_vacias(directorio: Path) -> None:
    (directorio / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
    )


def test_estado_sin_nada_todavia_es_saludable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "cursor=none" in salida.out


def test_estado_reporta_cursor_y_la_ultima_pasada(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    import datetime

    from duna_buzon import cursor as modulo_cursor

    directorio = _escribir_fuente(tmp_path)
    modulo_cursor.guardar_cursor(str(directorio), datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC))
    cripto.escribir_archivo_sensible(directorio / cli.NOMBRE_ARCHIVO_ESTADO, b"vistos=1 reenviados=1")

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos cursor=2026-03-01" in salida.out
    assert "fuente=hotmail-carlos vistos=1 reenviados=1" in salida.out


def test_estado_fuente_nunca_entrada_sugiere_entrar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # BUZON-ESTADO-FUENTE-NUEVA-NUDGE-1 -- a source with no cursor AND no
    # credencial.bin never even ran `entrar`; the next step is that, not
    # `sincronizar`.
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos")

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "fuente=hotmail-carlos cursor=none" in salida
    assert "fuente=hotmail-carlos siguiente: ejecute 'entrar --fuente hotmail-carlos' para firmar" in salida
    assert "sincronizar" not in salida


def test_estado_fuente_entrada_pero_sin_sincronizar_sugiere_sincronizar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path, id="hotmail-carlos")
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"{}", config.clave_cifrado)
    )

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "fuente=hotmail-carlos cursor=none" in salida
    assert "fuente=hotmail-carlos siguiente: ejecute 'sincronizar' para la primera pasada" in salida
    assert "entrar" not in salida


def test_estado_fuente_de_cuenta_sin_sincronizar_sugiere_sincronizar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A `origen="cuenta"` source never has a `credencial.bin` at all --
    # `entrar` is never its next step.
    config = _config(tmp_path)
    guardar_fuente(
        str(tmp_path),
        FuenteConfig(
            id="cuenta-abc123",
            proveedor="microsoft",
            direccion="carlos@outlook.com",
            etiqueta="carlos@outlook.com",
            token_finanzas_ref="carlos",
            activa=True,
            origen="cuenta",
        ),
    )

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "fuente=cuenta-abc123 siguiente: ejecute 'sincronizar' para la primera pasada" in salida
    assert "entrar" not in salida


def test_estado_con_clave_equivocada_falla(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    otra_clave = bytes((b + 1) % 256 for b in config.clave_cifrado)
    directorio = _escribir_fuente(tmp_path)
    cripto.escribir_archivo_sensible(directorio / "credencial.bin", cripto.cifrar(b"{}", otra_clave))

    assert cli.comando_estado(config) == 1
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos" in salida.err
    assert "key" in salida.err.lower()


def _agregar_pasada_historial(
    directorio: Path, *, finalizado_en: str, vistos: int = 0, errors: str | None = None
) -> None:
    from duna_buzon import historial

    historial.agregar_pasada(
        str(directorio),
        historial.RegistroPasada(
            finalizado_en=finalizado_en,
            vistos=vistos,
            reenviados=vistos,
            duplicados=0,
            omitidos=0,
            errors=errors,
        ),
    )


def test_estado_sin_historial_no_muestra_tabla(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # BUZON-ESTADO-HISTORIAL-PASADAS-1 -- a source that never ran a second
    # pass has no PREVIOUS pass to show; "no history yet" is silence, not
    # an empty table.
    config = _config(tmp_path)
    _escribir_fuente(tmp_path)

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "historial" not in salida


def test_estado_una_sola_pasada_es_la_de_hoy_sin_tabla(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Exactly one recorded pass is "today"'s own -- already shown via
    # estado.json -- so the table has nothing to add.
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    _agregar_pasada_historial(directorio, finalizado_en="2026-11-19T10:00:00Z", vistos=3)

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "historial" not in salida


def test_estado_muestra_las_pasadas_previas_mas_recientes_primero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    for n in range(1, 4):
        _agregar_pasada_historial(directorio, finalizado_en=f"2026-11-{n:02d}T10:00:00Z", vistos=n)

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    lineas = [l for l in salida.splitlines() if "fuente=hotmail-carlos historial " in l]
    # Pass 3 is "today" (the latest) and is never in the table; passes 1
    # and 2 are PREVIOUS, most recent (2) printed first.
    assert len(lineas) == 2
    assert "2026-11-02T10:00:00Z" in lineas[0]
    assert "vistos=2" in lineas[0]
    assert "2026-11-01T10:00:00Z" in lineas[1]
    assert "vistos=1" in lineas[1]
    assert "2026-11-03T10:00:00Z" not in salida


def test_estado_tabla_lleva_el_motivo_de_una_pasada_fallida(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    _agregar_pasada_historial(
        directorio, finalizado_en="2026-11-01T10:00:00Z", vistos=0, errors="finanzas enviar http=500"
    )
    _agregar_pasada_historial(directorio, finalizado_en="2026-11-02T10:00:00Z", vistos=5)

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "errors=finanzas enviar http=500" in salida


def test_estado_pasada_sin_error_muestra_un_guion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    _agregar_pasada_historial(directorio, finalizado_en="2026-11-01T10:00:00Z", vistos=1)
    _agregar_pasada_historial(directorio, finalizado_en="2026-11-02T10:00:00Z", vistos=2)

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert "errors=-" in salida


def test_estado_respeta_el_numero_predeterminado_de_pasadas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    # PASADAS_PREDETERMINADAS previous passes, plus one more that is "today".
    for n in range(1, cli.PASADAS_PREDETERMINADAS + 2):
        _agregar_pasada_historial(directorio, finalizado_en=f"2026-11-{n:02d}T10:00:00Z", vistos=n)

    assert cli.comando_estado(config) == 0
    lineas = [
        l for l in capsys.readouterr().out.splitlines() if "fuente=hotmail-carlos historial " in l
    ]
    assert len(lineas) == cli.PASADAS_PREDETERMINADAS


def test_estado_pasadas_amplia_mas_alla_del_predeterminado(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon import historial

    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    for n in range(1, historial.CAPACIDAD + 1):
        _agregar_pasada_historial(directorio, finalizado_en=f"2026-11-{n:02d}T10:00:00Z", vistos=n)

    assert cli.comando_estado(config, pasadas=historial.CAPACIDAD) == 0
    lineas = [
        l for l in capsys.readouterr().out.splitlines() if "fuente=hotmail-carlos historial " in l
    ]
    # One entry (the latest) is "today", never in the table -- the cap
    # bounds storage, not how many of the remaining previous passes show.
    assert len(lineas) == historial.CAPACIDAD - 1


def test_estado_pasadas_mas_alla_del_tope_se_limita_a_lo_disponible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon import historial

    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    for n in range(1, 4):
        _agregar_pasada_historial(directorio, finalizado_en=f"2026-11-{n:02d}T10:00:00Z", vistos=n)

    assert cli.comando_estado(config, pasadas=historial.CAPACIDAD * 5) == 0
    lineas = [
        l for l in capsys.readouterr().out.splitlines() if "fuente=hotmail-carlos historial " in l
    ]
    assert len(lineas) == 2


def test_estado_pasadas_cero_no_muestra_tabla(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    for n in range(1, 4):
        _agregar_pasada_historial(directorio, finalizado_en=f"2026-11-{n:02d}T10:00:00Z", vistos=n)

    assert cli.comando_estado(config, pasadas=0) == 0
    assert "historial" not in capsys.readouterr().out


def test_estado_linea_de_historial_corrupta_se_descarta_con_una_nota(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from duna_buzon import historial

    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)

    def _linea(vistos: int, finalizado_en: str) -> str:
        return json.dumps(
            {
                "finalizado_en": finalizado_en,
                "vistos": vistos,
                "reenviados": vistos,
                "duplicados": 0,
                "omitidos": 0,
                "errors": None,
            }
        )

    # Written directly to the file (never through `agregar_pasada`, which
    # would self-heal the corrupt line away before `estado` ever reads it)
    # -- a valid pass, a corrupt line, then another valid (the "today")
    # pass.
    (directorio / historial.NOMBRE_ARCHIVO).write_text(
        _linea(1, "2026-11-01T10:00:00Z")
        + "\n"
        + "esto no es json\n"
        + _linea(2, "2026-11-02T10:00:00Z")
        + "\n",
        encoding="utf-8",
    )

    # The corrupt line must not fail the command, and the valid pass
    # around it still shows.
    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr()
    assert "1 linea(s) descartada(s)" in salida.err
    assert "fuente=hotmail-carlos" in salida.err
    assert "vistos=1" in salida.out


def test_entrar_delega_en_auth_entrar_con_la_sola_fuente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    llamado_con: list[tuple[Config, str]] = []

    def falso_entrar(cfg: Config, directorio_fuente: str) -> None:
        llamado_con.append((cfg, directorio_fuente))

    monkeypatch.setattr(auth, "entrar", falso_entrar)
    assert cli.comando_entrar(config, None) == 0
    assert llamado_con == [(config, str(directorio))]


def test_entrar_sin_fuente_y_varias_configuradas_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos")
    _escribir_fuente(tmp_path, id="gmail-carlos", proveedor="imap")

    assert cli.comando_entrar(config, None) == 1
    assert "--fuente" in capsys.readouterr().err


def test_entrar_con_id_desconocido_es_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path)

    assert cli.comando_entrar(config, "no-existe") == 1
    assert "no-existe" in capsys.readouterr().err


def test_entrar_sobre_fuente_imap_delega_en_credencial_imap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        host="imap.zoho.com",
        port=993,
    )
    llamado_con: list[tuple[Config, str, str, int, str]] = []

    def falso_entrar(cfg: Config, directorio_fuente: str, *, host: str, puerto: int, usuario: str) -> None:
        llamado_con.append((cfg, directorio_fuente, host, puerto, usuario))

    monkeypatch.setattr(credencial_imap, "entrar", falso_entrar)
    assert cli.comando_entrar(config, "legal-duna") == 0
    assert llamado_con == [(config, str(directorio), "imap.zoho.com", 993, "legal@duna.solutions")]


def test_entrar_sobre_fuente_imap_con_login_invalido_es_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        host="imap.zoho.com",
        port=993,
    )

    def falso_entrar(cfg: Config, directorio_fuente: str, *, host: str, puerto: int, usuario: str) -> None:
        raise credencial_imap.VerificacionLoginError("IMAP4.error")

    monkeypatch.setattr(credencial_imap, "entrar", falso_entrar)
    assert cli.comando_entrar(config, "legal-duna") == 1
    assert "imap login failed" in capsys.readouterr().err


def test_sincronizar_sin_fuentes_activas_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_sincronizar(config) == 1
    assert "no active sources" in capsys.readouterr().err


def test_sincronizar_sin_cuenta_retorna_1_y_nunca_toca_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    _escribir_reglas_vacias(directorio)

    def falso_obtener_token(cfg: Config, directorio_fuente: str):  # type: ignore[no-untyped-def]
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert "entrar" in salida.err
    assert "fuente=hotmail-carlos" in salida.err


def _escribir_fuente_y_reglas(tmp_path: Path) -> Path:
    directorio = _escribir_fuente(tmp_path)
    _escribir_reglas_vacias(directorio)
    return directorio


class _ProveedorFalso:
    def cerrar(self) -> None:
        pass


@pytest.mark.parametrize(
    "resultado_falso_kwargs,motivo_esperado",
    [
        (
            {"detenida_por_rechazo_permanente": True, "motivo": "finanzas enviar http=403"},
            "finanzas enviar http=403",
        ),
        (
            {"detenida_por_error_transitorio": True, "motivo": "finanzas enviar http=503"},
            "finanzas enviar http=503",
        ),
        (
            {"detenida_por_autenticacion": True, "motivo": "graph listar http=401"},
            "graph listar http=401",
        ),
        (
            {"detenida_por_error_transitorio": True, "motivo": "graph adjuntos error=ConnectError"},
            "graph adjuntos error=ConnectError",
        ),
    ],
)
def test_sincronizar_detenida_imprime_el_motivo_en_stderr_y_en_estado(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resultado_falso_kwargs: dict,
    motivo_esperado: str,
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=2, omitidos_por_reglas=1), **resultado_falso_kwargs
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == f"fuente=hotmail-carlos parada: {motivo_esperado}"
    assert f"fuente=hotmail-carlos vistos=2" in salida.out
    assert f"parada={motivo_esperado}" in salida.out

    assert cli.comando_estado(config) == 0
    salida_estado = capsys.readouterr()
    assert f"parada={motivo_esperado}" in salida_estado.out


def test_sincronizar_sin_detenerse_no_imprime_nada_en_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    resultado_falso = ResultadoPasada(contadores=Contadores(vistos=1))
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert salida.err == ""
    assert "parada=" not in salida.out


def test_sincronizar_registra_finalizado_en_y_reintentos_en_estado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-GRAPH-TIMEOUT-1: `estado.json` (and `estado`'s own stdout)
    now carry when the pass finished and how many Graph retries it used,
    so `duna-deploy-buzon status` can tell a stuck timer from repeated
    failures."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    class _ProveedorConReintentos:
        reintentos_usados = 2

        def cerrar(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorConReintentos(),
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1, reenviados=1))
    )
    monkeypatch.setattr(
        cli, "_ahora", lambda: datetime.datetime(2026, 11, 12, 17, 5, tzinfo=datetime.UTC)
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "reintentos=2" in salida.out
    assert "finalizado_en=2026-11-12T17:05:00Z" in salida.out

    assert cli.comando_estado(config) == 0
    salida_estado = capsys.readouterr()
    assert "reintentos=2" in salida_estado.out
    assert "finalizado_en=2026-11-12T17:05:00Z" in salida_estado.out


def test_sincronizar_registra_el_top_de_graph_en_estado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-GRAPH-502-1: `estado.json` (and `estado`'s own stdout) now
    also carry the `$top` the Graph LIST call was last using, next to the
    stop reason -- a provider with no such concept (IMAP, Gmail, every
    test fake that is not `GraphClient`) simply omits the field."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    class _ProveedorConTop:
        reintentos_usados = 1
        top_lista_usado = 10

        def cerrar(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorConTop(),
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1, reenviados=1))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "top=10" in salida.out

    assert cli.comando_estado(config) == 0
    salida_estado = capsys.readouterr()
    assert "top=10" in salida_estado.out


def test_sincronizar_sin_top_de_graph_no_lo_imprime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A provider with no `top_lista_usado` at all (IMAP, Gmail, the plain
    `_ProveedorFalso` test fake) never gets a guessed `top=` in the status
    line."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1, reenviados=1))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "top=" not in salida.out


def test_sincronizar_registra_omitidos_por_error_proveedor_en_estado_y_en_historial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-IMAP-MENSAJE-AISLADO-1: a provider that skipped a folder or a
    message of its own (`ClienteImap.omitidos_por_error_proveedor`, read
    the same way `top_lista_usado` already is) gets it printed in
    `estado`'s own line and folded into `historial.jsonl`'s `omitidos`
    total -- a provider with no such concept (Microsoft, Gmail,
    `_ProveedorFalso`) reports 0 and prints nothing, exactly like today."""
    from duna_buzon import historial
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    class _ProveedorConOmitidos:
        omitidos_por_error_proveedor = 3

        def cerrar(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorConOmitidos(),
    )
    monkeypatch.setattr(
        cli,
        "ejecutar_pasada",
        lambda **kwargs: ResultadoPasada(
            contadores=Contadores(vistos=4, reenviados=1, omitidos_por_reglas=0, omitidos_por_413_422=0)
        ),
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "omitidos_por_error_proveedor=3" in salida.out

    registros, descartadas = historial.leer_historial(directorio)
    assert descartadas == 0
    assert registros[-1].omitidos == 3


def test_sincronizar_registra_omitidos_por_fecha_proveedor_en_estado_y_en_historial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-IMAP-FECHA-DROP-VISIBLE-1: same shape as
    `omitidos_por_error_proveedor` above, for `ClienteImap.
    omitidos_por_fecha_proveedor` -- a message its own date-bounded SINCE
    fallback deliberately skipped."""
    from duna_buzon import historial
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    class _ProveedorConOmitidosPorFecha:
        omitidos_por_fecha_proveedor = 2

        def cerrar(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorConOmitidosPorFecha(),
    )
    monkeypatch.setattr(
        cli,
        "ejecutar_pasada",
        lambda **kwargs: ResultadoPasada(
            contadores=Contadores(vistos=4, reenviados=1, omitidos_por_reglas=0, omitidos_por_413_422=0)
        ),
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "omitidos_por_fecha_proveedor=2" in salida.out

    registros, descartadas = historial.leer_historial(directorio)
    assert descartadas == 0
    assert registros[-1].omitidos == 2


def test_sincronizar_sin_omitidos_de_proveedor_no_lo_imprime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A provider with no `omitidos_por_error_proveedor` at all (the plain
    `_ProveedorFalso` test fake, and every Microsoft/Gmail pass) never gets
    a guessed `omitidos_por_error_proveedor=` in the status line."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1, reenviados=1))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "omitidos_por_error_proveedor=" not in salida.out
    assert "omitidos_por_fecha_proveedor=" not in salida.out


def test_sincronizar_sin_enviar_origen_no_pasa_etiqueta_ni_imprime_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1, off by default: no label is computed and
    nothing is printed about it."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] is None
    assert capsys.readouterr().err == ""


def test_sincronizar_con_enviar_origen_pasa_la_etiqueta_de_la_fuente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1: the label is this source's own
    `"<proveedor>:<id>"` -- here a Microsoft source, `microsoft:hotmail-
    carlos`."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_origen(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] == "microsoft:hotmail-carlos"
    assert capsys.readouterr().err == ""


def test_sincronizar_con_enviar_origen_etiqueta_imap_tiene_forma_proveedor_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_origen(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")
    _escribir_reglas_vacias(tmp_path / "fuentes" / "legal-duna")
    monkeypatch.setattr(credencial_imap, "obtener_credencial", lambda cfg, directorio: "credencial-falsa")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=0))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] == "imap:legal-duna"
    # Never a password, token or address in the label -- only the
    # provider and this connector's own source id.
    assert "legal@duna.solutions" not in capturado["etiqueta_origen"]
    assert "credencial-falsa" not in capturado["etiqueta_origen"]


def test_sincronizar_con_etiqueta_sobre_el_tope_la_omite_y_avisa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BUZON-ORIGEN-WIRE-1's >64 fallback: only reachable here because this
    source was written directly (bypassing `agregar-fuente`'s own
    48-character guard), exactly like a Cuenta-pulled source would be."""
    from duna_buzon.limites import LIMITE_ORIGEN
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    id_fuente = "a" * (LIMITE_ORIGEN - len("imap:") + 1)
    assert len(f"imap:{id_fuente}") == LIMITE_ORIGEN + 1

    config = _config_con_origen(tmp_path)
    _escribir_fuente(tmp_path, id=id_fuente, proveedor="imap", direccion="legal@duna.solutions")
    _escribir_reglas_vacias(tmp_path / "fuentes" / id_fuente)
    monkeypatch.setattr(credencial_imap, "obtener_credencial", lambda cfg, directorio: "credencial-falsa")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    capturado: dict[str, object] = {}

    def falso_ejecutar_pasada(**kwargs):  # type: ignore[no-untyped-def]
        capturado.update(kwargs)
        return ResultadoPasada(contadores=Contadores(vistos=0))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert capturado["etiqueta_origen"] is None
    error = capsys.readouterr().err
    assert f"fuente={id_fuente}" in error
    assert str(LIMITE_ORIGEN) in error


def test_una_fuente_fallando_no_detiene_a_las_demas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    directorio_b = _escribir_fuente(tmp_path, id="otra-fuente")
    _escribir_reglas_vacias(directorio_b)

    def falso_obtener_token(cfg: Config, directorio_fuente: str) -> str:
        if directorio_fuente.endswith("otra-fuente"):
            raise auth.SinCuentaError("no account is signed in: run entrar")
        return "token-falso"

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=3))
    )

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos vistos=3" in salida.out
    assert "fuente=otra-fuente" in salida.err
    assert "entrar" in salida.err


def test_parada_nunca_imprime_el_token_el_remitente_ni_el_asunto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import httpx

    from duna_buzon.finanzas import FinanzasClient
    from duna_buzon.graph import GraphClient

    token_graph = "TOKEN-GRAPH-SECRETO-NO-DEBE-APARECER"
    token_finanzas = "TOKEN-FINANZAS-SECRETO-NO-DEBE-APARECER"
    remitente = "secreto-remitente@no-debe-aparecer.com"
    asunto = "ASUNTO-SECRETO-NO-DEBE-APARECER"

    config = Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": token_finanzas,
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )
    directorio = _escribir_fuente(tmp_path)
    (directorio / "reglas.json").write_text(
        json.dumps({"desde": "2026-01-01T00:00:00+00:00", "remitentes": [remitente], "palabras": []}),
        encoding="utf-8",
    )

    def graph_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "internetMessageId": "<m1@mail.example>",
                        "from": {"emailAddress": {"address": remitente}},
                        "subject": asunto,
                        "receivedDateTime": "2026-03-01T10:00:00Z",
                        "hasAttachments": False,
                    }
                ]
            },
        )

    def finanzas_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "nope"})

    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: token_graph)
    monkeypatch.setattr(
        cli,
        "construir_proveedor",
        lambda fuente, token, cursor_proveedor_inicial=None: GraphClient(
            token, transport=httpx.MockTransport(graph_handler)
        ),
    )
    monkeypatch.setattr(
        cli,
        "FinanzasClient",
        lambda cfg: FinanzasClient(cfg, transport=httpx.MockTransport(finanzas_handler)),
    )

    assert cli.comando_sincronizar(config) == 1

    salida = capsys.readouterr()
    assert salida.err.strip() == "fuente=hotmail-carlos parada: finanzas enviar http=403"
    assert "parada=finanzas enviar http=403" in salida.out
    for marcador in (token_graph, token_finanzas, remitente, asunto):
        assert marcador not in salida.out
        assert marcador not in salida.err


def test_fuentes_lista_sin_secretos(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", direccion="carlos@hotmail.com")
    _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", activa=False, host="imap.zoho.com", port=993
    )

    assert cli.comando_fuentes(config) == 0
    salida = capsys.readouterr().out
    assert "hotmail-carlos proveedor=microsoft direccion=carlos@hotmail.com" in salida
    assert "estado=activa" in salida
    assert "legal-duna proveedor=imap" in salida
    assert "estado=inactiva" in salida


def test_fuentes_sin_nada_configurado(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert cli.comando_fuentes(config) == 0
    assert "no sources configured" in capsys.readouterr().out


def test_fuentes_una_fuente_rota_no_aborta_la_lista_de_las_sanas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # BUZON-FUENTES-LISTAR-RESILIENTE-1 -- one bad `fuente.json` used to
    # make `fuentes` (and every other command) raise and print nothing.
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", direccion="carlos@hotmail.com")
    ruta_rota = tmp_path / "fuentes" / "legal-rota"
    ruta_rota.mkdir(parents=True)
    (ruta_rota / "fuente.json").write_text(
        json.dumps({"proveedor": "imap", "direccion": "a@b.com"}), encoding="utf-8"
    )

    assert cli.comando_fuentes(config) == 1
    salida = capsys.readouterr()
    assert "hotmail-carlos proveedor=microsoft direccion=carlos@hotmail.com" in salida.out
    assert "fuente=legal-rota error:" in salida.err
    assert "missing required field" in salida.err


def test_agregar_fuente_imap_escribe_fuente_json_sin_firmar(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 0
    )

    fuente = cargar_fuente(str(tmp_path), "legal-duna")
    assert fuente.proveedor == "imap"
    assert fuente.direccion == "legal@duna.solutions"
    assert fuente.host == "imap.zoho.com"
    assert fuente.port == 993
    assert fuente.token_finanzas_ref == "carlos"
    assert fuente.activa is True
    assert fuente.etiqueta == "legal@duna.solutions"
    # Never signs in -- no credential file exists yet.
    assert not (tmp_path / "fuentes" / "legal-duna" / "credencial.bin").exists()


def test_agregar_fuente_imap_con_carpetas_las_guarda(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
            carpetas=["Legal", "Compartidos/Facturas"],
        )
        == 0
    )

    fuente = cargar_fuente(str(tmp_path), "legal-duna")
    assert fuente.carpetas == ("Legal", "Compartidos/Facturas")


def test_agregar_fuente_con_id_repetido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap")

    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente="legal-duna",
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    assert "legal-duna" in capsys.readouterr().err


def test_agregar_fuente_id_en_el_tope_se_acepta(tmp_path: Path) -> None:
    from duna_buzon.fuentes import cargar_fuente

    config = _config(tmp_path)
    id_fuente = "a" * cli.LIMITE_ID_FUENTE_AGREGAR
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente=id_fuente,
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 0
    )
    assert cargar_fuente(str(tmp_path), id_fuente).id == id_fuente


def test_agregar_fuente_id_sobre_el_tope_se_rechaza(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    id_fuente = "a" * (cli.LIMITE_ID_FUENTE_AGREGAR + 1)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="imap",
            id_fuente=id_fuente,
            direccion="legal@duna.solutions",
            host="imap.zoho.com",
            puerto=993,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    error = capsys.readouterr().err
    assert str(cli.LIMITE_ID_FUENTE_AGREGAR) in error
    assert not (tmp_path / "fuentes" / id_fuente).exists()


def test_agregar_fuente_proveedor_no_imap_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert (
        cli.comando_agregar_fuente(
            config,
            proveedor="microsoft",
            id_fuente="otra",
            direccion="x@y.com",
            host="h",
            puerto=1,
            token_finanzas="carlos",
            etiqueta=None,
        )
        == 1
    )
    assert "microsoft" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `carpetas <id>` (BUZON-IMAP-CARPETAS-INCLUIDAS-1)
# ---------------------------------------------------------------------------


def test_comando_carpetas_sin_filtro_marca_todo_lo_no_excluido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions"
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )

    inbox = _CarpetaFalsa(nombre="INBOX", mensajes={})
    legal = _CarpetaFalsa(nombre="Legal", mensajes={})
    trash = _CarpetaFalsa(nombre="Trash", banderas=("\\Trash",), mensajes={})
    with _ServidorImapFalso(carpetas=[inbox, legal, trash]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert "carpeta='INBOX' banderas=- leida=si" in salida
    assert "carpeta='Legal' banderas=- leida=si" in salida
    assert "carpeta='Trash' banderas=\\Trash leida=no" in salida


def test_comando_carpetas_con_filtro_marca_solo_las_incluidas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        carpetas=("Legal",),
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )

    inbox = _CarpetaFalsa(nombre="INBOX", mensajes={})
    legal = _CarpetaFalsa(nombre="Legal", mensajes={})
    with _ServidorImapFalso(carpetas=[inbox, legal]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert "carpeta='INBOX' banderas=- leida=no" in salida
    assert "carpeta='Legal' banderas=- leida=si" in salida


def test_comando_carpetas_con_marca_imprime_conteos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon import cursor as modulo_cursor
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a, _mensaje_simple

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions"
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )
    modulo_cursor.guardar_cursor_proveedor(str(directorio), '{"Legal": "1000:2"}')

    inbox = _CarpetaFalsa(nombre="INBOX", mensajes={})
    legal = _CarpetaFalsa(
        nombre="Legal",
        mensajes={i: _mensaje_simple(i) for i in range(1, 5)},
        uidvalidity=1000,
        uidnext=5,
    )
    with _ServidorImapFalso(carpetas=[inbox, legal]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert (
        "carpeta='Legal' banderas=- leida=si mensajes=4 uidnext=5 uidvalidity=1000 "
        "marca=1000:2 nuevos=2" in salida
    )
    # INBOX has no stored watermark at all -- "sin marca", both fields.
    assert (
        "carpeta='INBOX' banderas=- leida=si mensajes=0 uidnext=1 uidvalidity=1000 "
        "marca=sin marca nuevos=sin marca" in salida
    )


def test_comando_carpetas_excluida_no_imprime_conteos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.cliente_imap import ClienteImap

    from .test_cliente_imap import _CarpetaFalsa, _ServidorImapFalso, _conectar_a

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions"
    )
    cripto.escribir_archivo_sensible(
        directorio / "credencial.bin", cripto.cifrar(b"app-password", config.clave_cifrado)
    )

    trash = _CarpetaFalsa(nombre="Trash", banderas=("\\Trash",), mensajes={})
    with _ServidorImapFalso(carpetas=[trash]) as servidor:
        monkeypatch.setattr(
            cli,
            "ClienteImap",
            lambda **kwargs: ClienteImap(**{**kwargs, "construir_conexion": _conectar_a(servidor)}),
        )
        assert cli.comando_carpetas(config, "legal-duna") == 0

    salida = capsys.readouterr().out
    assert salida.strip() == "carpeta='Trash' banderas=\\Trash leida=no"
    # Never EXAMINEd -- it is excluded, so this must never issue EXAMINE for it.
    assert not any('"Trash"' in c for c in servidor.comandos if "EXAMINE" in c)


def test_comando_carpetas_id_desconocido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_carpetas(config, "no-existe") == 1
    assert "no-existe" in capsys.readouterr().err


def test_comando_carpetas_fuente_no_imap_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    assert cli.comando_carpetas(config, "hotmail-carlos") == 1
    assert "not an imap source" in capsys.readouterr().err


def test_comando_carpetas_sin_credencial_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")
    assert cli.comando_carpetas(config, "legal-duna") == 1
    assert "entrar" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `carpetas-graph <id>` (BUZON-CARPETAS-MICROSOFT-CLI-1)
# ---------------------------------------------------------------------------


def test_comando_carpetas_graph_lista_nombre_id_y_bien_conocida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.graph import GraphClient

    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    def handler(request: httpx.Request) -> httpx.Response:
        segmentos = [s for s in request.url.path.split("/") if s]
        assert request.method == "GET"
        if segmentos[-1] == "mailFolders":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"displayName": "Bandeja de entrada", "id": "AAA"},
                        {"displayName": "Archivo", "id": "BBB"},
                    ]
                },
            )
        # A well-known-name probe: only "inbox" exists in this fake mailbox.
        if segmentos[-1] == "inbox":
            return httpx.Response(200, json={"id": "AAA"})
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setattr(
        cli, "GraphClient", lambda token: GraphClient(token, transport=httpx.MockTransport(handler))
    )
    assert cli.comando_carpetas_graph(config, "hotmail-carlos") == 0

    salida = capsys.readouterr().out
    assert "carpeta='Archivo' bien_conocida=- id=BBB" in salida
    assert "carpeta='Bandeja de entrada' bien_conocida=inbox id=AAA" in salida


def test_comando_carpetas_graph_id_desconocido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_carpetas_graph(config, "no-existe") == 1
    assert "no-existe" in capsys.readouterr().err


def test_comando_carpetas_graph_fuente_no_microsoft_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")
    assert cli.comando_carpetas_graph(config, "legal-duna") == 1
    assert "not a microsoft source" in capsys.readouterr().err


def test_comando_carpetas_graph_sin_cuenta_es_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")

    def falso_obtener_token(cfg: Config, directorio_fuente: str) -> str:
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)
    assert cli.comando_carpetas_graph(config, "hotmail-carlos") == 1
    assert "entrar" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `fijar-carpetas <id> ...` (BUZON-CARPETAS-MICROSOFT-CLI-1)
# ---------------------------------------------------------------------------


def test_fijar_carpetas_fija_y_luego_quita_con_todas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")

    assert (
        cli.comando_fijar_carpetas(
            config, "hotmail-carlos", carpetas=["inbox", "archive"], todas=False
        )
        == 0
    )
    salida = capsys.readouterr().out
    assert "antes=[]" in salida
    assert "despues=['inbox', 'archive']" in salida
    assert cargar_fuente(str(tmp_path), "hotmail-carlos").carpetas == ("inbox", "archive")

    assert cli.comando_fijar_carpetas(config, "hotmail-carlos", carpetas=None, todas=True) == 0
    salida = capsys.readouterr().out
    assert "antes=['inbox', 'archive']" in salida
    assert "despues=[]" in salida
    assert cargar_fuente(str(tmp_path), "hotmail-carlos").carpetas is None


def test_fijar_carpetas_funciona_para_imap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions")

    assert (
        cli.comando_fijar_carpetas(config, "legal-duna", carpetas=["Legal"], todas=False) == 0
    )
    assert cargar_fuente(str(tmp_path), "legal-duna").carpetas == ("Legal",)


def test_fijar_carpetas_id_desconocido_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    assert cli.comando_fijar_carpetas(config, "no-existe", carpetas=["inbox"], todas=False) == 1
    assert "no-existe" in capsys.readouterr().err


def test_fijar_carpetas_proveedor_google_es_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    guardar_fuente(
        str(tmp_path),
        FuenteConfig(
            id="cuenta-abc",
            proveedor="google",
            direccion="carlos@gmail.com",
            etiqueta="carlos@gmail.com",
            token_finanzas_ref="carlos",
            activa=True,
            origen="cuenta",
        ),
    )
    assert cli.comando_fijar_carpetas(config, "cuenta-abc", carpetas=["inbox"], todas=False) == 1
    assert "google" in capsys.readouterr().err


def test_fijar_carpetas_preserva_las_otras_claves(tmp_path: Path) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path,
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        etiqueta="Legal Duna",
        token_finanzas_ref="ref-legal",
        activa=True,
    )
    cripto.escribir_archivo_sensible(directorio / "credencial.bin", b"no-tocar")

    assert (
        cli.comando_fijar_carpetas(config, "legal-duna", carpetas=["Legal"], todas=False) == 0
    )

    fuente = cargar_fuente(str(tmp_path), "legal-duna")
    assert fuente.direccion == "legal@duna.solutions"
    assert fuente.etiqueta == "Legal Duna"
    assert fuente.token_finanzas_ref == "ref-legal"
    assert fuente.activa is True
    assert fuente.host == "imap.zoho.com"
    assert fuente.port == 993
    assert (directorio / "credencial.bin").read_bytes() == b"no-tocar"


def test_fijar_carpetas_escritura_es_atomica_y_no_deja_tmp(tmp_path: Path) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")

    assert (
        cli.comando_fijar_carpetas(config, "hotmail-carlos", carpetas=["inbox"], todas=False) == 0
    )

    nombres = [p.name for p in directorio.iterdir()]
    assert "fuente.json" in nombres
    assert not any(n.endswith(".tmp") for n in nombres)


def test_migracion_automatica_desde_sincronizar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The legacy single-mailbox layout at the data root migrates itself
    into `fuentes/hotmail-carlos/` the first time any command runs, and
    the pass then proceeds against the migrated source."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    (tmp_path / "cursor.json").write_text('{"cursor": "2026-03-01T10:00:00+00:00"}', encoding="utf-8")
    _escribir_reglas_vacias(tmp_path)

    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=5))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "fuente=hotmail-carlos vistos=5" in salida.out
    assert not (tmp_path / "cursor.json").exists()
    assert (tmp_path / "fuentes" / "hotmail-carlos" / "cursor.json").is_file()


# ---------------------------------------------------------------------------
# Cuenta Duna pull (BUZON-FUENTES-PULL-1)
# ---------------------------------------------------------------------------


def _config_con_cuenta(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_CUENTA_URL": "https://cuenta.duna.internal",
            "BUZON_CUENTA_CLIENT_ID": "buzon",
            "BUZON_CUENTA_CLIENT_SECRET": "secreto-cuenta",
            "BUZON_CUENTA_SUB": "sub-carlos",
        }
    )


def _escribir_reglas_vacias_en(directorio: Path) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / "reglas.json").write_text(
        '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": [], "palabras": []}',
        encoding="utf-8",
    )


class _CuentaClienteFalso:
    """Replaces `cli.CuentaClient` -- never touches the network. `resultado`
    is either a `list[FuenteCuenta]` (pull succeeds) or a `CuentaPullError`
    instance (pull fails)."""

    def __init__(self, resultado: list[FuenteCuenta] | CuentaPullError, **kwargs) -> None:
        self._resultado = resultado
        self.cerrado = False

    def listar_fuentes(self) -> list[FuenteCuenta]:
        if isinstance(self._resultado, CuentaPullError):
            raise self._resultado
        return self._resultado

    def cerrar(self) -> None:
        self.cerrado = True


def test_sin_cuenta_configurada_nunca_construye_un_cuenta_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)  # no BUZON_CUENTA_* set at all
    assert config.cuenta_configurada is False
    _escribir_fuente_y_reglas(tmp_path)

    def _nunca_llamar(*args, **kwargs):
        raise AssertionError("CuentaClient must never be constructed when Cuenta is not configured")

    monkeypatch.setattr(cli, "CuentaClient", _nunca_llamar)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0


def test_pull_exitoso_ejecuta_la_fuente_de_cuenta_con_el_token_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.fuentes import cargar_fuente
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-cuenta-secreto",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))

    tokens_vistos: list[str] = []

    def falso_construir_proveedor(fuente, token, cursor_proveedor_inicial=None):
        tokens_vistos.append(token)
        return _ProveedorFalso()

    monkeypatch.setattr(cli, "construir_proveedor", falso_construir_proveedor)
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=2))
    )

    def _nunca_auth_local(*args, **kwargs):
        raise AssertionError("a cuenta-origin source must never call auth.obtener_token")

    monkeypatch.setattr(auth, "obtener_token", _nunca_auth_local)

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "fuente=cuenta-abc123 vistos=2" in salida.out
    assert tokens_vistos == ["token-cuenta-secreto"]

    fuente = cargar_fuente(str(tmp_path), "cuenta-abc123")
    assert fuente.proveedor == "google"
    assert fuente.direccion == "carlos@gmail.com"
    assert fuente.origen == "cuenta"
    assert fuente.activa is True

    # Never writes a credential, and never writes the pulled token to disk
    # anywhere -- not in `fuente.json`, not in a `credencial.bin`.
    directorio_cuenta = tmp_path / "fuentes" / "cuenta-abc123"
    assert not (directorio_cuenta / "credencial.bin").exists()
    assert "token-cuenta-secreto" not in (directorio_cuenta / "fuente.json").read_text(encoding="utf-8")


def test_pull_404_omite_fuentes_de_cuenta_pero_las_locales_siguen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)  # the usual LOCAL "hotmail-carlos" source

    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso(CuentaPullError(http_status=404)))
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=3))
    )

    assert cli.comando_sincronizar(config) == 0
    salida = capsys.readouterr()
    assert "cuenta: cuenta fuentes http=404" in salida.err
    assert "fuente=hotmail-carlos vistos=3" in salida.out


def test_fuente_de_cuenta_revocada_se_marca_inactiva_y_deja_de_ejecutarse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.fuentes import cargar_fuente
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-1",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0
    assert cargar_fuente(str(tmp_path), "cuenta-abc123").activa is True
    capsys.readouterr()

    # Second pass: Cuenta Duna no longer returns this source (revoked).
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([]))
    llamadas_ejecutadas: list[str] = []
    monkeypatch.setattr(
        cli,
        "ejecutar_pasada",
        lambda **kwargs: (
            llamadas_ejecutadas.append("llamado") or ResultadoPasada(contadores=Contadores(vistos=1))
        ),
    )

    assert cli.comando_sincronizar(config) == 1  # no active sources left at all
    fuente_revocada = cargar_fuente(str(tmp_path), "cuenta-abc123")
    assert fuente_revocada.activa is False
    assert not llamadas_ejecutadas
    # The cursor/rules directory itself is untouched -- a reconnect can
    # resume it.
    assert (tmp_path / "fuentes" / "cuenta-abc123" / "reglas.json").is_file()


def test_fuente_de_cuenta_reconectada_reanuda_el_cursor_existente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime

    from duna_buzon import cursor as modulo_cursor
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta(tmp_path)
    directorio_cuenta = tmp_path / "fuentes" / "cuenta-abc123"
    _escribir_reglas_vacias_en(directorio_cuenta)
    cursor_previo = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    modulo_cursor.guardar_cursor(str(directorio_cuenta), cursor_previo)

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-reconectado",
        expira_en_segundos=3600,
    )
    monkeypatch.setattr(cli, "CuentaClient", lambda **kw: _CuentaClienteFalso([fuente_cuenta]))
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    cursores_vistos: list[datetime.datetime] = []

    def falso_ejecutar_pasada(*, cursor_inicial, **kwargs):
        cursores_vistos.append(cursor_inicial)
        return ResultadoPasada(contadores=Contadores(vistos=1))

    monkeypatch.setattr(cli, "ejecutar_pasada", falso_ejecutar_pasada)

    assert cli.comando_sincronizar(config) == 0
    assert cursores_vistos == [cursor_previo]


# ---------------------------------------------------------------------------
# Estado heartbeat push to Cuenta Duna (BUZON-ESTADO-A-CUENTA-1)
# ---------------------------------------------------------------------------


def _config_con_cuenta_y_estado(tmp_path: Path) -> Config:
    return Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_CUENTA_URL": "https://cuenta.duna.internal",
            "BUZON_CUENTA_CLIENT_ID": "buzon",
            "BUZON_CUENTA_CLIENT_SECRET": "secreto-cuenta",
            "BUZON_CUENTA_SUB": "sub-carlos",
            "BUZON_ENVIAR_ESTADO_CUENTA": "1",
        }
    )


class _CuentaClienteEstadoFalso:
    """Replaces `cli.CuentaClient` for the heartbeat tests below -- fakes
    BOTH `listar_fuentes` (the existing pull, which `_pull_fuentes_cuenta`
    always attempts the moment Cuenta is configured) and `enviar_estado`
    (this slice's own push), never touching the network."""

    def __init__(
        self,
        *,
        pull_resultado: list[FuenteCuenta] | CuentaPullError | None = None,
        estado_resultado: CuentaPullError | None = None,
        **kwargs: object,
    ) -> None:
        self._pull_resultado = pull_resultado if pull_resultado is not None else []
        self._estado_resultado = estado_resultado
        self.cerrado = False
        self.estado_llamado = False
        self.fuentes_enviadas: list | None = None
        self.enviado_en: datetime.datetime | None = None

    def listar_fuentes(self) -> list[FuenteCuenta]:
        if isinstance(self._pull_resultado, CuentaPullError):
            raise self._pull_resultado
        return self._pull_resultado

    def enviar_estado(self, fuentes: list, *, enviado_en: datetime.datetime) -> None:
        self.estado_llamado = True
        self.fuentes_enviadas = fuentes
        self.enviado_en = enviado_en
        if self._estado_resultado is not None:
            raise self._estado_resultado

    def cerrar(self) -> None:
        self.cerrado = True


def test_estado_cuenta_apagado_nunca_llama_a_enviar_estado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch defaults OFF: even with Cuenta fully configured (so the
    EXISTING pull still runs), this slice's own push is never attempted."""
    config = _config_con_cuenta(tmp_path)  # BUZON_ENVIAR_ESTADO_CUENTA not set
    assert config.enviar_estado_cuenta is False
    _escribir_fuente_y_reglas(tmp_path)

    instancias: list[_CuentaClienteEstadoFalso] = []

    def fabrica(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso()
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0
    assert not any(i.estado_llamado for i in instancias)


def _correr_pasada_local_con_estado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, vistos: int = 12, reenviados: int = 3
) -> list:
    """Runs one clean `sincronizar` pass over the usual LOCAL
    `hotmail-carlos` source, with the estado push switched on and a fake
    `CuentaClient` that records exactly what `enviar_estado` was called
    with. Returns that call's own `fuentes` list."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta_y_estado(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)

    instancias: list[_CuentaClienteEstadoFalso] = []

    def fabrica(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso()
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli,
        "ejecutar_pasada",
        lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=vistos, reenviados=reenviados)),
    )

    assert cli.comando_sincronizar(config) == 0

    llamadas_estado = [i for i in instancias if i.estado_llamado]
    assert len(llamadas_estado) == 1
    assert llamadas_estado[0].fuentes_enviadas is not None
    return llamadas_estado[0].fuentes_enviadas


def test_estado_cuenta_prendido_envia_la_fuente_local_con_el_cuerpo_del_contrato(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fuentes_enviadas = _correr_pasada_local_con_estado(tmp_path, monkeypatch, vistos=12, reenviados=3)

    assert len(fuentes_enviadas) == 1
    fila = fuentes_enviadas[0]
    assert fila.fuente_id == "hotmail-carlos"
    assert fila.proveedor == "microsoft"
    assert fila.direccion == "carlos@hotmail.com"  # unmasked here -- `CuentaClient.enviar_estado` masks it
    assert fila.etiqueta == "Hotmail"
    assert fila.origen == "buzon"
    assert fila.vistos == 12
    assert fila.reenviados == 3
    assert fila.alerta is None


def test_estado_cuenta_nunca_manda_mas_que_los_campos_enumerados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing but what `FuenteEstadoWire` itself carries ever reaches
    `enviar_estado` -- no subject, sender, token, or address beyond this
    one field, which `cuenta.py`'s own wire builder then masks."""
    import dataclasses

    from duna_buzon.cuenta import FuenteEstadoWire

    fuentes_enviadas = _correr_pasada_local_con_estado(tmp_path, monkeypatch)
    campos = {f.name for f in dataclasses.fields(FuenteEstadoWire)}
    assert campos == {
        "fuente_id",
        "proveedor",
        "direccion",
        "etiqueta",
        "origen",
        "ultimo_paso_en",
        "vistos",
        "reenviados",
        "alerta",
    }


def test_estado_cuenta_incluye_fuente_de_cuenta_pulled_con_origen_cuenta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta_y_estado(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-cuenta-secreto",
        expira_en_segundos=3600,
    )

    instancias: list[_CuentaClienteEstadoFalso] = []

    def fabrica(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso(pull_resultado=[fuente_cuenta])
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica)
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=2))
    )

    assert cli.comando_sincronizar(config) == 0

    llamadas_estado = [i for i in instancias if i.estado_llamado]
    assert len(llamadas_estado) == 1
    fuentes_enviadas = llamadas_estado[0].fuentes_enviadas
    assert len(fuentes_enviadas) == 1
    assert fuentes_enviadas[0].fuente_id == "cuenta-abc123"
    assert fuentes_enviadas[0].origen == "cuenta"
    assert fuentes_enviadas[0].direccion == "carlos@gmail.com"


def test_estado_cuenta_fuente_de_cuenta_revocada_se_marca_reingreso_requerido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta_y_estado(tmp_path)
    _escribir_reglas_vacias_en(tmp_path / "fuentes" / "cuenta-abc123")

    fuente_cuenta = FuenteCuenta(
        id="abc123",
        proveedor="google",
        direccion="carlos@gmail.com",
        access_token="token-1",
        expira_en_segundos=3600,
    )
    instancias: list[_CuentaClienteEstadoFalso] = []

    def fabrica_primera_pasada(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso(pull_resultado=[fuente_cuenta])
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica_primera_pasada)
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0  # first pass: source connects and runs

    # Second pass: Cuenta Duna no longer returns this source (revoked) --
    # no OTHER active source exists, so the pass itself reports "no active
    # sources" (exit 1) and never reaches the push; what matters here is
    # that the revoked, still-on-disk source is reachable for a LATER push
    # once some other source is active again. Add a local source so the
    # pass (and the push) still runs.
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")

    instancias.clear()

    def fabrica_segunda_pasada(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso(pull_resultado=[])
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica_segunda_pasada)

    assert cli.comando_sincronizar(config) == 0

    llamadas_estado = [i for i in instancias if i.estado_llamado]
    assert len(llamadas_estado) == 1
    fuentes_enviadas = {f.fuente_id: f for f in llamadas_estado[0].fuentes_enviadas}
    assert fuentes_enviadas["cuenta-abc123"].alerta == "reingreso_requerido"
    assert fuentes_enviadas["hotmail-carlos"].alerta is None


def test_estado_cuenta_fuente_nunca_sincronizada_se_omite_del_envio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that was just added but never completed a single pass has
    no `historial.jsonl` yet -- nothing true to report, so it is left out
    of the heartbeat rather than sent with a fabricated 0/0. An INACTIVE
    source is the only way to have one on disk that the main loop never
    even attempts (every active source gets a pass attempt, successful or
    not, the moment `sincronizar` runs)."""
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta_y_estado(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)

    # A second source, inactive and never run -- no historial.jsonl exists
    # for it at all.
    _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions", activa=False
    )

    instancias: list[_CuentaClienteEstadoFalso] = []

    def fabrica(**kw: object) -> _CuentaClienteEstadoFalso:
        instancia = _CuentaClienteEstadoFalso()
        instancias.append(instancia)
        return instancia

    monkeypatch.setattr(cli, "CuentaClient", fabrica)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0

    llamadas_estado = [i for i in instancias if i.estado_llamado]
    assert len(llamadas_estado) == 1
    ids_enviados = {f.fuente_id for f in llamadas_estado[0].fuentes_enviadas}
    assert ids_enviados == {"hotmail-carlos"}
    assert not (tmp_path / "fuentes" / "legal-duna" / "historial.jsonl").exists()


def test_estado_cuenta_falla_nunca_falla_la_pasada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config_con_cuenta_y_estado(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)

    def fabrica(**kw: object) -> _CuentaClienteEstadoFalso:
        return _CuentaClienteEstadoFalso(estado_resultado=CuentaPullError(paso="estado", http_status=500))

    monkeypatch.setattr(cli, "CuentaClient", fabrica)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1))
    )

    assert cli.comando_sincronizar(config) == 0  # the push's own failure never fails the pass
    salida = capsys.readouterr()
    assert "cuenta: cuenta estado http=500" in salida.err


# ---------------------------------------------------------------------------
# `rebobinar-cursor <fuente> <fecha> [--aplicar]` (BUZON-CURSOR-REBOBINAR-CLI-1)
# ---------------------------------------------------------------------------


def _pinned_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> datetime.datetime:
    # Midnight-aligned so "N days back" lands on a clean calendar date --
    # 2026-11-19T00:00:00 minus 90 days is exactly 2026-08-21T00:00:00.
    ahora = datetime.datetime(2026, 11, 19, 0, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: ahora)
    return ahora


def test_rebobinar_cursor_id_desconocido_es_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _pinned_now(tmp_path, monkeypatch)
    assert cli.comando_rebobinar_cursor(config, "no-existe", "2026-11-01", aplicar=False) == 1
    assert "no-existe" in capsys.readouterr().err


def test_rebobinar_cursor_fecha_invalida_es_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "no-es-una-fecha", aplicar=False) == 1
    salida = capsys.readouterr().err
    assert "not a valid" in salida.lower()


def test_rebobinar_cursor_rechaza_fecha_futura(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-12-01", aplicar=False) == 1
    assert "future" in capsys.readouterr().err.lower()


def test_rebobinar_cursor_rechaza_mas_de_90_dias_atras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    # "now" is pinned to 2026-11-19; 91 days back is 2026-08-20.
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-08-20", aplicar=False) == 1
    assert "90" in capsys.readouterr().err


def test_rebobinar_cursor_acepta_exactamente_90_dias_atras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    # 90 days back from 2026-11-19 is 2026-08-21.
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-08-21", aplicar=False) == 0


def test_rebobinar_cursor_dry_run_no_escribe_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon import cursor as modulo_cursor

    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    cursor_previo = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    modulo_cursor.guardar_cursor(str(directorio), cursor_previo)

    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-11-01", aplicar=False) == 0
    salida = capsys.readouterr().out
    assert "cursor antes=2026-10-01T00:00:00+00:00" in salida
    assert "cursor despues=2026-11-01T00:00:00+00:00" in salida
    assert "dry-run" in salida.lower()

    # Nothing changed on disk.
    recargado = modulo_cursor.cargar_cursor(
        str(directorio), valor_inicial=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    )
    assert recargado == cursor_previo


def test_rebobinar_cursor_dry_run_sin_cursor_previo_muestra_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)

    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-11-01", aplicar=False) == 0
    salida = capsys.readouterr().out
    assert "cursor antes=none" in salida


def test_rebobinar_cursor_aplicar_escribe_atomicamente_y_sin_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon import cursor as modulo_cursor

    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    cursor_previo = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    modulo_cursor.guardar_cursor(str(directorio), cursor_previo)

    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-11-01", aplicar=True) == 0
    salida = capsys.readouterr().out
    assert "cursor antes=2026-10-01T00:00:00+00:00" in salida
    assert "cursor despues=2026-11-01T00:00:00+00:00" in salida

    recargado = modulo_cursor.cargar_cursor(
        str(directorio), valor_inicial=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    )
    assert recargado == datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC)

    nombres = [p.name for p in directorio.iterdir()]
    assert modulo_cursor.NOMBRE_ARCHIVO in nombres
    assert not any(n.endswith(".tmp") for n in nombres)


def test_rebobinar_cursor_aplicar_borra_el_cursor_proveedor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewind clears the IMAP per-folder UID watermark too, so the next
    pass actually re-reads from the rewound date instead of resuming past
    it -- without this, `cliente_imap.py`'s own UID-resume path would
    silently override the rewound date cursor (`cursor.borrar_cursor_
    proveedor`'s own docstring)."""
    from duna_buzon import cursor as modulo_cursor

    config = _config(tmp_path)
    directorio = _escribir_fuente(
        tmp_path, id="legal-duna", proveedor="imap", direccion="legal@duna.solutions"
    )
    _pinned_now(tmp_path, monkeypatch)
    modulo_cursor.guardar_cursor(str(directorio), datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC))
    modulo_cursor.guardar_cursor_proveedor(str(directorio), '{"INBOX": "1:42"}')

    assert cli.comando_rebobinar_cursor(config, "legal-duna", "2026-11-01", aplicar=True) == 0
    assert modulo_cursor.cargar_cursor_proveedor(str(directorio)) is None


def test_rebobinar_cursor_aplicar_sin_cursor_proveedor_no_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-11-01", aplicar=True) == 0


def test_rebobinar_cursor_fecha_sin_zona_se_asume_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path, id="hotmail-carlos", proveedor="microsoft")
    _pinned_now(tmp_path, monkeypatch)
    assert cli.comando_rebobinar_cursor(config, "hotmail-carlos", "2026-11-01T08:00:00", aplicar=False) == 0
    assert "cursor despues=2026-11-01T08:00:00+00:00" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# `validar-reglas` (BUZON-REGLAS-ENTRADA-1) -- reads stdin, builds no Config.
# ---------------------------------------------------------------------------


def test_validar_reglas_acepta_json_valido(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"desde": "2026-01-01T00:00:00+00:00", "remitentes": ["a@b.com"], "palabras": []}'),
    )
    assert cli.comando_validar_reglas() == 0
    assert capsys.readouterr().err == ""


def test_validar_reglas_rechaza_json_invalido(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("esto no es json"))
    assert cli.comando_validar_reglas() == 1
    assert "error:" in capsys.readouterr().err.lower()


def test_validar_reglas_rechaza_campo_faltante(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"desde": "2026-01-01T00:00:00+00:00", "remitentes": []}')
    )
    assert cli.comando_validar_reglas() == 1
    assert "error:" in capsys.readouterr().err.lower()


def test_validar_reglas_nunca_imprime_nada_en_exito(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            '{"desde": "2026-01-01T00:00:00+00:00", "remitentes": ["a@b.com"], "palabras": ["factura"]}'
        ),
    )
    assert cli.comando_validar_reglas() == 0
    salida = capsys.readouterr()
    assert salida.out == ""


# ---------------------------------------------------------------------------
# BUZON-ALERTA-AUTH-1 -- a dead credential becomes visible: per-source
# `auth_fallando_desde`, a derived ALERTA once it passes the threshold, in
# `estado`, in the sincronizar journal line, and in `alertas --json`.
# ---------------------------------------------------------------------------


def test_fallo_de_auth_temprano_marca_la_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    momento = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: momento)

    def falso_obtener_token(cfg: Config, directorio_fuente: str) -> str:
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) == momento


def test_fallo_de_auth_repetido_conserva_el_primer_momento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)

    def falso_obtener_token(cfg: Config, directorio_fuente: str) -> str:
        raise auth.SinCuentaError("no account is signed in: run entrar")

    monkeypatch.setattr(auth, "obtener_token", falso_obtener_token)

    primero = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: primero)
    assert cli.comando_sincronizar(config) == 1

    segundo = datetime.datetime(2026, 11, 20, 20, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: segundo)
    assert cli.comando_sincronizar(config) == 1

    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) == primero


def test_fallo_de_auth_durante_la_pasada_marca_la_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1), detenida_por_autenticacion=True, motivo="graph listar http=401"
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    momento = datetime.datetime(2026, 11, 20, 9, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: momento)
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) == momento


def test_error_transitorio_no_marca_ni_limpia_la_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    momento_previo = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), momento_previo)

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1),
        detenida_por_error_transitorio=True,
        motivo="finanzas enviar http=503",
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)
    monkeypatch.setattr(cli, "_ahora", lambda: datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC))
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) == momento_previo


def test_reglas_invalidas_limpia_la_alerta_porque_el_token_si_funciono(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    (directorio / "reglas.json").write_text("esto no es json", encoding="utf-8")

    momento_previo = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), momento_previo)

    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) is None


def test_pasada_limpia_sin_fallos_limpia_la_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    momento_previo = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), momento_previo)

    resultado_falso = ResultadoPasada(contadores=Contadores(vistos=1))
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)
    assert cli.comando_sincronizar(config) == 0
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) is None


def test_rechazo_permanente_limpia_la_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    momento_previo = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), momento_previo)

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1),
        detenida_por_rechazo_permanente=True,
        motivo="finanzas enviar http=403",
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_auth_fallando_desde(str(directorio)) is None


def test_remedio_credencial_microsoft_local(tmp_path: Path) -> None:
    fuente = FuenteConfig(
        id="hotmail-carlos",
        proveedor="microsoft",
        direccion="carlos@hotmail.com",
        etiqueta="Hotmail",
        token_finanzas_ref="carlos",
        activa=True,
    )
    assert cli._remedio_credencial(fuente) == "ejecute 'entrar --fuente hotmail-carlos' para firmar"


def test_remedio_credencial_imap_menciona_contrasena_de_aplicacion(tmp_path: Path) -> None:
    fuente = FuenteConfig(
        id="legal-duna",
        proveedor="imap",
        direccion="legal@duna.solutions",
        etiqueta="Legal",
        token_finanzas_ref="carlos",
        activa=True,
        host="imap.zoho.com",
        port=993,
    )
    remedio = cli._remedio_credencial(fuente)
    assert "contrasena de aplicacion" in remedio
    assert "entrar --fuente legal-duna" in remedio


def test_remedio_credencial_fuente_cuenta_menciona_cuenta_duna() -> None:
    fuente = FuenteConfig(
        id="cuenta-abc",
        proveedor="microsoft",
        direccion="carlos@outlook.com",
        etiqueta="Outlook",
        token_finanzas_ref="carlos",
        activa=True,
        origen="cuenta",
    )
    assert cli._remedio_credencial(fuente) == "reconecte en Cuenta Duna"


def test_estado_sin_pasar_el_umbral_no_muestra_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    ahora = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), ahora - datetime.timedelta(hours=23))
    monkeypatch.setattr(cli, "_ahora", lambda: ahora)

    assert cli.comando_estado(config) == 0
    assert "ALERTA" not in capsys.readouterr().out


def test_estado_pasado_el_umbral_muestra_alerta_con_remedio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=25))

    assert cli.comando_estado(config) == 0
    salida = capsys.readouterr().out
    assert (
        "fuente=hotmail-carlos ALERTA credencial caida desde 2026-11-19T08:00:00Z (25 h)" in salida
    )
    assert "entrar --fuente hotmail-carlos" in salida


def test_estado_en_el_umbral_exacto_muestra_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=24))

    assert cli.comando_estado(config) == 0
    assert "ALERTA" in capsys.readouterr().out


def test_estado_umbral_personalizado_via_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": "token",
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
            "BUZON_ALERTA_AUTH_HORAS": "2",
        }
    )
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=3))

    assert cli.comando_estado(config) == 0
    assert "ALERTA" in capsys.readouterr().out


def test_estado_y_alertas_nunca_imprimen_el_token_finanzas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secreto = "TOKEN-FINANZAS-SECRETO-NO-DEBE-APARECER"
    config = Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": secreto,
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=30))

    assert cli.comando_estado(config) == 0
    assert secreto not in capsys.readouterr().out

    assert cli.comando_alertas(config) == 0
    assert secreto not in capsys.readouterr().out


def test_alertas_json_vacio_sin_fuentes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_vacio_cuando_ninguna_fuente_esta_en_alerta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    _escribir_fuente(tmp_path)
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_lista_la_fuente_en_alerta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=30))

    assert cli.comando_alertas(config) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida == [
        {
            "fuente": "hotmail-carlos",
            "tipo": "auth",
            "desde": "2026-11-19T08:00:00Z",
            "horas": 30,
            "remedio": "ejecute 'entrar --fuente hotmail-carlos' para firmar",
        }
    ]


def test_alertas_json_omite_una_fuente_con_fuente_json_roto(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    (tmp_path / "fuentes" / "rota").mkdir(parents=True)
    (tmp_path / "fuentes" / "rota" / "fuente.json").write_text("esto no es json", encoding="utf-8")
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_parada_de_auth_en_el_journal_lleva_alerta_si_tras_el_umbral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1), detenida_por_autenticacion=True, motivo="graph listar http=401"
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=26))

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert salida.err.strip() == "fuente=hotmail-carlos parada: graph listar http=401 alerta=si"


def test_parada_de_auth_sin_pasar_el_umbral_no_lleva_alerta_si(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1), detenida_por_autenticacion=True, motivo="graph listar http=401"
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert salida.err.strip() == "fuente=hotmail-carlos parada: graph listar http=401"


def test_parada_no_auth_nunca_lleva_alerta_si_aunque_haya_alerta_vieja(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A transient stop never carries alerta=si even when an OLD, already
    # past-threshold auth marker still sits on disk from a previous run --
    # bullet 4 names "a pass that stops on auth", not every stop.
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )

    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)

    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1),
        detenida_por_error_transitorio=True,
        motivo="finanzas enviar http=503",
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=26))

    assert cli.comando_sincronizar(config) == 1
    salida = capsys.readouterr()
    assert salida.err.strip() == "fuente=hotmail-carlos parada: finanzas enviar http=503"


# ---------------------------------------------------------------------------
# BUZON-ALERTA-SILENCIO-1 -- a durable per-source "last pass with
# vistos>0" marker (`alerta.ultimo_visto_en`): a source that used to see
# mail and has gone quiet for `BUZON_ALERTA_SILENCIO_HORAS` raises a
# "silencio" alert through `alertas`, and clears the moment a pass sees
# mail again.
# ---------------------------------------------------------------------------


def test_pasada_con_vistos_marca_el_ultimo_visto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(
        cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores(vistos=1, reenviados=1))
    )

    momento = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: momento)
    assert cli.comando_sincronizar(config) == 0
    assert modulo_alerta.cargar_ultimo_visto_en(str(directorio)) == momento


def test_pasada_sin_vistos_no_marca_el_ultimo_visto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores()))

    assert cli.comando_sincronizar(config) == 0
    assert modulo_alerta.cargar_ultimo_visto_en(str(directorio)) is None


def test_pasada_con_vistos_pero_detenida_igual_marca_el_ultimo_visto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mailbox HAD mail this pass, whatever happened to it afterwards --
    # a transient Finanzas error does not make the source "silent".
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    resultado_falso = ResultadoPasada(
        contadores=Contadores(vistos=1),
        detenida_por_error_transitorio=True,
        motivo="finanzas enviar http=503",
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: resultado_falso)

    momento = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: momento)
    assert cli.comando_sincronizar(config) == 1
    assert modulo_alerta.cargar_ultimo_visto_en(str(directorio)) == momento


def test_alertas_json_lista_la_fuente_en_silencio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_ultimo_visto(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=25))

    assert cli.comando_alertas(config) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida == [
        {
            "fuente": "hotmail-carlos",
            "tipo": "silencio",
            "desde": "2026-11-19T08:00:00Z",
            "horas": 25,
            "remedio": cli._remedio_silencio(cargar_fuente(str(tmp_path), "hotmail-carlos")),
        }
    ]


def test_alertas_json_sin_pasar_el_umbral_de_silencio_queda_vacio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_ultimo_visto(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=23))

    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_fuente_que_nunca_vio_correo_nunca_esta_en_silencio(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Neither `ultimo_visto_en` NOR `primera_pasada_en` exists -- this
    # source has never even attempted a single pass yet, so there is
    # nothing to measure silence from.
    config = _config(tmp_path)
    _escribir_fuente(tmp_path)
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_fuente_que_nunca_vio_correo_pero_ya_fue_pasada_cae_en_silencio_tras_el_umbral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No `ultimo_visto_en` at all (this source has NEVER once seen mail),
    # but it HAS been checked before (`primera_pasada_en` is set) -- past
    # BUZON_ALERTA_SILENCIO_HORAS since that first pass, this must raise,
    # not stay quiet forever (BUZON-CARPETAS-CONTEO-1).
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_primera_pasada(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=25))

    assert cli.comando_alertas(config) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida == [
        {
            "fuente": "hotmail-carlos",
            "tipo": "silencio",
            "desde": "2026-11-19T08:00:00Z",
            "horas": 25,
            "remedio": cli._remedio_silencio(cargar_fuente(str(tmp_path), "hotmail-carlos")),
        }
    ]


def test_alertas_json_fuente_que_nunca_vio_correo_antes_del_umbral_queda_vacio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_primera_pasada(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=23))

    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_ultimo_visto_tiene_prioridad_sobre_primera_pasada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A source that DID once see mail measures silence from THAT sighting,
    # never from the older `primera_pasada_en` -- `ultimo_visto_en` is the
    # one that moves forward.
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    primera_pasada = datetime.datetime(2026, 11, 1, 8, 0, tzinfo=datetime.UTC)
    ultimo_visto = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_primera_pasada(str(directorio), primera_pasada)
    modulo_alerta.marcar_ultimo_visto(str(directorio), ultimo_visto)
    # Past the threshold since `primera_pasada`, but NOT since `ultimo_visto`.
    monkeypatch.setattr(cli, "_ahora", lambda: ultimo_visto + datetime.timedelta(hours=23))

    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_sincronizar_marca_primera_pasada_en_la_primera_corrida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores()))

    momento = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: momento)
    assert cli.comando_sincronizar(config) == 0
    assert modulo_alerta.cargar_primera_pasada_en(str(directorio)) == momento


def test_sincronizar_no_sobrescribe_primera_pasada_en_corridas_siguientes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from duna_buzon.sincronizar import Contadores, ResultadoPasada

    config = _config(tmp_path)
    directorio = _escribir_fuente_y_reglas(tmp_path)
    monkeypatch.setattr(auth, "obtener_token", lambda cfg, directorio_fuente: "token-falso")
    monkeypatch.setattr(
        cli, "construir_proveedor", lambda fuente, token, cursor_proveedor_inicial=None: _ProveedorFalso()
    )
    monkeypatch.setattr(cli, "ejecutar_pasada", lambda **kwargs: ResultadoPasada(contadores=Contadores()))

    primero = datetime.datetime(2026, 11, 20, 8, 0, tzinfo=datetime.UTC)
    segundo = datetime.datetime(2026, 11, 20, 8, 10, tzinfo=datetime.UTC)
    monkeypatch.setattr(cli, "_ahora", lambda: primero)
    assert cli.comando_sincronizar(config) == 0
    monkeypatch.setattr(cli, "_ahora", lambda: segundo)
    assert cli.comando_sincronizar(config) == 0

    assert modulo_alerta.cargar_primera_pasada_en(str(directorio)) == primero


def test_alertas_json_silencio_se_limpia_tras_una_pasada_con_correo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_ultimo_visto(str(directorio), desde)
    ahora = desde + datetime.timedelta(hours=30)
    monkeypatch.setattr(cli, "_ahora", lambda: ahora)
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) != []

    # The next pass sees mail again -- the marker moves forward to now,
    # and the alert clears immediately.
    modulo_alerta.marcar_ultimo_visto(str(directorio), ahora)
    assert cli.comando_alertas(config) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_alertas_json_auth_tiene_prioridad_sobre_silencio_para_la_misma_fuente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A source already failing auth never also gets a redundant "silencio"
    # entry for the same underlying cause.
    config = _config(tmp_path)
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_auth_fallando(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=30))

    assert cli.comando_alertas(config) == 0
    salida = json.loads(capsys.readouterr().out)
    assert [entrada["tipo"] for entrada in salida] == ["auth"]


def test_estado_y_alertas_nunca_imprimen_el_token_finanzas_con_silencio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secreto = "TOKEN-FINANZAS-SECRETO-NO-DEBE-APARECER"
    config = Config.desde_entorno(
        {
            "BUZON_GRAPH_CLIENT_ID": "cliente",
            "BUZON_CLAVE_CIFRADO": CLAVE_VALIDA_BASE64,
            "FINANZAS_BUZON_TOKEN": secreto,
            "BUZON_DATOS": str(tmp_path),
            "BUZON_FINANZAS_URL": "http://127.0.0.1:8010",
        }
    )
    directorio = _escribir_fuente(tmp_path)
    desde = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)
    modulo_alerta.marcar_ultimo_visto(str(directorio), desde)
    monkeypatch.setattr(cli, "_ahora", lambda: desde + datetime.timedelta(hours=30))

    assert cli.comando_alertas(config) == 0
    assert secreto not in capsys.readouterr().out
