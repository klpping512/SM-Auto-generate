"""第 1 批：小红书渲染前/后门禁与标题三层常量。"""
from pathlib import Path

from PIL import Image

import xhs_quality_gate as gate
from xhs_cards import HEIGHT, WIDTH, render_carousel


def _ok_pages():
    return [
        {"type": "cover", "headline": "清关避坑", "subheadline": "建议收藏", "points": []},
        {"type": "content", "headline": "确认节点", "points": ["核对船期与港口状态", "提前同步客户预期"]},
        {"type": "content", "headline": "准备资料", "points": ["单证预审再发运", "留好沟通记录"]},
        {"type": "content", "headline": "费用边界", "points": ["以实际单据为准", "条件式表达时效"]},
        {"type": "content", "headline": "行动清单", "points": ["明确负责人", "必要时咨询专业服务"]},
    ]


def test_constants_are_single_source():
    assert gate.XHS_PAGES_MIN == 5
    assert gate.XHS_PAGES_MAX == 7
    assert gate.XHS_TITLE_MAX == 20
    assert gate.XHS_COVER_HOOK_MIN == 3
    assert gate.XHS_COVER_HOOK_MAX == 10
    assert gate.XHS_HEADLINE_MAX == 18
    assert gate.XHS_POINTS_MAX == 48
    assert gate.XHS_GATE_MAX_CALLS == 2
    assert "最" not in gate.ADLAW_TERMS  # 禁止裸「最」


def test_structure_ok_passes():
    result = gate.check_before_render("南非清关怎么做", "条件式说明节点。", _ok_pages())
    assert result.errors == []


def test_structure_page_count_and_cover():
    pages = _ok_pages()[:4]
    result = gate.check_before_render("标题标题标题", "正文", pages)
    assert any("页数" in e for e in result.errors)

    pages = _ok_pages()
    pages[0]["type"] = "content"
    result = gate.check_before_render("标题标题标题", "正文", pages)
    assert any("cover" in e for e in result.errors)


def test_cover_hook_and_headline_bounds():
    pages = _ok_pages()
    pages[0]["headline"] = "啊"  # 1 字
    assert any("封面钩子" in e for e in gate.check_before_render("题", "体", pages).errors)

    pages[0]["headline"] = "一二三四五六七八九十甲"  # 11
    assert len(pages[0]["headline"]) == 11
    assert any("封面钩子" in e for e in gate.check_before_render("题", "体", pages).errors)

    pages[0]["headline"] = "清关避坑"  # 4，通过钩子
    pages[1]["headline"] = "一二三四五六七八九十一二三四五六七八九"  # 19
    assert len(pages[1]["headline"]) == 19
    assert any("headline" in e for e in gate.check_before_render("题", "体", pages).errors)

    pages[1]["headline"] = "一二三四五六七八九十一二三四五六七八"  # 18
    assert len(pages[1]["headline"]) == 18
    errors = gate.check_before_render("题", "体", pages).errors
    assert not any("第 2 页 headline" in e for e in errors)


def test_content_points_bounds():
    pages = _ok_pages()
    pages[1]["points"] = []
    assert any("points" in e for e in gate.check_before_render("题", "体", pages).errors)

    pages[1]["points"] = ["x" * 49]
    assert any(f"{gate.XHS_POINTS_MAX}" in e for e in gate.check_before_render("题", "体", pages).errors)

    pages[1]["points"] = ["x" * 48]
    assert not any("point" in e and "超过" in e for e in gate.check_before_render("题", "体", pages).errors)


def test_adlaw_hits_and_no_false_positive_on_zui():
    pages = _ok_pages()
    hit = gate.check_before_render("国家级物流方案", "我们保证时效", pages)
    assert any("广告法" in e for e in hit.errors)
    assert any("国家级" in e or "保证" in e for e in hit.errors)

    hit2 = gate.check_before_render("清关指南", "100% 放行承诺", pages)
    assert any("100%" in e for e in hit2.errors)

    safe = gate.check_before_render(
        "最近物流观察",
        "第一时间核对港口动态，选最优缓冲方案。",
        pages,
    )
    assert not any("广告法" in e for e in safe.errors)


def test_truth_guard_is_warning_not_error():
    pages = _ok_pages()
    risky = gate.check_before_render(
        "德班港动态",
        "据报道德班港今日拥堵，清关延误 3 天。",
        pages,
    )
    assert risky.errors == [] or not any("风险表述" in e for e in risky.errors)
    assert any("风险表述" in w for w in risky.warnings)

    calm = gate.check_before_render("清关避坑框架", "先核对节点与责任边界，再决定方案。", pages)
    assert not any("风险表述" in w for w in calm.warnings)


def test_title_soft_warning():
    pages = _ok_pages()
    long_title = "这是一个超过二十个字的小红书笔记标题示例X"
    assert len(long_title) == 21
    result = gate.check_before_render(long_title, "正文", pages)
    assert any("笔记标题" in w for w in result.warnings)
    assert not any("笔记标题" in e for e in result.errors)


def test_check_rendered_missing_and_bad_size(tmp_path: Path):
    pages = [{"type": "cover"}, {"type": "content"}]
    missing = gate.check_rendered(
        pages,
        [{"path": "uploads/image/nope.png"}, {"path": "uploads/image/nope2.png"}],
        tmp_path,
    )
    assert any("附件缺失" in e for e in missing)

    bad = tmp_path / "uploads" / "image" / "bad.png"
    bad.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), (255, 0, 0)).save(bad)
    size_err = gate.check_rendered(
        [{"type": "cover"}],
        [{"path": "uploads/image/bad.png"}],
        tmp_path,
    )
    assert any("尺寸不符" in e for e in size_err)


def test_check_rendered_ok_after_real_render(tmp_path: Path):
    pages, attachments = render_carousel(
        "南非物流提醒",
        [
            {"type": "cover", "headline": "清关避坑", "subheadline": "建议收藏"},
            {"type": "content", "headline": "确认最新动态", "points": ["核对船期", "提前同步客户"]},
        ],
        tmp_path,
    )
    assert gate.check_rendered(pages, attachments, tmp_path) == []
    # 尺寸真源来自 xhs_cards
    first = tmp_path / attachments[0]["path"]
    with Image.open(first) as image:
        assert image.size == (WIDTH, HEIGHT)


def test_semantic_red_line_no_fact_verified_claim():
    """验收清单第 7：禁止宣称过 truth_guard = 事实已核对通过。"""
    src = Path(gate.__file__).read_text(encoding="utf-8")
    assert "事实已校验" not in src
    assert "已校验" not in src
