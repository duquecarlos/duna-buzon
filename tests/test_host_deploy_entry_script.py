"""BUZON-CONECTOR-1: `deploy/host/duna-deploy-buzon` is the root-owned entry
script Carlos installs once at `/usr/local/sbin/duna-deploy-buzon`, called
through a matching no-password sudoers fragment. This suite never runs it
with root privileges, installs it anywhere, or touches a real service --
it checks its TEXT and its DECISION logic directly, the same way
duna-cuenta-api's own `tests/test_host_deploy_entry_script.py` does.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = REPO_ROOT / "deploy" / "host" / "duna-deploy-buzon"
SUDOERS_FRAGMENT = REPO_ROOT / "deploy" / "host" / "duna-deploy-buzon.sudoers"
HOST_README = REPO_ROOT / "deploy" / "host" / "README.md"

_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash is not on PATH on this machine")


def _contenido() -> str:
    return ENTRY_SCRIPT.read_text(encoding="utf-8")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_BASH, str(ENTRY_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_el_script_existe_y_usa_solo_lf() -> None:
    assert ENTRY_SCRIPT.is_file()
    assert b"\r" not in ENTRY_SCRIPT.read_bytes()


def test_bash_analiza_el_script_sin_error() -> None:
    resultado = subprocess.run(
        [_BASH, "-n", str(ENTRY_SCRIPT)], check=False, capture_output=True, text=True
    )
    assert resultado.returncode == 0, resultado.stderr


def test_set_eeuo_pipefail_esta_activo() -> None:
    assert "set -Eeuo pipefail" in _contenido()


def test_solo_los_subcomandos_fijos_son_aceptados() -> None:
    contenido = _contenido()
    assert "status)" in contenido
    assert "deploy)" in contenido
    assert "entrar)" in contenido
    assert "--dry-run" in contenido
    assert re.search(r"\*\)\s*\n\s*usage\s*\n", contenido)


def test_subcomando_desconocido_se_rechaza_con_uso() -> None:
    resultado = _run("frobnicate")
    assert resultado.returncode == 2
    assert "usage" in resultado.stderr.lower()


def test_sin_argumentos_se_rechaza_con_uso() -> None:
    resultado = _run()
    assert resultado.returncode == 2
    assert "usage" in resultado.stderr.lower()


def test_argumento_extra_tipo_ruta_se_rechaza() -> None:
    resultado = _run("status", "/etc/passwd")
    assert resultado.returncode == 2
    assert "usage" in resultado.stderr.lower()

    resultado = _run("entrar", "algo-mas")
    assert resultado.returncode == 2
    assert "usage" in resultado.stderr.lower()

    resultado = _run("deploy", "--owner-env-file", "/tmp/evil.env")
    assert resultado.returncode == 2
    assert "usage" in resultado.stderr.lower()


def test_la_forma_de_los_argumentos_se_verifica_antes_del_rechazo_por_euid() -> None:
    contenido = _contenido()
    assert contenido.index("parse_args() {") < contenido.index("require_root() {")

    bloque_main = contenido[contenido.index("__main() {") :]
    assert bloque_main.index('parse_args "$@"') < bloque_main.index("require_root\n")


def test_rechazo_por_euid() -> None:
    # This test process is not root.
    resultado = _run("status")
    assert resultado.returncode == 1
    assert "root" in resultado.stderr.lower()
    assert "euid" in resultado.stderr.lower() or "0" in resultado.stderr


@pytest.mark.parametrize("subcomando", ["status", "deploy", "entrar"])
def test_rechazo_por_euid_cubre_cada_subcomando(subcomando: str) -> None:
    resultado = _run(subcomando)
    assert resultado.returncode == 1
    assert "root" in resultado.stderr.lower()


def test_las_rutas_fijas_nunca_vienen_de_argv() -> None:
    contenido = _contenido()
    assert 'REPO="/srv/duna/repos/duna-buzon"' in contenido
    assert 'DATOS_DIR="/srv/duna/data/buzon"' in contenido
    assert 'ENV_CLIENTE="/srv/duna/env/buzon.env"' in contenido
    assert 'ENV_TOKEN="/srv/duna/env/buzon-conector.env"' in contenido
    assert not re.search(r'REPO="?\$[12@]', contenido)
    assert not re.search(r'DATOS_DIR="?\$[12@]', contenido)


def test_deploy_dry_run_no_ejecuta_nada_real() -> None:
    resultado = _run("deploy", "--dry-run")
    # Still refused: this test runs as non-root, and --dry-run does not
    # bypass the EUID check -- it only changes what `deploy` itself does
    # once past it, proven by text inspection below.
    assert resultado.returncode == 1

    contenido = _contenido()
    bloque_cmd_deploy = contenido[contenido.index("cmd_deploy() {") : contenido.index("cmd_entrar() {")]
    indice_dry_run = bloque_cmd_deploy.index('if [[ "$dry_run" == "1" ]]')
    indice_fin_dry_run = bloque_cmd_deploy.index("return 0", indice_dry_run)
    bloque_dry_run = bloque_cmd_deploy[indice_dry_run : indice_fin_dry_run + len("return 0")]

    # Every line inside the --dry-run branch only PRINTS a description --
    # the real commands below appear only as text inside an echo, never as
    # a bare statement that would actually run.
    for numero, linea in enumerate(bloque_dry_run.splitlines()[1:-1], start=1):
        despojada = linea.strip()
        if not despojada:
            continue
        assert despojada.startswith("echo "), (
            f"line {numero} of the --dry-run branch is not an echo: {despojada!r}"
        )

    # And, past the dry-run branch, the real commands DO appear in this
    # function as bare statements -- confirming the block above was the
    # right slice and that --dry-run is not simply skipping real work that
    # was never there.
    bloque_real = bloque_cmd_deploy[indice_fin_dry_run:]
    for comando_real in ("install -d", "install -o root", "systemctl daemon-reload", "systemctl enable", "git -C"):
        assert comando_real in bloque_real


def test_ninguna_llamada_a_git_es_sin_runuser() -> None:
    for numero, linea in enumerate(_contenido().splitlines(), start=1):
        despojada = linea.strip()
        if despojada.startswith("#"):
            continue
        if re.search(r"\bgit\b", linea):
            assert "runuser -u" in linea, f"line {numero} runs git without runuser -u: {linea!r}"


def test_antes_de_deploy_se_captura_el_sha_antes_de_pullear() -> None:
    contenido = _contenido()
    indice_before = contenido.index('before="$(runuser -u "$owner" -- git -C "$REPO" rev-parse HEAD)"')
    indice_pull = contenido.index('runuser -u "$owner" -- git -C "$REPO" pull --ff-only')
    assert indice_before < indice_pull


def _lineas_de_codigo(contenido: str) -> list[str]:
    return [linea for linea in contenido.splitlines() if not linea.strip().startswith("#")]


def test_entrar_nunca_usa_systemd_run_ni_pty() -> None:
    contenido = _contenido()
    bloque_entrar = contenido[contenido.index("cmd_entrar() {"): contenido.index("dispatch() {")]
    assert "systemd-run" not in bloque_entrar
    assert not any("--pty" in linea for linea in _lineas_de_codigo(bloque_entrar))


def test_entrar_abre_los_env_como_root_por_fd_nunca_por_ruta_bajo_duna() -> None:
    # Regression guard (BUZON-ENTRAR-ENV-ROOT-1): `duna` cannot traverse
    # /srv/duna/env/ on the real host, so the duna-owned child must never
    # source either env file BY PATH itself -- root opens both files (via
    # redirection attached to the `runuser` line, so root does the open)
    # and hands them to the child as already-open fds 3 and 4, which the
    # child sources from /dev/fd/N.
    contenido = _contenido()
    bloque_entrar = contenido[contenido.index("cmd_entrar() {"): contenido.index("dispatch() {")]

    assert '. "$ENV_CLIENTE"' not in bloque_entrar
    assert '. "$ENV_TOKEN"' not in bloque_entrar
    assert ". /dev/fd/3" in bloque_entrar
    assert ". /dev/fd/4" in bloque_entrar

    # The redirections that open the files are on the `runuser` command
    # itself, i.e. still executed by root, outside the single-quoted script
    # the duna-owned child runs.
    indice_runuser = bloque_entrar.index("exec runuser -u duna")
    indice_cierre_comilla = bloque_entrar.rindex("'")
    linea_redirecciones = bloque_entrar[indice_cierre_comilla:]
    assert '3<"$ENV_CLIENTE"' in linea_redirecciones
    assert '4<"$ENV_TOKEN"' in linea_redirecciones
    assert indice_runuser < indice_cierre_comilla

    # The fds are closed again before the real python process replaces the
    # shell.
    indice_cierre_fds = bloque_entrar.index("3<&- 4<&-")
    indice_exec_python = bloque_entrar.index("duna_buzon entrar")
    assert indice_cierre_fds < indice_exec_python


def test_entrar_rechaza_si_falta_el_venv_antes_de_runuser() -> None:
    # Carlos hit `entrar` before `deploy` had ever created the venv on the
    # real host -- refuse with a clear one-line message, before runuser.
    contenido = _contenido()
    bloque_entrar = contenido[contenido.index("cmd_entrar() {"): contenido.index("dispatch() {")]

    indice_check = bloque_entrar.index("if [[ ! -x")
    indice_exec_runuser = bloque_entrar.index("exec runuser")
    assert indice_check < indice_exec_runuser
    assert "deploy" in bloque_entrar[indice_check:indice_exec_runuser]
    assert "$REPO/.venv/bin/python" in bloque_entrar[indice_check:indice_exec_runuser]


def test_status_y_deploy_nunca_usan_pty() -> None:
    contenido = _contenido()
    # "--pty" appears only in the explanatory header comment, never as an
    # actual flag on a real command line.
    assert not any("--pty" in linea for linea in _lineas_de_codigo(contenido))
    assert "systemd-run --pipe --quiet --wait" in contenido


def test_ningun_valor_de_entorno_se_imprime() -> None:
    contenido = _contenido()
    lineas_con_salida = [
        linea for linea in contenido.splitlines() if re.match(r"\s*(echo|printf)\b", linea)
    ]
    assert lineas_con_salida
    for linea in lineas_con_salida:
        assert "cat " not in linea
        assert "$FINANZAS_BUZON_TOKEN" not in linea
        assert "$BUZON_CLAVE_CIFRADO" not in linea


def test_los_archivos_de_entorno_nunca_se_leen_con_cat() -> None:
    assert "cat " not in _contenido()
    assert "cat\n" not in _contenido()


def test_fragmento_sudoers_es_exactamente_una_linea() -> None:
    assert SUDOERS_FRAGMENT.is_file()
    assert b"\r" not in SUDOERS_FRAGMENT.read_bytes()
    lineas = SUDOERS_FRAGMENT.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1
    assert lineas[0] == "carlos ALL=(root) NOPASSWD: /usr/local/sbin/duna-deploy-buzon"


def test_readme_instala_los_archivos_correctos() -> None:
    assert HOST_README.is_file()
    contenido = HOST_README.read_text(encoding="utf-8")
    assert (
        "sudo install -o root -g root -m 755 deploy/host/duna-deploy-buzon "
        "/usr/local/sbin/duna-deploy-buzon" in contenido
    )
    assert (
        "sudo install -o root -g root -m 440 deploy/host/duna-deploy-buzon.sudoers "
        "/etc/sudoers.d/duna-deploy-buzon" in contenido
    )
    assert "sudo visudo -c" in contenido
    assert "remove" in contenido.lower() or "## remove" in contenido.lower()
    assert "trust boundary" in contenido.lower()
    assert "no backup" in contenido.lower()


def test_unidades_systemd_existen_con_la_forma_correcta() -> None:
    servicio = (REPO_ROOT / "deploy" / "systemd" / "duna-buzon.service").read_text(encoding="utf-8")
    temporizador = (REPO_ROOT / "deploy" / "systemd" / "duna-buzon.timer").read_text(encoding="utf-8")

    assert "Type=oneshot" in servicio
    assert "User=duna" in servicio
    assert "EnvironmentFile=/srv/duna/env/buzon.env" in servicio
    assert "EnvironmentFile=/srv/duna/env/buzon-conector.env" in servicio
    assert "ExecStart=/srv/duna/repos/duna-buzon/.venv/bin/python -m duna_buzon sincronizar" in servicio
    assert not any(
        linea.strip().startswith("Restart=") for linea in servicio.splitlines()
    )

    assert "Persistent=true" in temporizador
    assert "Unit=duna-buzon.service" in temporizador
    assert "WantedBy=timers.target" in temporizador
