"""Parse OpenAI-compatible SSE chunks without mutating the stream."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StreamAccumulator:
    content_chars: int = 0
    reasoning_chars: int = 0
    tool_arg_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: Optional[str] = None
    model: Optional[str] = None
    first_token_seen: bool = False
    usage_from_upstream: bool = False
    raw_usage: dict[str, Any] = field(default_factory=dict)

    def estimated_completion_tokens(self) -> int:
        if self.usage_from_upstream and self.completion_tokens:
            return self.completion_tokens
        # rough from chars
        total_chars = self.content_chars + self.reasoning_chars + self.tool_arg_chars
        return max(0, total_chars // 4)


def parse_sse_line(line: str, acc: StreamAccumulator) -> None:
    line = line.strip()
    if not line or line.startswith(":"):
        return
    if line.startswith("data:"):
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        _ingest_chunk(data, acc)


def _ingest_chunk(data: dict[str, Any], acc: StreamAccumulator) -> None:
    if not isinstance(data, dict):
        return
    if data.get("model"):
        acc.model = str(data["model"])
    usage = data.get("usage")
    if isinstance(usage, dict):
        acc.usage_from_upstream = True
        acc.raw_usage = usage
        acc.prompt_tokens = int(usage.get("prompt_tokens") or acc.prompt_tokens or 0)
        acc.completion_tokens = int(usage.get("completion_tokens") or acc.completion_tokens or 0)
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            acc.cached_tokens = int(details.get("cached_tokens") or acc.cached_tokens or 0)
        cdetails = usage.get("completion_tokens_details") or {}
        if isinstance(cdetails, dict):
            acc.reasoning_tokens = int(
                cdetails.get("reasoning_tokens") or acc.reasoning_tokens or 0
            )

    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if choice.get("finish_reason"):
            acc.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") or choice.get("message") or {}
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if content:
            acc.content_chars += len(str(content))
            acc.first_token_seen = True
        for key in ("reasoning_content", "reasoning"):
            rc = delta.get(key)
            if rc:
                acc.reasoning_chars += len(str(rc))
                acc.first_token_seen = True
        tool_calls = delta.get("tool_calls") or []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if args:
                acc.tool_arg_chars += len(str(args))
                acc.first_token_seen = True


def parse_sse_buffer(buffer: str, acc: StreamAccumulator) -> None:
    for line in buffer.splitlines():
        parse_sse_line(line, acc)
