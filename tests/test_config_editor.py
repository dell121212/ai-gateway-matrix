from pathlib import Path
import shutil

import yaml

from gateway import channel_ids
from dashboard.config_editor import update_model, update_priority
from dashboard.channel_loader import read_env_file, write_env_var


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


def test_model_update_keeps_unanchored_direct_copy_in_sync(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    shutil.copy2(source, target)
    old_model = "openai/Qwen/Qwen2.5-7B-Instruct"
    new_model = "openai/user-selected-model"
    api_base = "https://api.siliconflow.cn/v1"
    env_var = "SILICONFLOW_API_KEY"

    assert update_model(
        target, "free-pool", old_model, api_base, env_var, new_model
    )
    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    main = next(
        item for item in config["model_list"]
        if item["model_name"] == "free-pool"
        and item["litellm_params"]["model"] == new_model
        and item["litellm_params"].get("api_base") == api_base
    )
    old_direct = channel_ids.make_direct_model_name(old_model, api_base, env_var)
    new_direct = channel_ids.make_direct_model_name(new_model, api_base, env_var)
    assert not any(item["model_name"] == old_direct for item in config["model_list"])
    direct = next(item for item in config["model_list"] if item["model_name"] == new_direct)
    assert direct["litellm_params"] == main["litellm_params"]


def test_model_update_keeps_anchored_trusted_and_direct_entries_in_sync(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    shutil.copy2(source, target)
    old_model = "groq/openai/gpt-oss-20b"
    new_model = "groq/user-selected-model"
    env_var = "GROQ_API_KEY"

    assert update_model(
        target, "fast-pool", old_model, None, env_var, new_model
    )
    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    main = next(
        item for item in config["model_list"]
        if item["model_name"] == "fast-pool"
        and item["litellm_params"]["model"] == new_model
    )
    new_direct = channel_ids.make_direct_model_name(new_model, None, env_var)
    direct = next(item for item in config["model_list"] if item["model_name"] == new_direct)
    trusted = next(
        item for item in config["model_list"]
        if item["model_name"] == "trusted-pool"
        and item["litellm_params"]["model"] == new_model
    )
    assert direct["litellm_params"] == main["litellm_params"]
    assert trusted["litellm_params"] == main["litellm_params"]


def test_model_update_rejects_yaml_injection(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    target = tmp_path / "config.yaml"
    shutil.copy2(source, target)
    before = target.read_text(encoding="utf-8")

    try:
        update_model(
            target,
            "fast-pool",
            "groq/openai/gpt-oss-20b",
            None,
            "GROQ_API_KEY",
            "model\npriority: 999",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("模型名称换行注入应被拒绝")
    assert target.read_text(encoding="utf-8") == before


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


def test_env_writer_quotes_compose_interpolation_characters(tmp_path: Path):
    env_path = tmp_path / ".env"
    write_env_var("GLM_API_KEY", "key-$literal value'part", env_path)
    assert env_path.read_text(encoding="utf-8") == (
        "GLM_API_KEY='key-$literal value\\'part'\n"
    )
    assert read_env_file(env_path) == {
        "GLM_API_KEY": "key-$literal value'part"
    }
