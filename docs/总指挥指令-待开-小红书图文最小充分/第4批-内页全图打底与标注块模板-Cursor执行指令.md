# 第 4 批：内页全图打底 + 标注块（模板批）—— Cursor 执行指令

> 日期：2026-08-07
> 状态：**已验收**（执行提交 `a8b4293`；总指挥对抗审阅通过，harness 24/24 + 铁律全绿，备注见 `README.md` 第 4 批小节）。
> 设计真源：总指挥现场反馈「不只是图一有图片素材，图2到图5都要有图片素材，看着没那么乏味、也比较专业些」；参考 `小红书实例图/` 官方账号成品；版式方向经 AskUserQuestion 确认「全图打底+标注块」，并追加定式「第一张内页标注块在下面，然后依次上-下-上交替」。
> 拍板：总指挥放行——本批是**显式模板批**，允许改 `TEMPLATE_VERSION`（v4→v5）。

## 零、前置依赖

| 子项 | 依赖 | 状态 |
|---|---|---|
| 内页照片供给 | 批次 2 `xhs_photo_match.pick_photos` + `render_carousel(photo_pool=...)`（每页已解出照片，`pool[index % len(pool)]`） | ✅ 现网已就绪 |
| 全幅照片处理 | `_photo_panel(source, size)`（ImageOps.fit 填满 + 色彩/对比度增强） | ✅ 可复用 |
| 品牌角标 | `_brand()`（白色 Logo + 金色页码胶囊）+ `_dots()`（底部圆点） | ✅ 可复用 |

> 根因结论：配图素材**已按页喂到渲染层**，缺的是绘制层——`_draw_photo_page` 目前是 stub（直接返回 `_draw_text_page`），`_render_page` 也只把偶数页路由给它。**本批纯改渲染层 + 测试，不碰素材调度。**

## 一、背景与 Why

总指挥看过 MVP 出图与官方账号成品后指出：现有轮播内页（图 2-5）几乎无图，观感乏味、不够专业。官方账号的成品是**实拍照片 + 标注**风格：每页都有品牌照片打底，文字以标注块的形式叠在照片上，信息与画面并存。

现网能力盘点：
- 素材库 139 张已分类品牌照片（仓库 123 / 配送 9 / 品牌 2 / 设施 2 / 员工 3），`pick_photos` 已按话题分类命中，`render_carousel` 已把照片逐个解析给**每一页**。
- 但 `_render_page` 把内页奇数页路由到 `_draw_text_page`（纯渐变无图）、偶数页路由到 `_draw_photo_page`（stub = 又回文本页）。**照片到了绘制层却被丢掉了。**

本批目标：实现 `_draw_photo_page`，让图 2-7 全部成为「全图打底 + 标注块」页；标注块位置按页交替（图2 下、图3 上、图4 下、图5 上…），形成照片在上/在下的节奏；封面（图1）保持现状不动。

## 二、铁律（不做的事）

1. **本批是显式模板批**：`TEMPLATE_VERSION` v4→v5 在本批范围内（总指挥已放行）。**除此之外**不顺手改任何其他模板样式/封面/文本页版式。
2. **只动渲染层与测试**：`xhs_cards.py`、`tests/test_xhs_cards.py`、`tests/test_upload_api.py`（仅版本断言）。**不碰** `xhs_photo_match.py` / `xhs_diff_guard.py` / `xhs_quality_gate.py` / `xhs_ledger` / scheduler / app.py 发布与生成路径 / 任何数据库 schema。
3. **无照片兜底不变**：`photo is None` 时内页回退 `_draw_text_page`（现状行为），不新增空照片占位、不报错。
4. **单图缺失只影响该页**：沿用 `_photo_panel` 的 `source.exists()` 防御，缺图即用渐变兜底，**不抛异常、不中断整批渲染**。
5. **不引入新依赖**、不开新文件、不新增系统级样式常量表。
6. 附件元数据 `template_version` 取自常量，自动随 v4→v5 更新，**无需手工逐条改**。

## 三、改动清单

### 改动 A：`xhs_cards.py` 实现 `_draw_photo_page`（内页全图打底 + 标注块）

**版本号**：L12 `TEMPLATE_VERSION = "buffalo-reference-v4"` → `"buffalo-reference-v5"`。

**路由**（`_render_page`，L291-300）：内页一律优先照片页，无照片回退文本页：

```python
def _render_page(page: dict, index: int, total: int, output: Path, photo: Path | None = None):
    if index == 0:
        image = _draw_cover(page, total, photo)
    elif photo is not None:
        image = _draw_photo_page(page, index, total, photo)
    else:
        image = _draw_text_page(page, index, total)
```

**`_draw_photo_page` 版式定式**：

