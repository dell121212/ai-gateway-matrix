#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速启动脚本 (v2 新增)
————————————————————————————————
用法：
  python3 -m scripts.quickstart              # 交互式引导
  python3 -m scripts.quickstart --check      # 只做环境检查
  python3 -m scripts.quickstart --gen-key    # 生成随机 master key

功能：
  1. 检查 Python 版本、依赖是否齐全
  2. 检查 .env 文件是否存在、必填项是否已填
  3. 生成随机 GATEWAY_MASTER_KEY
  4. 运行 scripts/test_gateway.py 体检
  5. 提示下一步操作
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str):
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def check_python():
    """检查 Python 版本。"""
    print(f"\n{BOLD}── Python 环境 ──{RESET}")
    version = sys.version_info
    if version >= (3, 10):
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        fail(f"Python {version.major}.{version.minor} 版本过低，需要 3.10+")
        return False
    return True


def check_dependencies():
    """检查依赖库。"""
    print(f"\n{BOLD}── 依赖检查 ──{RESET}")
    deps = {"yaml": "pyyaml", "litellm": "litellm", "requests": "requests"}
    all_ok = True
    for module, pip_name in deps.items():
        try:
            __import__(module)
            ok(f"{pip_name}")
        except ImportError:
            fail(f"{pip_name} 未安装 (pip install {pip_name})")
            all_ok = False
    return all_ok


def check_env():
    """检查 .env 文件。"""
    print(f"\n{BOLD}── 环境变量 ──{RESET}")
    env_file = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"

    if not env_file.exists():
        if env_example.exists():
            warn(".env 不存在，从 .env.example 复制中...")
            import shutil
            shutil.copy(env_example, env_file)
            ok("已创建 .env，请编辑填入真实 API key")
        else:
            fail(".env 和 .env.example 都不存在")
        return False

    # 检查必填项
    required = [
        "GATEWAY_MASTER_KEY", "DASHBOARD_TOKEN", "REDIS_PASSWORD", "POSTGRES_PASSWORD",
    ]
    optional = [
        "GLM_API_KEY", "MISTRAL_KEY_1", "MISTRAL_KEY_2",
        "GEMINI_API_KEY", "GROQ_API_KEY", "SILICONFLOW_API_KEY",
    ]

    env_vars = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

    all_ok = True
    for key in required:
        if not env_vars.get(key):
            fail(f"{key} 未设置（必填）")
            all_ok = False
        else:
            ok(f"{key} 已设置")

    filled_optional = sum(1 for k in optional if env_vars.get(k))
    warn(f"已填写的免费渠道 key: {filled_optional}/{len(optional)}")

    return all_ok


def check_config():
    """检查 config.yaml 和 gateway/custom_router_hook.py。"""
    print(f"\n{BOLD}── 配置文件 ──{RESET}")
    config = PROJECT_ROOT / "config.yaml"
    hook = PROJECT_ROOT / "gateway" / "custom_router_hook.py"
    compose = PROJECT_ROOT / "docker-compose.yml"

    all_ok = True
    for f in [config, hook, compose]:
        if f.exists():
            ok(f"{f.name} 存在")
        else:
            fail(f"{f.name} 缺失")
            all_ok = False
    return all_ok


def run_tests():
    """运行 scripts/test_gateway.py。"""
    print(f"\n{BOLD}── 体检测试 ──{RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.test_gateway"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    # 过滤掉 WARNING 行
    output = "\n".join(
        line for line in (result.stdout + result.stderr).splitlines()
        if "WARNING" not in line and "register_model" not in line
    )
    print(output)
    return result.returncode == 0


def gen_key():
    """生成随机 master key。"""
    key = "sk-" + secrets.token_hex(24)
    print(f"\n{BOLD}生成的随机 key:{RESET}")
    print(f"  {key}")
    print("\n把它填到 .env 的 GATEWAY_MASTER_KEY= 后面即可。")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Gateway Matrix 快速启动")
    parser.add_argument("--check", action="store_true", help="只做环境检查")
    parser.add_argument("--gen-key", action="store_true", help="生成随机 master key")
    args = parser.parse_args()

    if args.gen_key:
        gen_key()
        return

    print(f"\n{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}║     AI Gateway Matrix — 快速启动向导 (v2)                   ║{RESET}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")

    checks = [
        ("Python", check_python),
        ("依赖", check_dependencies),
        ("配置文件", check_config),
        ("环境变量", check_env),
    ]

    all_ok = True
    for name, check_fn in checks:
        if not check_fn():
            all_ok = False

    if all_ok:
        print(f"\n{GREEN}{BOLD}✅ 环境检查全部通过！{RESET}")
        if not args.check:
            run_tests()
            print(f"\n{BOLD}── 下一步 ──{RESET}")
            print("  1. 编辑 .env，填入你的免费 API key")
            print("  2. 运行 docker-compose up -d 启动网关")
            print("  3. 运行 python3 -m scripts.health_check 检查渠道状态")
            print("  4. 用 Vibe CLI 或 curl 调用 http://127.0.0.1:4000/v1/chat/completions")
    else:
        print(f"\n{YELLOW}{BOLD}⚠  有问题需要先修复，请看上面的 ✗ 标记{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
