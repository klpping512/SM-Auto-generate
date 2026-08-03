"""Deterministic platform duration budgets for video timelines."""
from __future__ import annotations

from collections.abc import Iterable


PLATFORM_BUDGETS_MS = {
    "douyin": 60_000,
    "xiaohongshu": 45_000,
    "youtube": 60_000,
    "wechat": 90_000,
    "wechat_official": 90_000,
}
MIN_BUDGET_MS = 15_000
MAX_BUDGET_MS = 180_000
MIN_SCENE_MS = 3_000


def platform_budget_ms(platform: str | None, override_ms: int | None = None) -> int:
    """Return a validated project budget; unknown platforms use the short-video default."""
    value = override_ms if override_ms is not None else PLATFORM_BUDGETS_MS.get(
        str(platform or "douyin").casefold(), PLATFORM_BUDGETS_MS["douyin"]
    )
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("成片时长预算必须是整数毫秒") from exc
    if not MIN_BUDGET_MS <= value <= MAX_BUDGET_MS:
        raise ValueError("成片时长预算必须在 15–180 秒之间")
    return value


def fit_scenes_to_budget(scenes: Iterable[dict], target_duration_ms: int) -> list[dict]:
    """Keep scene order and trim only the final scene that crosses the budget."""
    target = platform_budget_ms("custom", target_duration_ms)
    remaining = target
    result: list[dict] = []
    for raw in scenes:
        if remaining <= 0:
            break
        item = dict(raw or {})
        try:
            requested = int(item.get("duration_ms") or round(float(item.get("duration") or 0) * 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError("分镜时长必须是数字") from exc
        if requested <= 0:
            continue
        actual = min(requested, remaining)
        item["duration_ms"] = actual
        item["duration"] = round(actual / 1000, 3)
        item["trimmed_to_budget"] = actual < requested
        result.append(item)
        remaining -= actual
    return result


def rebalance_scenes_to_budget(
    scenes: Iterable[dict],
    target_duration_ms: int,
    *,
    minimum_scene_ms: int = MIN_SCENE_MS,
) -> list[dict]:
    """Preserve every scene and proportionally shrink an over-budget timeline."""
    target = platform_budget_ms("custom", target_duration_ms)
    normalized: list[dict] = []
    requested: list[int] = []
    for raw in scenes:
        item = dict(raw or {})
        try:
            duration = int(item.get("duration_ms") or round(float(item.get("duration") or 0) * 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError("分镜时长必须是数字") from exc
        duration = max(minimum_scene_ms, duration)
        normalized.append(item)
        requested.append(duration)
    if not normalized:
        return []
    if len(normalized) * minimum_scene_ms > target:
        raise ValueError("分镜数量过多，无法在平台预算内保留每个完整镜头")

    total = sum(requested)
    allocated = list(requested)
    if total > target:
        remaining = target - len(normalized) * minimum_scene_ms
        flexible = [max(0, duration - minimum_scene_ms) for duration in requested]
        flexible_total = sum(flexible)
        allocated = [
            minimum_scene_ms + int(remaining * value / flexible_total)
            for value in flexible
        ]
        # Integer division can leave a few milliseconds; distribute them stably.
        for index in range(target - sum(allocated)):
            allocated[index % len(allocated)] += 1

    result = []
    for item, original, duration in zip(normalized, requested, allocated):
        item["duration_ms"] = duration
        item["duration"] = round(duration / 1000, 3)
        item["trimmed_to_budget"] = duration < original
        result.append(item)
    return result
