from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gateway import quota_manager
from gateway.custom_router_hook import (
    ComplexityRouterHook,
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

        mark_failure.assert_awaited_once_with("ministral", "quality_error")

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

    def test_gemini_pro_uses_probe_circuit_breaker(self):
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
        mark_failure.assert_awaited_once_with("gemini-pro", "quota_probe")

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
