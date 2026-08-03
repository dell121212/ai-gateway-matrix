from __future__ import annotations

from gateway.routing_strategies import rank_candidates


CANDIDATES = [
    {"id": "cheap", "priority": 30, "remaining_percent": 25, "cost_per_million": 1.0, "latency_ms": 900, "success_rate": 0.99, "reset_seconds": 7200},
    {"id": "headroom", "priority": 20, "remaining_percent": 90, "cost_per_million": 4.0, "latency_ms": 300, "success_rate": 0.96, "reset_seconds": 3600},
    {"id": "reliable", "priority": 10, "remaining_percent": 50, "cost_per_million": 3.0, "latency_ms": 500, "success_rate": 0.999, "reset_seconds": 60},
]


def test_selected_mature_strategies_rank_deterministically() -> None:
    assert rank_candidates(CANDIDATES, "priority")[0]["candidate"]["id"] == "cheap"
    assert rank_candidates(CANDIDATES, "headroom")[0]["candidate"]["id"] == "headroom"
    assert rank_candidates(CANDIDATES, "cost-optimized")[0]["candidate"]["id"] == "cheap"
    assert rank_candidates(CANDIDATES, "reset-aware")[0]["candidate"]["id"] == "reliable"
    assert rank_candidates(CANDIDATES, "lkgp")[0]["candidate"]["id"] == "reliable"
    assert rank_candidates(CANDIDATES, "adaptive")[0]["candidate"]["id"] == "headroom"


def test_adaptive_strategy_balances_health_headroom_latency_and_cost() -> None:
    ranked = rank_candidates(CANDIDATES, "adaptive")

    assert ranked[0]["reason"] == "综合成功率、余量、延迟与成本自动选择"
    assert ranked[0]["score"] > ranked[-1]["score"]


def test_route_explanation_exposes_scores_without_secrets() -> None:
    ranked = rank_candidates(CANDIDATES, "headroom")
    assert ranked[0]["selected"] is True
    assert ranked[0]["reason"] == "剩余配额最高"
    assert all(set(row) == {"candidate", "score", "selected", "reason"} for row in ranked)
    assert all("api_key" not in row["candidate"] for row in ranked)
