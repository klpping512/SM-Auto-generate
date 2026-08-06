"""小红书图文渲染前/后门禁：纯函数，禁止 LLM/网络调用。

语义红线：truth_guard.evaluate 的 uncovered 只作软警告（风险句须补证据），
绝不是把风险句当成「事实核对通过」。发布硬拦仍由 truth_guard.publish_error 负责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

import truth_guard
from xhs_cards import HEIGHT, WIDTH

# ---- 常量真源（prompt / 门禁 / 测试共用，禁止各写各的）----
XHS_PAGES_MIN = 5
XHS_PAGES_MAX = 7
XHS_TITLE_MAX = 20  # 笔记标题——软检查
XHS_COVER_HOOK_MIN = 3  # 封面钩子——硬检查
XHS_COVER_HOOK_MAX = 10
XHS_HEADLINE_MAX = 18  # 卡面 headline——硬检查
XHS_POINTS_MAX = 48  # 与 xhs_cards._clean 截断上限对齐
XHS_GATE_MAX_CALLS = 2

# 广告法精确词：禁用裸「最」子串，避免误伤「最近/最优/第一时间」
ADLAW_TERMS = [
    "国家级", "世界级", "全球第一", "全国第一", "全网第一", "销量第一", "行业第一",
    "世界领先", "全球领先", "行业领先", "顶级", "顶尖", "极致", "首选", "唯一",
    "100%", "百分之百", "绝对", "保证", "根治", "零风险", "包赚", "最低价", "永久免费",
]


@dataclass
class GateResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean_text(value) -> str:
    return str(value or "").strip()


def _pages_plain_text(pages: list[dict] | None) -> str:
    parts: list[str] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for key in ("headline", "subheadline"):
            text = _clean_text(page.get(key))
            if text:
                parts.append(text)
        for point in page.get("points") or []:
            text = _clean_text(point)
            if text:
                parts.append(text)
    return " ".join(parts)


def _scan_adlaw(blob: str) -> list[str]:
    hits = [term for term in ADLAW_TERMS if term and term in blob]
    return hits


def check_before_render(title: str, body: str, pages: list[dict] | None) -> GateResult:
    """渲染前门禁：结构/广告法 → errors；标题过长与风险句 → warnings。"""
    result = GateResult()
    page_list = [p for p in (pages or []) if isinstance(p, dict)]
    n = len(page_list)
    if n < XHS_PAGES_MIN or n > XHS_PAGES_MAX:
        result.errors.append(f"页数须为 {XHS_PAGES_MIN}-{XHS_PAGES_MAX}，当前 {n}")

    if page_list:
        if _clean_text(page_list[0].get("type")) != "cover":
            result.errors.append("首页 type 须为 cover")
        cover_hook = _clean_text(page_list[0].get("headline"))
        if not (XHS_COVER_HOOK_MIN <= len(cover_hook) <= XHS_COVER_HOOK_MAX):
            result.errors.append(
                f"封面钩子（第 1 页 headline）须 {XHS_COVER_HOOK_MIN}-{XHS_COVER_HOOK_MAX} 字，"
                f"当前 {len(cover_hook)} 字"
            )

    for index, page in enumerate(page_list):
        headline = _clean_text(page.get("headline"))
        page_type = _clean_text(page.get("type")) or ("cover" if index == 0 else "content")
        if not headline:
            result.errors.append(f"第 {index + 1} 页 headline 不能为空")
        elif len(headline) > XHS_HEADLINE_MAX:
            result.errors.append(
                f"第 {index + 1} 页 headline 超过 {XHS_HEADLINE_MAX} 字（当前 {len(headline)}）"
            )
        if page_type != "cover":
            points = [_clean_text(p) for p in (page.get("points") or []) if _clean_text(p)]
            if not (1 <= len(points) <= 4):
                result.errors.append(
                    f"第 {index + 1} 页（content）points 须 1-4 条，当前 {len(points)}"
                )
            for pi, point in enumerate(points):
                if len(point) > XHS_POINTS_MAX:
                    result.errors.append(
                        f"第 {index + 1} 页第 {pi + 1} 条 point 超过 {XHS_POINTS_MAX} 字"
                    )

    title_text = _clean_text(title)
    if len(title_text) > XHS_TITLE_MAX:
        result.warnings.append(
            f"笔记标题超过 {XHS_TITLE_MAX} 字（当前 {len(title_text)}），建议缩短以利搜索展示"
        )

    blob = f"{title_text}\n{_clean_text(body)}\n{_pages_plain_text(page_list)}"
    hits = _scan_adlaw(blob)
    if hits:
        result.errors.append(f"广告法绝对化用语命中：{', '.join(hits)}")

    # 软警告：风险句须补证据；不是「事实已核对正确」
    body_for_guard = f"{title_text}。{_clean_text(body)} {_pages_plain_text(page_list)}".strip()
    guard = truth_guard.evaluate(title_text, body_for_guard, None)
    for sentence in guard.get("uncovered") or []:
        result.warnings.append(
            f"风险表述，发布前须补证据或改条件式：{sentence}"
        )

    return result


def check_rendered(
    image_pages: list[dict] | None,
    attachments: list[dict] | None,
    static_dir: Path,
) -> list[str]:
    """渲染后完整性：附件齐、可解码、尺寸正确。返回 error 列表（空=通过）。"""
    errors: list[str] = []
    pages = image_pages or []
    atts = attachments or []
    if len(atts) != len(pages):
        errors.append(f"附件数 {len(atts)} 与页数 {len(pages)} 不一致")

    root = Path(static_dir)
    for att in atts:
        if not isinstance(att, dict):
            errors.append("附件项格式无效")
            continue
        rel = _clean_text(att.get("path"))
        if not rel:
            errors.append("附件缺少 path")
            continue
        path = Path(rel)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            errors.append(f"附件缺失: {rel}")
            continue
        try:
            with Image.open(path) as image:
                image.load()
                size = image.size
            if size != (WIDTH, HEIGHT):
                errors.append(
                    f"尺寸不符 {rel}: {size[0]}×{size[1]}，期望 {WIDTH}×{HEIGHT}"
                )
        except Exception as exc:
            errors.append(f"无法解码图片: {rel} ({exc})")
    return errors
