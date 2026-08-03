"""Deterministic, explainable ranking policies for provider candidates."""

from __future__ import annotations

from typing import Any, Iterable


STRATEGIES = (
    "adaptive",
    "priority",
    "fill-first",
    "headroom",
    "reset-aware",
    "lkgp",
    "p2c",
    "cost-optimized",
)


def _number(candidate: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(candidate.get(key, default))
    except (TypeError, ValueError):
        return default


def _score(candidate: dict[str, Any], strategy: str) -> tuple[float, str]:
    if strategy == "adaptive":
        success = min(1.0, max(0.0, _number(candidate, "success_rate", 1.0)))
        headroom = min(100.0, max(0.0, _number(candidate, "remaining_percent", 100.0)))
        latency = min(5_000.0, max(0.0, _number(candidate, "latency_ms", 0.0)))
        cost = _number(candidate, "cost_per_million", 0.0)
        cost_penalty = min(20.0, max(0.0, cost)) * 5 if cost != float("inf") else 0.0
        score = (
            success * 500
            + headroom * 2
            + _number(candidate, "priority") * 1.5
            - latency / 20
            - cost_penalty
        )
        return score, "综合成功率、余量、延迟与成本自动选择"
    if strategy in {"priority", "fill-first"}:
        return _number(candidate, "priority"), "人工优先级最高"
    if strategy == "headroom":
        return _number(candidate, "remaining_percent"), "剩余配额最高"
    if strategy == "reset-aware":
        return -_number(candidate, "reset_seconds", float("inf")), "配额重置最接近"
    if strategy == "lkgp":
        return _number(candidate, "success_rate"), "近期成功率最高"
    if strategy == "p2c":
        reliability = _number(candidate, "success_rate") * 1000
        latency = _number(candidate, "latency_ms", 1_000_000)
        return reliability - latency / 10, "成功率与延迟综合最优"
    if strategy == "cost-optimized":
        return -_number(candidate, "cost_per_million", float("inf")), "单位 Token 成本最低"
    raise ValueError(f"unknown routing strategy: {strategy}")


def rank_candidates(candidates: Iterable[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown routing strategy: {strategy}")
    scored = []
    for candidate in candidates:
        safe = {key: value for key, value in candidate.items() if key not in {"api_key", "authorization", "secret"}}
        score, reason = _score(safe, strategy)
        scored.append({"candidate": safe, "score": score, "selected": False, "reason": reason})
    scored.sort(key=lambda row: (-row["score"], str(row["candidate"].get("id") or "")))
    if scored:
        scored[0]["selected"] = True
    return scored
