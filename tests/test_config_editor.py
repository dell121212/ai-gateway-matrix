from pathlib import Path
import shutil

import yaml

from gateway import channel_ids
from dashboard.config_editor import update_priority
from dashboard.channel_loader import write_env_var


def test_priority_update_keeps_unanchored_direct_copy_in_sync(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    shutil.copy2(source, target)
    assert update_priority(
        target,
        "free-pool",
        "openai/Qwen/Qwen2.5-7B-Instruct",
        "https://api.siliconflow.cn/v1",
        "SILICONFLOW_API_KEY",
        777,
    )
    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    main = next(
        item for item in config["model_list"]
        if item["model_name"] == "free-pool"
        and item["litellm_params"]["model"] == "openai/Qwen/Qwen2.5-7B-Instruct"
    )
    direct_name = channel_ids.make_direct_model_name(
        main["litellm_params"]["model"],
        main["litellm_params"].get("api_base"),
        "SILICONFLOW_API_KEY",
    )
    direct = next(item for item in config["model_list"] if item["model_name"] == direct_name)
    assert direct["litellm_params"] == main["litellm_params"]


def test_env_writer_rejects_line_injection(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("GLM_API_KEY=old\n", encoding="utf-8")
    try:
        write_env_var("GLM_API_KEY", "new\nOTHER_KEY=injected", env_file)
    except ValueError:
        pass
    else:
        raise AssertionError("换行注入应被拒绝")
    assert env_file.read_text(encoding="utf-8") == "GLM_API_KEY=old\n"