1. **全幅照片打底**：`base = _photo_panel(photo, (WIDTH, HEIGHT))`（ImageOps.fit 填满 1242×1660，已有色彩 0.86 / 对比 1.08 增强）。
2. **标注块位置按页交替**：`position = "bottom" if index % 2 == 1 else "top"`。即图2(下标1)/图4(下标3)/图6(下标5) 标注块在**下**，图3(下标2)/图5(下标4)/图7(下标6) 标注块在**上**。标注块对侧为照片纯露出区。
3. **可读性遮罩**：标注块所在半区叠半透明暖棕渐变（参考 `_gradient` 同色系，约 45-50% 高度），保证白色文字在照片上可读；对侧保持照片原样（可加极轻暗角，不能压过照片观感）。
4. **标注块内容（自顶向下）**：
   - `_brand()`：白色 Logo 居右 + 金色页码胶囊（沿用现有调用）。
   - 大标题：`page["headline"]`，加粗 52-56px，自动换行 ≤2 行，白色 + 深棕描边（沿用文本页 `stroke_fill="#704425"` 同款保证对比）。
   - 要点列表：`page.get("points")` 至多 4 条，每条圆形数字徽章（`01`/`02`…，沿用文本页徽章样式）+ 要点文本（加粗，≤2 行换行）；每条之间留呼吸间距。要点不足 4 条时按实际条数排布，**不补默认文案**。
   - 底部一行运营固定句（可选，若块高允许）：「跨境物流，认准 BUFFALO」白色小字。
5. **`_dots()` 保留**：页码圆点照旧画在 1605 附近（标注块在下时画在块内/块沿，在上时画在照片区，均保持白色可见）。
6. 标注块建议用全宽圆角矩形或大椭圆落边（沿用封面 `odraw.ellipse` 落边手法），**不与封面/文本页抢观感**，整体暖棕（GOLD 系）。

### 改动 B：测试

**版本断言同步**（两处）：
- `tests/test_xhs_cards.py` L44：`attachments[0]["template_version"] == "buffalo-reference-v5"`。
- `tests/test_upload_api.py` L99：`all(item["template_version"] == "buffalo-reference-v5" for item in content["attachments"])`。

**新增用例（`tests/test_xhs_cards.py`）**：

1. **内页交替位置**：构造 photo_pool 为 5 张不同纯色图（或 5 张不同色块图），渲染 5 页；对图2/图4 断言标注块在下方（上区为照片色、下区为标注块色系），图3/图5 断言标注块在上方（下区为照片色、上区为标注块色系）。取样点避开 Logo/胶囊/圆点区域（如 x=621 中线、上取样 y=300、下取样 y=1500）。
2. **照片页品牌与圆点保留**：photo_pool 造 1 张图渲染，断言内页右上 Logo 区有白色像素（约 `(690, 50, 830, 150)`）、右下圆点区有白色像素。
3. **无照片兜底**：photo_pool=None 且无 `_photo_sources` 命中 → 渲染不抛异常，内页仍是可解码 PNG 且尺寸 1242×1660。
4. **回归**：现有 `test_render_carousel_uses_available_brand_photos` 继续通过（内页变为照片页后，其「上下像素不同」断言语义仍成立）；封面 Logo/字标可见性断言不变。

跑法：先定向 `tests/test_xhs_cards.py` + `tests/test_upload_api.py`，再全量 `python3 -m pytest -q`（存量 8 条 UI 基线失败不算回归）。

### 改动 C：验证脚本（可选，VM/本地均可跑）

建议用「生产链路真渲染」目检：`pick_photos`（真实 `data/logiflow.db` + 真实 `static`）命中 → 拷入临时 static → `render_carousel(photo_pool=pool)` 出 5 张 PNG，确认图2-5 全部是照片页且标注块 下/上/下/上 交替、封面保持现状。此脚本不入库，验收用。

## 四、验收清单

1. pytest：新增用例全绿、现有 xhs_cards / upload_api 测试无回归、全量无新增破坏（存量 8 条 UI 基线除外）。
2. 生产链路真渲染（真库真素材）5 张 PNG：图2-5 全部照片页，标注块位置 **下/上/下/上**；封面保持「上图文字、下图照片」现状。
3. 每页标注块内：大标题 + 要点徽章 + 页码胶囊 + 底部圆点齐全；Logo 白标在右上可见。
4. 照片缺失场景：某页缺图只回退该页（渐变），整批渲染不崩。
5. `attachments` 的 `template_version == "buffalo-reference-v5"`。
6. **重启 app**（模板常量生效于运行进程）。

## 五、回滚

`git revert` 本批提交即回到 v4 文本页渲染。注意：版本断言同步在**同一提交**内（`xhs_cards.py` + 两个测试文件一起），回滚整体还原，不会出现版本号与断言错位。

## 六、备注

- 本批不新增任何素材调度/门禁逻辑：照片供给（批次 2）已闭环，只是绘制层把它用起来。
- 标注块上下交替是总指挥拍板的定式，**不是**为对齐全网规律硬凑——验收时逐页目检，凡「图2 下、图3 上」出现错位即不通过。
- 若某页照片与标注块同侧颜色过近导致文字读不出，优先加遮罩深度、**不改**照片选择逻辑。
- 全量 pytest 以 executor 本地为准（VM 无网络装不了 pytest）；VM 侧可用 harness 复现新增渲染逻辑（PIL 可用，构造临时 static + photo_pool 即可）。
