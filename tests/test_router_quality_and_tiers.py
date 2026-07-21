from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import litellm

from gateway import quota_manager
from gateway.custom_router_hook import (
    ComplexityRouterHook,
    ELITE_POOL,
    FAST_POOL,
    FREE_POOL,
    STRONG_POOL,
)


def _bare_hook() -> ComplexityRouterHook:
    hook = object.__new__(ComplexityRouterHook)
    hook._channel_registry = {}
    hook._provider_registry = None
    hook._registry_config_mtime_ns = None
    return hook


class FakeCooldownRedis:
    def __init__(self):
        self.ttls = {}
        self.values = {}

    async def ttl(self, key):
        return self.ttls.get(key, -2)

    async def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex


class RouterQualityAndTierTests(unittest.TestCase):
    def test_all_cooled_elite_channels_fall_back_to_healthy_strong_pool(self):
        hook = _bare_hook()
        elite = {"display_id": "elite-a"}
        strong = {"display_id": "strong-a"}
        hook._provider_registry = SimpleNamespace(
            candidates=lambda pool, _requirements: {
                ELITE_POOL: [elite],
                STRONG_POOL: [strong],
            }.get(pool, [])
        )
        hook._is_configured = lambda _channel: True

        async def cooldown(display_id):
            return 120 if display_id == "elite-a" else 0

        with patch(
            "gateway.custom_router_hook.quota_manager.cooldown_remaining",
            side_effect=cooldown,
        ):
            selected = asyncio.run(
                hook._resolve_capability_target(ELITE_POOL, {"text"})
            )

        self.assertEqual(selected, STRONG_POOL)

    def test_deployment_success_hook_records_actual_response_usage(self):
        hook = _bare_hook()
        request = {
            "model": "openai/test-model",
            "api_base": "https://example.test/v1",
            "messages": [{"role": "user", "content": "测试"}],
        }
        response = SimpleNamespace(
            id="response-1",
            usage=SimpleNamespace(prompt_tokens=6, completion_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(content="通过"))],
        )

        with patch(
            "gateway.custom_router_hook.usage_tracker.record_call", AsyncMock(),
        ) as record_call:
            result = asyncio.run(
                hook.async_post_call_success_deployment_hook(
                    request, response, "acompletion",
                )
            )

        self.assertIs(result, response)
        record_call.assert_awaited_once()
        kwargs = record_call.await_args.kwargs
        self.assertEqual(kwargs["prompt_tokens"], 6)
        self.assertEqual(kwargs["completion_tokens"], 2)
        self.assertEqual(kwargs["event_id"], "response-1")

    def test_short_tasks_use_intent_not_length_alone(self):
        hook = _bare_hook()

        self.assertEqual(hook.decide_pool({"messages": [{"role": "user", "content": "2+3*4"}]}), FAST_POOL)
        self.assertEqual(hook.decide_pool({"messages": [{"role": "user", "content": "帮我写一封请假邮件"}]}), FREE_POOL)
        self.assertEqual(hook.decide_pool({"messages": [{"role": "user", "content": "解释量子纠缠"}]}), FREE_POOL)
        self.assertEqual(hook.decide_pool({
            "messages": [{"role": "user", "content": "分析 Python 异步服务为什么会死锁并给修复建议"}]
        }), STRONG_POOL)

    def test_classifier_cannot_downgrade_below_rule_floor(self):
        hook = _bare_hook()

        floor = hook._minimum_pool_for_text("请用 Python 写一个排序函数")
        self.assertEqual(floor, FREE_POOL)
        self.assertEqual(hook._higher_pool(FAST_POOL, floor), FREE_POOL)
        self.assertEqual(hook._higher_pool(STRONG_POOL, floor), STRONG_POOL)

    def test_quality_detector_catches_bad_output(self):
        detect = ComplexityRouterHook._quality_failure_reason

        self.assertEqual(detect("正常问题", "<|im_start|>assistant\n答案"), "chat_template_echo")
        self.assertEqual(detect("正常问题", "错误字符�"), "mojibake")
        self.assertEqual(
            detect("你好", '{"error":{"message":"Rate limit exceeded","type":"rate_limit_error"}}'),
            "upstream_error_json",
        )
        self.assertEqual(
            detect("你好", "Error: model is unavailable, try again later"),
            "upstream_error_text",
        )
        self.assertEqual(detect("x", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), "low_entropy")
        self.assertEqual(
            detect("小明有12个苹果，送出5个，又买了8个，现在有几个？", "现在有 8 个"),
            "arithmetic_mismatch",
        )
        self.assertEqual(
            detect(
                "2+3*4等于多少？只回答结果。",
                '5 kukushirotation_fa Error: input "*3*4" not not valid',
            ),
            "arithmetic_mismatch",
        )
        self.assertIsNone(
            detect("小明有12个苹果，送出5个，又买了8个，现在有几个？", "现在有 15 个")
        )
        rating_prompt = (
            "请给小说评分。评分维度每项 0-10 分，满分 10 分；"
            "严格按 JSON 格式输出结果，不要添加其他内容。"
        )
        self.assertIsNone(detect(rating_prompt, '{"plot": 8.2, "total": 8.0}'))
        prompt = "请分析这个异步服务为什么会发生死锁并给出修复建议"
        self.assertEqual(detect(prompt, prompt + "，下面开始分析"), "prompt_echo")
        self.assertIsNone(detect("2+2", "4"))

    def test_siliconflow_qwen_bad_output_becomes_quality_failure(self):
        hook = _bare_hook()
        hook._extract_display_id_from_request = lambda _data: "siliconflow-qwen"
        request = {
            "model": "openai/Qwen/Qwen2.5-7B-Instruct",
            "api_base": "https://api.siliconflow.cn/v1",
            "messages": [{"role": "user", "content": "请说明原因"}],
        }
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="<|im_start|>assistant\n复述"))]
        )

        with patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            with self.assertRaisesRegex(RuntimeError, "response_quality_error"):
                asyncio.run(hook.async_post_call_success_deployment_hook(request, response, "acompletion"))

        mark_failure.assert_awaited_once_with("siliconflow-qwen", "quality_error")

    def test_empty_plain_text_response_is_retried_for_any_model(self):
        hook = _bare_hook()
        hook._extract_display_id_from_request = lambda _data: "glm-flash"
        request = {
            "model": "openai/glm-4.7-flash",
            "messages": [{"role": "user", "content": "写一封请假邮件"}],
        }
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
        )

        with patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            with self.assertRaisesRegex(RuntimeError, "response_quality_error:empty_output"):
                asyncio.run(
                    hook.async_post_call_success_deployment_hook(
                        request, response, "acompletion"
                    )
                )

        mark_failure.assert_awaited_once_with("glm-flash", "quality_error")

    def test_wrong_arithmetic_is_retried_for_any_model(self):
        hook = _bare_hook()
        hook._extract_display_id_from_request = lambda _data: "ministral"
        request = {
            "model": "mistral/ministral-8b-latest",
            "messages": [{"role": "user", "content": "2+3*4等于多少？只回答结果。"}],
        }
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="26"))]
        )

        with patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            with self.assertRaisesRegex(
                RuntimeError, "response_quality_error:arithmetic_mismatch"
            ):
                asyncio.run(
                    hook.async_post_call_success_deployment_hook(
                        request, response, "acompletion"
                    )
                )

        mark_failure.assert_not_awaited()

    def test_custom_cooldown_is_a_retryable_rate_limit_not_internal_500(self):
        hook = _bare_hook()
        hook._ensure_channel_registry_fresh = lambda: None
        hook._extract_display_id_from_request = lambda _data: "mistral-large"
        request = {"model": "mistral/mistral-large-latest"}

        with patch(
            "gateway.custom_router_hook.quota_manager.cooldown_remaining",
            AsyncMock(return_value=30),
        ):
            with self.assertRaises(litellm.RateLimitError) as raised:
                asyncio.run(
                    hook.async_pre_call_deployment_hook(request, "acompletion")
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("channel_cooldown_active", str(raised.exception))

    def test_gemini_35_flash_gets_safe_generation_defaults(self):
        hook = _bare_hook()
        hook._ensure_channel_registry_fresh = lambda: None
        hook._extract_display_id_from_request = lambda _data: None
        request = {
            "model": "gemini/gemini-3.5-flash",
            "temperature": 0.1,
            "max_tokens": 90,
        }

        result = asyncio.run(hook.async_pre_call_deployment_hook(request, "acompletion"))

        self.assertNotIn("temperature", result)
        self.assertEqual(result["reasoning_effort"], "minimal")
        self.assertEqual(result["max_tokens"], 512)

    def test_removed_gemini_pro_has_no_special_probe_circuit_breaker(self):
        hook = _bare_hook()
        hook._ensure_channel_registry_fresh = lambda: None
        hook._extract_display_id_from_request = lambda _data: "gemini-pro"
        request = {"model": "gemini/gemini-2.5-pro"}

        with patch(
            "gateway.custom_router_hook.quota_manager.cooldown_remaining",
            AsyncMock(return_value=0),
        ), patch(
            "gateway.custom_router_hook.quota_manager.mark_failure",
            AsyncMock(),
        ) as mark_failure:
            result = asyncio.run(hook.async_pre_call_deployment_hook(request, "acompletion"))

        self.assertIs(result, request)
        mark_failure.assert_not_awaited()

    def test_config_enforces_requested_provider_tiers(self):
        import yaml
        from pathlib import Path

        config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
        gemini = []
        restricted = []
        for item in config["model_list"]:
            pool = item.get("model_name")
            params = item.get("litellm_params") or {}
            model = str(params.get("model") or "")
            base = str(params.get("api_base") or "")
            if "gemini-2.5-pro" in model or "gemini-3.5-flash" in model:
                gemini.append((pool, model))
            if pool in {FAST_POOL, FREE_POOL, STRONG_POOL, ELITE_POOL, "trusted-pool"} and (
                model.startswith(("groq/", "sambanova/")) or "siliconflow.cn" in base
            ):
                restricted.append((pool, model))

        self.assertNotIn("gemini/gemini-2.5-pro", [model for _, model in gemini])
        self.assertIn((STRONG_POOL, "gemini/gemini-3.5-flash"), gemini)
        self.assertTrue(restricted)
        self.assertTrue(all(pool == FAST_POOL for pool, _ in restricted))
        # 闭环：router 层负责同池换 peer（质检失败 / 429）；litellm 层不再叠重试
        self.assertGreaterEqual(int(config["router_settings"]["num_retries"]), 2)
        self.assertEqual(int(config["litellm_settings"]["num_retries"]), 0)
        self.assertIn(
            "gateway.custom_router_hook.proxy_handler_instance",
            config["litellm_settings"]["callbacks"],
        )

    def test_display_id_falls_back_to_unique_model_match(self):
        hook = _bare_hook()
        hook._channel_registry = {
            "gemini-pro": {
                "display_id": "gemini-pro",
                "model": "gemini/gemini-2.5-pro",
                "api_base": None,
            }
        }

        resolved = hook._extract_display_id({
            "litellm_params": {
                "model": "gemini-2.5-pro",
                "api_base": "https://generativelanguage.googleapis.com",
            }
        })

        self.assertEqual(resolved, "gemini-pro")

    def test_direct_model_name_resolves_to_display_id(self):
        hook = _bare_hook()
        hook._channel_registry = {
            "gemini-flash": {
                "display_id": "gemini-flash",
                "model": "gemini/gemini-3.5-flash",
                "api_base": None,
                "direct_model_name": "direct-gemini-flash",
            }
        }

        self.assertEqual(
            hook._extract_display_id_from_request({"model": "direct-gemini-flash"}),
            "gemini-flash",
        )

    def test_final_direct_quota_failure_writes_cooldown(self):
        hook = _bare_hook()
        hook._stats = {"errors": 0}
        hook._channel_registry = {
            "gemini-flash": {
                "display_id": "gemini-flash",
                "model": "gemini/gemini-3.5-flash",
                "api_base": None,
                "direct_model_name": "direct-gemini-flash",
            }
        }

        with patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            asyncio.run(
                hook.async_post_call_failure_hook(
                    {"model": "direct-gemini-flash"},
                    RuntimeError("429 exceeded your current quota"),
                    None,
                )
            )

        mark_failure.assert_awaited_once_with("gemini-flash", "quota_error")

    def test_pooled_quota_failure_uses_exception_model_and_shared_credential(self):
        hook = _bare_hook()
        hook._stats = {"errors": 0}
        hook._channel_registry = {
            "cerebras-a": {
                "display_id": "cerebras-a",
                "model": "cerebras/gpt-oss-120b",
                "api_base": None,
                "env_var": "CEREBRAS_API_KEY",
            },
            "cerebras-b": {
                "display_id": "cerebras-b",
                "model": "cerebras/zai-glm-4.7",
                "api_base": None,
                "env_var": "CEREBRAS_API_KEY",
            },
        }
        error = RuntimeError("Payment required to access this resource")
        error.model = "cerebras/gpt-oss-120b"
        error.llm_provider = "cerebras"

        with patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            asyncio.run(
                hook.async_post_call_failure_hook(
                    {"model": "elite-model-pool"}, error, None,
                )
            )

        self.assertEqual(mark_failure.await_count, 2)
        mark_failure.assert_any_await("cerebras-a", "quota_error")
        mark_failure.assert_any_await("cerebras-b", "quota_error")

    def test_quota_zero_is_classified_before_generic_429(self):
        hook = _bare_hook()

        self.assertEqual(hook._classify_error("429 quota exceeded; limit: 0"), "quota_zero")
        self.assertEqual(hook._classify_error("429 exceeded your current quota"), "quota_error")
        self.assertEqual(hook._classify_error("429 too many requests"), "rate_limit")

    def test_top_level_failure_kwargs_write_gemini_quota_cooldown(self):
        hook = _bare_hook()
        hook._channel_registry = {
            "gemini-flash": {
                "display_id": "gemini-flash",
                "model": "gemini/gemini-3.5-flash",
                "api_base": None,
                "env_var": "GEMINI_API_KEY",
            }
        }
        kwargs = {
            "model": "gemini/gemini-3.5-flash",
            "exception": RuntimeError("429 exceeded your current quota"),
        }

        with patch(
            "gateway.custom_router_hook.usage_tracker.record_call", AsyncMock()
        ), patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            asyncio.run(hook.async_log_failure_event(kwargs, None, None, None))

        mark_failure.assert_awaited_once_with("gemini-flash", "quota_error")

    def test_internal_cooldown_signal_does_not_extend_itself(self):
        hook = _bare_hook()
        hook._channel_registry = {
            "gemini-flash": {
                "display_id": "gemini-flash",
                "model": "gemini/gemini-3.5-flash",
                "api_base": None,
                "env_var": "GEMINI_API_KEY",
            }
        }
        kwargs = {
            "model": "gemini/gemini-3.5-flash",
            "exception": RuntimeError("channel_cooldown_active"),
        }

        with patch(
            "gateway.custom_router_hook.usage_tracker.record_call", AsyncMock()
        ), patch(
            "gateway.custom_router_hook.quota_manager.mark_failure", AsyncMock()
        ) as mark_failure:
            asyncio.run(hook.async_log_failure_event(kwargs, None, None, None))

        mark_failure.assert_not_awaited()

    def test_long_cooldown_is_not_shortened(self):
        fake = FakeCooldownRedis()
        with patch("gateway.quota_manager.usage_tracker.get_client", return_value=fake):
            asyncio.run(quota_manager.mark_failure("gemini-pro", "quota_zero"))
            asyncio.run(quota_manager.mark_failure("gemini-pro", "rate_limit"))

        key = next(iter(fake.ttls))
        self.assertEqual(fake.ttls[key], 86400)
        self.assertEqual(fake.values[key], "quota_zero")


if __name__ == "__main__":
    unittest.main()
