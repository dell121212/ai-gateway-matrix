#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地结构性自检脚本 (v4 — 引入指定模型分类)——在不接真实 API key 的情况下，验证：
  1. config.yaml 能被正确解析、能成功构造 litellm.Router
  2. fast-pool / free-pool / strong-model-pool / trusted-pool / auto-route 分组正确
  3. gateway/custom_router_hook.py 的复杂度路由规则按预期工作
  4. 每个 deployment 的 rpm / max_budget / budget_duration 字段都被正确加载
  5. [v2 新增] async_pre_call_hook 在 call_type="acompletion" 下真的会执行路由
  6. [v2 新增] 多模态 content (list 格式) 不会让 hook 崩溃
  7. [v2 新增] 并发选择稳定性
  8. [v3 新增] 敏感内容检测优先级高于复杂度升级规则——同时命中"重构"关键词
     和一个 AWS Key 时，必须去 trusted-pool 而不是 strong-model-pool
  9. [v4 新增] llm_classifier.classify_task 在这个脚本里全程被 mock 掉
     （要么强制返回 None 模拟"分类器不可用"，要么强制返回固定档位验证
     "分类器可用时路由采信分类器判断"）——因为 async_pre_call_hook 现在
     默认会真的去调用分类器指定的模型，如果不 mock，这个"纯配置体检"脚本
     就会在没配真实 GROQ_API_KEY 的情况下对外发起一次会失败的网络请求，
     这既违反脚本"不发真实请求"的原本承诺，也会让测试结果依赖网络状况。

跑这个脚本不会真的发请求出去，纯粹是部署前的"配置体检"。
真正接上真实 key 之后，记得照 README 里说的，故意把某个免费渠道跑到限流，
肉眼确认 fallback 真的生效——这一步脚本测不出来，必须真实环境验证。
同时建议观察一段时间 hook 统计里的 classifier_used / classifier_fallback_to_heuristic
比例，如果 fallback 占比长期很高，说明分类器指定的模型（默认 Groq GPT-OSS 20B）
本身不稳定，需要考虑换一个更稳的渠道当分类器。

v2 修复：
  · 修复 test_router 在函数内被引用但定义在后面的 late-binding 问题
    （虽然 Python 允许，但容易出错，现在显式传参）
  · 修复 call_type 测试用 "acompletion" 而非 "completion"
    （原版测试用 "completion" 能过，但真实代理路径传的是 "acompletion"，
     这正是原版 hook bug 的掩盖者）
  · 新增多模态 content 测试
  · 新增 hook 统计计数器验证

v4 修复：
  · 原来"中等长度→free-pool"这条测试样例文本只有 235 字符，其实低于
    FAST_CHAR_THRESHOLD（600），从引入 fast-pool 阈值那天起就应该一直
    被误判成 fast-pool，只是没人跑出来过——这次改成 15 次重复（约 700 字符）
    让它真正落在 free-pool 区间。同理修正了"分类器覆盖"测试用例的样例长度。
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

import yaml


def _set_dummy_env():
    """设置 dummy 环境变量，让 config.yaml 里的 os.environ/ 引用不报错。"""
    for var in (
        "GLM_API_KEY", "MISTRAL_KEY_1", "MISTRAL_KEY_2",
        "RELAY_STATION_KEY", "GATEWAY_MASTER_KEY",
        "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "DATABASE_URL", "POSTGRES_PASSWORD",
        "DASHBOARD_TOKEN", "CLASSIFIER_API_KEY",
        "AGNES_API_KEY",
        # v4 新增
        "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "SAMBANOVA_API_KEY",
        "SILICONFLOW_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY", "GITHUB_TOKEN", "HF_TOKEN",
        "NVIDIA_API_KEY", "DEEPINFRA_API_KEY", "NOVITA_API_KEY",
        "FIREWORKS_API_KEY", "LEPTON_API_KEY",
        "DASHSCOPE_API_KEY", "HUNYUAN_API_KEY", "QIANFAN_API_KEY",
        "MOONSHOT_API_KEY",
        "AIHUBMIX_API_KEY", "VERCEL_AI_API_KEY", "GLAMA_API_KEY",
        "AIMLAPI_API_KEY",
    ):
        if var == "REDIS_HOST":
            os.environ.setdefault(var, "localhost")
        elif var == "REDIS_PORT":
            os.environ.setdefault(var, "6379")
        elif var == "DATABASE_URL":
            os.environ.setdefault(var, "postgresql://litellm:litellm@localhost:5432/litellm")
        else:
            os.environ.setdefault(var, f"test-fixture-{var.lower()}")


