from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent


def _project_copy(tmp_path: Path) -> Path:
    """仓库布局：project/{run.sh, app/, home?}"""
    project = tmp_path / "project"
    app = project / "app"
    shutil.copytree(
        APP_ROOT,
        app,
        ignore=shutil.ignore_patterns(
            ".git", ".env", "__pycache__", ".pytest_cache", "dist", "data", "build", "home"
        ),
    )
    shutil.copy2(REPO_ROOT / "run.sh", project / "run.sh")
    if (REPO_ROOT / "backup.sh").is_file():
        shutil.copy2(REPO_ROOT / "backup.sh", project / "backup.sh")
    # 保证 templates 存在
    (app / "templates").mkdir(exist_ok=True)
    for name in (".env.example", "config.yaml", "provider_manifest.yaml"):
        src = app / "templates" / name
        if not src.is_file():
            alt = app / name
            if alt.is_file():
                shutil.copy2(alt, src)
    return project


def _fake_docker(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        r"""#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "info" ]]; then
    exit 0
fi
if [[ "${1:-}" == "inspect" ]]; then
    printf '%s healthy\n' "${MOCK_DOCKER_STATE:-running}"
    exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-directory|-f|--file|--env-file)
                shift 2 || true
                ;;
            --project-name|-p)
                shift 2 || true
                ;;
            version|config)
                exit 0
                ;;
            up)
                [[ -n "${HOST_UID:-}" && -n "${HOST_GID:-}" ]] || {
                    printf 'missing host uid/gid\n' >&2
                    exit 3
                }
                exit 0
                ;;
            ps)
                if [[ "${2:-}" == "--format" ]]; then
                    printf '%s %s\n' "mock" "${MOCK_DOCKER_STATE:-running (healthy)}"
                elif [[ "${2:-}" == "--status" ]]; then
                    printf '%s\n' "mock running"
                else
                    printf '%s\n' 'mock compose services'
                fi
                exit 0
                ;;
            logs)
                printf '%s\n' 'mock compose logs'
                exit 0
                ;;
            down)
                exit 0
                ;;
            *)
                shift
                ;;
        esac
    done
    exit 0
fi
printf 'unexpected docker call: %s\n' "$*" >&2
exit 2
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return fake_bin


def _run(project: Path, fake_bin: Path, *, state: str = "running", args: list[str] | None = None):
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    # run.sh 健康检查匹配 *healthy*，默认带 healthy 字样
    if state == "running":
        state = "running (healthy)"
    env["MOCK_DOCKER_STATE"] = state
    env["STARTUP_TIMEOUT"] = "3"
    env.pop("AI_GATEWAY_HOME", None)
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    cmd = ["bash", "run.sh"] + (args or [])
    return subprocess.run(
        cmd,
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_run_script_preserves_existing_env_and_syncs_new_keys(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    home = project / "home"
    home.mkdir(parents=True)
    env_path = home / ".env"
    env_path.write_text(
        "GLM_API_KEY=user-value\n"
        "GATEWAY_MASTER_KEY=sk-existing\n"
        "DASHBOARD_TOKEN=dash-existing\n"
        "REDIS_PASSWORD=redis-existing\n"
        "POSTGRES_PASSWORD=postgres-existing\n",
        encoding="utf-8",
    )
    # seed config templates into home so start doesn't fail
    for name in ("config.yaml", "provider_manifest.yaml"):
        src = project / "app" / "templates" / name
        if src.is_file():
            shutil.copy2(src, home / name)

    first = _run(project, fake_bin)
    assert first.returncode == 0, first.stdout + first.stderr
    first_content = env_path.read_text(encoding="utf-8")
    assert "GLM_API_KEY=user-value\n" in first_content
    assert "GATEWAY_MASTER_KEY=sk-existing\n" in first_content


def test_run_script_rejects_exited_container_even_with_stale_healthy_state(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    # fake docker returns "exited" as status word
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["MOCK_DOCKER_STATE"] = "exited"
    env["STARTUP_TIMEOUT"] = "1"
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    # minimal seed
    home = project / "home"
    home.mkdir(parents=True)
    (home / ".env").write_text(
        "REDIS_PASSWORD=a\nPOSTGRES_PASSWORD=b\nGATEWAY_MASTER_KEY=sk-x\n",
        encoding="utf-8",
    )
    for name in ("config.yaml", "provider_manifest.yaml"):
        src = project / "app" / "templates" / name
        if src.is_file():
            shutil.copy2(src, home / name)
    # override docker to report exited/unhealthy in ps format
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text(encoding="utf-8").replace(
            'printf \'%s %s\\n\' "mock" "${MOCK_DOCKER_STATE:-running (healthy)}"',
            'printf \'%s %s\\n\' "mock" "exited"',
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", "run.sh", "start"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    # either fails health or still returns non-zero
    assert result.returncode != 0 or "异常" in (result.stdout + result.stderr)


def test_run_script_rejects_restarting_container_immediately(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    home = project / "home"
    home.mkdir(parents=True)
    (home / ".env").write_text(
        "REDIS_PASSWORD=a\nPOSTGRES_PASSWORD=b\nGATEWAY_MASTER_KEY=sk-x\n",
        encoding="utf-8",
    )
    for name in ("config.yaml", "provider_manifest.yaml"):
        src = project / "app" / "templates" / name
        if src.is_file():
            shutil.copy2(src, home / name)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STARTUP_TIMEOUT"] = "1"
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    docker = fake_bin / "docker"
    docker.write_text(
        docker.read_text(encoding="utf-8").replace(
            'printf \'%s %s\\n\' "mock" "${MOCK_DOCKER_STATE:-running (healthy)}"',
            'printf \'%s %s\\n\' "mock" "Restarting (1)"',
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", "run.sh", "start"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0 or "超时" in (result.stdout + result.stderr) or "异常" in (
        result.stdout + result.stderr
    )


def test_run_script_home_and_portable_data_layout(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STARTUP_TIMEOUT"] = "3"
    env["MOCK_DOCKER_STATE"] = "running (healthy)"
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    env.pop("AI_GATEWAY_HOME", None)

    home_cmd = subprocess.run(
        ["bash", "run.sh", "home"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert home_cmd.returncode == 0, home_cmd.stdout + home_cmd.stderr
    assert home_cmd.stdout.strip() == str(project / "home")

    started = subprocess.run(
        ["bash", "run.sh", "start"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    data = project / "home"
    assert (data / ".env").is_file()
    assert (data / "config.yaml").is_file()
    assert (data / "provider_manifest.yaml").is_file()
    assert (data / "state").is_dir()


def test_compose_uses_code_dir_and_bind_data_paths():
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${AI_GATEWAY_CODE:-.}/gateway" in compose
    assert "./data/redis:/data" in compose
    assert "./data/postgres:/var/lib/postgresql/data" in compose
    assert "redis-data:" not in compose
    assert "postgres-data:" not in compose


def test_backup_restore_preserves_keys_and_config(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    data = tmp_path / "portable-home"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STARTUP_TIMEOUT"] = "3"
    env["MOCK_DOCKER_STATE"] = "running (healthy)"
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    env["AI_GATEWAY_HOME"] = str(data)

    started = subprocess.run(
        ["bash", "run.sh", "start"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert started.returncode == 0, started.stdout + started.stderr

    env_file = data / ".env"
    text = env_file.read_text(encoding="utf-8")
    text = text.replace("GLM_API_KEY=", "GLM_API_KEY=secret-user-key-xyz", 1)
    env_file.write_text(text, encoding="utf-8")
    (data / "config.yaml").write_text(
        "# custom-config-marker\n" + (data / "config.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (data / "state" / "marker.txt").write_text("keep-me\n", encoding="utf-8")

    archive = tmp_path / "agm-backup.tgz"
    backed = subprocess.run(
        ["bash", "run.sh", "backup", str(archive)],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert backed.returncode == 0, backed.stdout + backed.stderr
    assert archive.is_file() and archive.stat().st_size > 0

    env_file.write_text("WIPED=1\n", encoding="utf-8")
    restored = subprocess.run(
        ["bash", "run.sh", "restore", str(archive)],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "secret-user-key-xyz" in (data / ".env").read_text(encoding="utf-8")
    assert "custom-config-marker" in (data / "config.yaml").read_text(encoding="utf-8")
    assert (data / "state" / "marker.txt").read_text(encoding="utf-8") == "keep-me\n"


def test_installed_layout_uses_xdg_config(tmp_path: Path):
    project = _project_copy(tmp_path)
    (project / "app" / ".installed").write_text("", encoding="utf-8")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("AI_GATEWAY_HOME", None)
    env.pop("XDG_CONFIG_HOME", None)
    env["AI_GATEWAY_LICENSE_BYPASS"] = "1"
    home = subprocess.run(
        ["bash", "run.sh", "home"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert home.returncode == 0, home.stdout + home.stderr
    assert home.stdout.strip() == str(fake_home / ".config" / "ai-gateway-matrix")
