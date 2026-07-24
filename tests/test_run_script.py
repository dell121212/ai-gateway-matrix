from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _project_copy(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(
        ROOT,
        project,
        ignore=shutil.ignore_patterns(".git", ".env", "__pycache__", ".pytest_cache", "dist", "data"),
    )
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
    # 跳过 run.sh 传入的 --project-directory / -f 等全局选项
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
                printf '%s\n' 'mock compose services'
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


def _run(project: Path, fake_bin: Path, *, state: str = "running"):
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["MOCK_DOCKER_STATE"] = state
    env["STARTUP_TIMEOUT"] = "1"
    # 隔离宿主残留的可移植目录 / 授权豁免，避免污染 DATA_DIR
    env.pop("AI_GATEWAY_HOME", None)
    env.pop("AI_GATEWAY_LICENSE_BYPASS", None)
    return subprocess.run(
        ["bash", "run.sh"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_run_script_preserves_existing_env_and_syncs_new_keys(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    env_path = project / ".env"
    env_path.write_text(
        "GLM_API_KEY=user-value\n"
        "GATEWAY_MASTER_KEY=sk-existing\n"
        "DASHBOARD_TOKEN=dash-existing\n"
        "REDIS_PASSWORD=redis-existing\n"
        "POSTGRES_PASSWORD=postgres-existing\n",
        encoding="utf-8",
    )

    first = _run(project, fake_bin)
    assert first.returncode == 0, first.stdout + first.stderr
    first_content = env_path.read_text(encoding="utf-8")
    assert "GLM_API_KEY=user-value\n" in first_content
    assert "GATEWAY_MASTER_KEY=sk-existing\n" in first_content
    assert "AIMLAPI_API_KEY=\n" in first_content
    assert "DASHBOARD_AUTH=local\n" in first_content
    assert "仪表盘使用仅本机免登录模式" in first.stdout
    assert env_path.stat().st_mode & 0o777 == 0o600

    second = _run(project, fake_bin)
    assert second.returncode == 0, second.stdout + second.stderr
    assert env_path.read_text(encoding="utf-8") == first_content


def test_run_script_rejects_exited_container_even_with_stale_healthy_state(
    tmp_path: Path,
):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)

    result = _run(project, fake_bin, state="exited")

    assert result.returncode != 0
    assert "exited/healthy" in result.stderr
    assert "mock compose logs" in result.stderr


def test_run_script_rejects_restarting_container_immediately(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)

    result = _run(project, fake_bin, state="restarting")

    assert result.returncode != 0
    assert "restarting/healthy" in result.stderr
    assert "等待服务健康超时" not in result.stderr


def test_writable_services_use_the_host_file_owner():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count('user: "${HOST_UID:-1000}:${HOST_GID:-1000}"') == 2


def test_run_script_home_and_portable_data_layout(tmp_path: Path):
    project = _project_copy(tmp_path)
    fake_bin = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["STARTUP_TIMEOUT"] = "1"
    env.pop("AI_GATEWAY_LICENSE_BYPASS", None)
    env["AI_GATEWAY_HOME"] = str(tmp_path / "portable-home")

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
    assert home.stdout.strip() == str(tmp_path / "portable-home")

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
    data = tmp_path / "portable-home"
    assert (data / ".env").is_file()
    assert (data / "config.yaml").is_file()
    assert (data / "provider_manifest.yaml").is_file()
    assert (data / "state").is_dir()
    assert (data / "data" / "redis").is_dir()
    assert (data / "data" / "postgres").is_dir()
    assert (data / ".env").stat().st_mode & 0o777 == 0o600


def test_compose_uses_code_dir_and_bind_data_paths():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${AI_GATEWAY_CODE:-.}/gateway" in compose
    assert "./data/redis:/data" in compose
    assert "./data/postgres:/var/lib/postgresql/data" in compose
    assert "redis-data:" not in compose
    assert "postgres-data:" not in compose