async def main() -> int:
    _set_dummy_env()
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. YAML 结构 ──────────────────────────────────────────
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    print(f"[1/9] config.yaml 解析 OK，共 {len(cfg['model_list'])} 个 deployment")

    # ── 2. Router 构造 ────────────────────────────────────────
    from litellm import Router
    router = Router(
        model_list=cfg["model_list"],
        routing_strategy=cfg.get("router_settings", {}).get("routing_strategy", "simple-shuffle"),
        enable_pre_call_checks=cfg.get("router_settings", {}).get("enable_pre_call_checks", False),
        num_retries=cfg.get("router_settings", {}).get("num_retries", 3),
        cooldown_time=cfg.get("router_settings", {}).get("cooldown_time", 60),
    )
    model_groups: dict[str, int] = {}
    for m in cfg["model_list"]:
        name = m["model_name"]
        model_groups[name] = model_groups.get(name, 0) + 1
    print(f"[2/9] Router 构造 OK：{model_groups}")

    # v2 新增：检查 auto-route 是否存在
    if "auto-route" not in model_groups:
        errors.append("config.yaml 里缺少 auto-route 模型分组——客户端调用 model='auto-route' 会直接 404")
    else:
        print(f"     ✓ auto-route 分组存在（{model_groups['auto-route']} 个 deployment）")

    # v3 新增：检查四层路由池是否存在
    for pool in ("fast-pool", "free-pool", "strong-model-pool", "trusted-pool"):
        if pool not in model_groups:
            errors.append(f"config.yaml 里缺少 {pool} 模型分组——路由无法工作")
        else:
            print(f"     ✓ {pool} 分组存在（{model_groups[pool]} 个 deployment）")

    # v3 新增：trusted-pool 里不应该出现任何"观察期/未验证"渠道
    # （目前只有 Agnes AI 属于这一类，用 api_base 关键字做个粗略的黑名单检查）
    UNTRUSTED_API_BASE_MARKERS = ("agnes-ai.com", "aihubmix.com", "vercel.sh", "glama.ai", "aimlapi.com")
    for m in cfg["model_list"]:
        if m["model_name"] != "trusted-pool":
            continue
        api_base = (m.get("litellm_params", {}) or {}).get("api_base", "") or ""
        for marker in UNTRUSTED_API_BASE_MARKERS:
            if marker in api_base:
                errors.append(
                    f"trusted-pool 里混入了一个不该在这里出现的渠道（api_base 含 '{marker}'）——"
                    "这会让敏感内容检测的安全兜底失效"
                )
    print("     ✓ trusted-pool 未验证渠道黑名单检查完成")

    # v6 新增：检查 direct-* 分组（"限时优先"功能靠这些分组做精确寻址）
    direct_groups = {k: v for k, v in model_groups.items() if k.startswith("direct-")}
    fast_free_strong_count = sum(model_groups.get(p, 0) for p in ("fast-pool", "free-pool", "strong-model-pool"))
    direct_group_count_matches = len(direct_groups) == fast_free_strong_count
    if not direct_group_count_matches:
        errors.append(
            f"direct-* 分组数量（{len(direct_groups)}）应该等于 fast/free/strong-pool "
            f"的 deployment 总数（{fast_free_strong_count}）——'限时优先'功能需要每个渠道"
            "都有一个专属的直连地址，数量对不上说明有渠道漏加了"
        )
    multi_member_direct = {k: v for k, v in direct_groups.items() if v != 1}
    if multi_member_direct:
        errors.append(
            f"以下 direct-* 分组里的 deployment 数量不是 1（{multi_member_direct}）——"
            "'限时优先'功能依赖每个 direct-* 分组恰好只有一个 deployment 才能精确寻址，"
            "多个会导致 Router 在里面做负载均衡，路由到哪个渠道变得不确定"
        )
    if direct_group_count_matches and not multi_member_direct:
        print(f"     ✓ direct-* 寻址分组检查完成（共 {len(direct_groups)} 个，均为单一 deployment）")

    # ── 3. 预算/限速字段健全性 ────────────────────────────────
    for i, m in enumerate(cfg["model_list"]):
        params = m.get("litellm_params", {})
        name = m["model_name"]
        has_budget = "max_budget" in params
        has_duration = "budget_duration" in params
        # 有 max_budget 就必须有 budget_duration，否则预算永不重置
        if has_budget and not has_duration:
            errors.append(
                f"deployment #{i} ({name}/{params.get('model','?')}) 设了 max_budget 但没设 budget_duration，"
                "预算会永久累积不重置"
            )
    print("[3/9] 预算/限速字段健全性检查完成")

    # ── 4. 复杂度路由 hook 行为 ────────────────────────────────
    from gateway import custom_router_hook, llm_classifier
    hook = custom_router_hook.proxy_handler_instance

    # 这是纯离线配置体检：限时优先和原子额度预占已有独立单测，
    # 此处不应尝试连接 Redis。否则没启 Redis 时会遗留解析/连接任务，
    # 导致脚本即使打印“全部通过”，仍在 asyncio.run() 退出阶段等待。
    async def _no_optimal_channels():
        return []

    async def _choose_first_candidate(candidates):
        return next(iter(candidates), None)

    custom_router_hook.optimal_channels.list_optimal = _no_optimal_channels
    custom_router_hook.quota_manager.choose_and_reserve = _choose_first_candidate

    # v4 关键点：async_pre_call_hook 现在会调用 decide_pool_with_classifier()，
    # 而它在非"极短输入"/非"敏感内容"的情况下会真的去调用 llm_classifier.classify_task()
    # 发一次网络请求给 Groq。这个脚本的承诺是"不发真实请求，纯配置体检"，
    # 所以这里把分类器强制 mock 成"总是返回 None"（模拟分类器不可用），
    # 这样下面这组测试用例走的就是 decide_pool_with_classifier() 里
    # "分类器失败 → 回退到 decide_pool() 纯启发式规则"这条路径，
    # 跟 v3 时期的行为完全一致，不需要真实网络、也不需要改期望值。
    _original_classify_task = llm_classifier.classify_task

    async def _classifier_always_unavailable(text: str):
        return None

    llm_classifier.classify_task = _classifier_always_unavailable

    # v6 新增：hook 初始化时应该已经解析好 config.yaml，建好"限时优先"功能
    # 需要的渠道注册表；数量应该跟 fast/free/strong-pool 的 deployment 总数一致。
    registry_size = len(hook._channel_registry)
    expected_size = sum(model_groups.get(p, 0) for p in ("fast-pool", "free-pool", "strong-model-pool"))
    if registry_size != expected_size:
        errors.append(
            f"hook 的渠道注册表大小（{registry_size}）跟 fast/free/strong-pool 总数"
            f"（{expected_size}）对不上——'限时优先'功能可能找不到某些渠道"
        )
    else:
        print(f"     ✓ 渠道注册表加载正常（{registry_size} 个渠道，'限时优先'功能可以正常查找）")

    # v2 CRITICAL: 用 "acompletion" 测试，这才是真实代理路径传入的 call_type
    # v3: 更新为四层路由 (fast-pool / free-pool / strong-model-pool / trusted-pool)
    # v4: 以下用例全部跑在"分类器不可用"的 mock 模式下，验证的是启发式兜底规则
    test_cases = [
        # (描述, data, call_type, 期望目标池)
        ("超短问候→fast-pool", {"model": "auto-route", "messages": [{"role": "user", "content": "hi"}]}, "acompletion", "fast-pool"),
        ("短问题→fast-pool", {"model": "auto-route", "messages": [{"role": "user", "content": "1+1=?"}]}, "acompletion", "fast-pool"),
        # v4 修复：原来这里只重复 5 次（235 字符），其实低于 FAST_CHAR_THRESHOLD（600），
        # 从 fast-pool 阈值引入那天起这条用例就应该一直是误判成 fast-pool 而不是 free-pool，
        # 只是没人跑出来过。改成 15 次（约 700 字符）让它真正落在 free-pool 的区间里。
        ("中等长度→free-pool", {"model": "auto-route", "messages": [{"role": "user", "content": "请帮我写一个Python函数来计算斐波那契数列，要求支持递归和迭代两种方式，并解释时间复杂度。" * 15}]}, "acompletion", "free-pool"),
        ("关键词'重构'→strong", {"model": "auto-route", "messages": [{"role": "user", "content": "帮我重构这个项目"}]}, "acompletion", "strong-model-pool"),
        ("关键词'refactor'→strong", {"model": "auto-route", "messages": [{"role": "user", "content": "please refactor this module"}]}, "acompletion", "strong-model-pool"),
        ("超长文本→strong", {"model": "auto-route", "messages": [{"role": "user", "content": "x" * 35000}]}, "acompletion", "strong-model-pool"),
        ("多文件→strong", {"model": "auto-route", "messages": [{"role": "user", "content": "修改 a.py b.py c.py d.py e.py f.py"}]}, "acompletion", "strong-model-pool"),
        ("非补全调用→不改写", {"model": "auto-route", "messages": [{"role": "user", "content": "hi"}]}, "embedding", "auto-route"),
        ("客户端直指free-pool→不改写", {"model": "free-pool", "messages": [{"role": "user", "content": "重构整个项目"}]}, "acompletion", "free-pool"),
        ("客户端直指free-pool但含密钥→trusted", {"model": "free-pool", "messages": [{"role": "user", "content": "api_key=sk-abcdefghijklmnopqrstuvwxyz123456"}]}, "acompletion", "trusted-pool"),
        ("text completion prompt中的密钥→trusted", {"model": "auto-route", "prompt": "password=hunter2-secret"}, "atext_completion", "trusted-pool"),
        ("长文本末尾密钥→trusted", {"model": "auto-route", "messages": [{"role": "user", "content": "x" * 100001 + " sk-abcdefghijklmnopqrstuvwxyz123456"}]}, "acompletion", "trusted-pool"),
        ("结构化api_key字段→trusted", {"model": "auto-route", "messages": [{"role": "user", "content": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"}}]}, "acompletion", "trusted-pool"),
        # v3 新增：敏感内容检测必须覆盖复杂度升级规则
        ("重构关键词+AWS Key→trusted(覆盖strong)", {"model": "auto-route", "messages": [{"role": "user", "content": "帮我重构这段代码，用到了 AKIAIOSFODNN7EXAMPLE 这个 key"}]}, "acompletion", "trusted-pool"),
        ("数据库连接串→trusted-pool", {"model": "auto-route", "messages": [{"role": "user", "content": "帮我检查一下 postgres://admin:hunter2@10.0.0.5:5432/prod 这个配置对不对"}]}, "acompletion", "trusted-pool"),
        ("tool arguments 中的密钥→trusted-pool", {
            "model": "auto-route",
            "messages": [{"role": "assistant", "content": None, "tool_calls": [{
                "type": "function",
                "function": {"name": "deploy", "arguments": '{"token":"sk-abcdefghijklmnopqrstuvwxyz123456"}'},
            }]}],
        }, "acompletion", "trusted-pool"),
        # 注意：这句话很短（17字符），会被规则6分到fast-pool，这里主要验证的是
        # "提到'密码'这个词但没有真实赋值"不会被 _detect_sensitive 误报成 trusted-pool
        ("提及'密码'但无真实赋值→不应误报trusted", {"model": "auto-route", "messages": [{"role": "user", "content": "帮我写一段密码强度校验的正则表达式"}]}, "acompletion", "fast-pool"),
    ]

    for desc, data, call_type, expected in test_cases:
        import copy
        test_data = copy.deepcopy(data)
        # 模拟 LiteLLM 传入的参数（v2: 包含 cache 参数）
        result = await hook.async_pre_call_hook(
            user_api_key_dict=None,
            cache=None,
            data=test_data,
            call_type=call_type,
        )
        actual = result.get("model", "?")
        status = "✓" if actual == expected else "✗"
        if actual != expected:
            errors.append(f"hook 行为测试失败 [{desc}]: 期望 {expected}, 实际 {actual}")
        print(f"     {status} {desc}: model={actual}")

    print("[4/9] 复杂度路由 hook 行为检查完成（分类器 mock 为不可用，验证的是启发式兜底规则）")

    # ── 5. 分类器覆盖行为（用 mock 验证 decide_pool_with_classifier 的接线，
    #        不发真实网络请求）─────────────────────────────────
    _classifier_calls: list[str] = []

    async def _classifier_returns_strong(text: str):
        _classifier_calls.append(text)
        return "strong-model-pool"

    # 5a. 分类器可用且返回结果时，应该采信分类器的判断，而不是启发式规则。
    #     故意选一句启发式规则会判成 free-pool 的话，验证分类器的结果把它改判成了 strong-model-pool。
    #     注意：这句话必须长过 FAST_CHAR_THRESHOLD（600字符），否则会在"极短输入"那一步
    #     就直接被分流到 fast-pool，根本轮不到调用分类器（之前就因为这个漏测过一次）。
    llm_classifier.classify_task = _classifier_returns_strong
    override_text = "帮我看看这段业务逻辑写得怎么样，有没有可以简化的地方" * 30
    override_result = await hook.decide_pool_with_classifier(
        {"messages": [{"role": "user", "content": override_text}]}
    )
    if override_result != "strong-model-pool":
        errors.append(f"分类器结果应该被采信，期望 strong-model-pool，实际 {override_result}")
    elif len(_classifier_calls) != 1:
        errors.append(f"预期分类器被调用 1 次，实际 {len(_classifier_calls)} 次")
    else:
        print("     ✓ 分类器返回结果时，路由采信分类器判断（而不是启发式规则）")

    # 5b. 敏感内容检测必须在分类器之前短路——即使分类器被 mock 成能正常返回结果，
    #     命中敏感内容也不应该真的去调用分类器，应该直接给 trusted-pool。
    _classifier_calls.clear()
    sensitive_result = await hook.decide_pool_with_classifier(
        {"messages": [{"role": "user", "content": "这个 key AKIAIOSFODNN7EXAMPLE 要不要轮换一下"}]}
    )
    if sensitive_result != "trusted-pool":
        errors.append(f"敏感内容检测应该短路分类器直接给 trusted-pool，实际 {sensitive_result}")
    elif len(_classifier_calls) != 0:
        errors.append(f"敏感内容检测应该跳过分类器调用，实际分类器被调用了 {len(_classifier_calls)} 次")
    else:
        print("     ✓ 敏感内容检测正确短路了分类器调用")

    llm_classifier.classify_task = _classifier_always_unavailable  # 恢复成"不可用"供后面步骤使用
    print("[5/9] 分类器覆盖/短路行为检查完成")

    # ── 6. 多模态 content 容错 ────────────────────────────────
    multimodal_data = {
        "model": "auto-route",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "帮我重构这个项目"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ]}
        ],
    }
    try:
        result = await hook.async_pre_call_hook(
            user_api_key_dict=None, cache=None, data=multimodal_data, call_type="acompletion"
        )
        actual = result.get("model", "?")
        selected = next(
            (channel for channel in hook._channel_registry.values()
             if channel.get("direct_model_name") == actual),
            None,
        )
        if not selected or not selected.get("capabilities", {}).get("vision"):
            errors.append(f"多模态 content 测试失败: 应精确路由到支持 vision 的直连渠道, 实际 {actual}")
        print(f"     ✓ 多模态 content 按能力过滤后精确路由: model={actual}")
    except Exception as e:
        errors.append(f"多模态 content 测试异常: {type(e).__name__}: {e}")
        traceback.print_exc()
    print("[6/9] 多模态 content 容错检查完成")

    # ── 6. .env 弱密码检查 ────────────────────────────────────
    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as f:
            env_content = f.read()
        if "GATEWAY_MASTER_KEY=sk-dummy" in env_content or "GATEWAY_MASTER_KEY=dummy" in env_content:
            errors.append(".env 里的 GATEWAY_MASTER_KEY 还是 dummy 值，上线前必须换成随机强密码")
        elif "GATEWAY_MASTER_KEY=" in env_content:
            line = [
                env_line
                for env_line in env_content.splitlines()
                if env_line.startswith("GATEWAY_MASTER_KEY=")
            ][0]
            key = line.split("=", 1)[1].strip()
            if len(key) < 20:
                warnings.append(f"GATEWAY_MASTER_KEY 长度只有 {len(key)} 字符，建议至少 32 字符")
    print("[7/9] .env 弱密码检查完成")

    # ── 7. 强制非流式（客户端 stream=true 也应被关掉）──────────
    stream_data = {
        "model": "auto-route",
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hi"}],
    }
    try:
        result = await hook.async_pre_call_hook(
            user_api_key_dict=None, cache=None, data=stream_data, call_type="acompletion"
        )
        if result.get("stream") is not False:
            errors.append(f"应强制 stream=false，实际 stream={result.get('stream')!r}")
        if result.get("stream_options") is not None:
            errors.append("应清除 stream_options")
        # "hi" 是超短输入，通常路由到 fast-pool
        if result.get("model") not in {"fast-pool", "free-pool", "strong-model-pool", "elite-model-pool"}:
            errors.append(f"请求路由异常: model={result.get('model')}")
        print(f"     ✓ 强制非流式: stream={result.get('stream')}, model={result.get('model')}")
    except Exception as e:
        errors.append(f"非流式强制测试异常: {type(e).__name__}: {e}")
    print("[8/9] 强制非流式路径检查完成")

    # ── 8. 并发选择稳定性 ─────────────────────────────────────
    # 注意：这一步只验证"高并发下选择函数本身稳定不崩"，不验证 RPM 限制本身。
    # 使用独立的 model_name 避免和主 router 的全局缓存冲突。
    async def _try_select_deployment(test_router: Router) -> bool:
        try:
            dep = await test_router.async_get_available_deployment(
                model="concurrent-test-only",
                request_kwargs={},
                messages=[{"role": "user", "content": "hi"}],
            )
            return dep is not None
        except Exception:
            return False

    test_model_list = [{
        "model_name": "concurrent-test-only",
        "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "dummy", "rpm": 100},
    }]
    test_router = Router(
        model_list=test_model_list,
        routing_strategy="simple-shuffle",
        enable_pre_call_checks=False,  # 测试环境无 Redis，关闭预检查避免误拒
    )
    results = await asyncio.gather(*[_try_select_deployment(test_router) for _ in range(5)])
    succeeded = sum(1 for r in results if r)
    print(f"[9/9] 并发选择稳定性检查：5 个并发选择请求里有 {succeeded} 个正常返回（不代表验证了 RPM 限制本身）")
    if succeeded < 5:
        errors.append(
            f"并发选择测试出现 {5 - succeeded} 次异常/失败——这本身不该发生"
            f"（这一步只是选 deployment，不涉及真实网络调用），值得排查"
        )

    # ── hook 统计计数器 ────────────────────────────────────────
    stats = hook.get_stats()
    print(f"\n     hook 统计: {stats}")
    if stats["total_requests"] < 14:
        warnings.append(f"hook 统计 total_requests={stats['total_requests']}，预期至少 14（12 个测试用例 + 多模态 + 流式）")

    print()
    if warnings:
        print(f"⚠️  {len(warnings)} 个警告：")
        for w in warnings:
            print(f"   - {w}")
        print()
    if errors:
        print(f"❌ 发现 {len(errors)} 个问题：")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ 全部体检通过，可以接真实 key 上线了")
    print("   上线后第一件事：故意把某个免费渠道跑到限流，肉眼确认 fallback 真的生效。")

    # LiteLLM Router 的用量路由和预算策略会创建周期同步任务。Proxy 是
    # 长驻进程，所以上游没有 Router.close()；但体检脚本必须主动取消，
    # 否则 asyncio.run() 会在已经打印“全部通过”后一直等待这些循环。
    current_task = asyncio.current_task()
    litellm_tasks = []
    for pending_task in asyncio.all_tasks():
        if pending_task is current_task or pending_task.done():
            continue
        coroutine = pending_task.get_coro()
        filename = getattr(getattr(coroutine, "cr_code", None), "co_filename", "")
        if "/litellm/" in filename.replace("\\", "/"):
            litellm_tasks.append(pending_task)
            pending_task.cancel()
    if litellm_tasks:
        # 让取消在当前事件循环生效。不在此 gather：上游预算同步
        # 协程在某些版本中会在清理路径里等待下一个同步周期。
        await asyncio.sleep(0)

    # 保留引用到此处，防止 Router 在检查完成前被回收。
    del router, test_router
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
