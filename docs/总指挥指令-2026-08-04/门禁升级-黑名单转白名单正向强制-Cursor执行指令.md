# Cursor 执行指令 · 文案门禁升级：黑名单 → 白名单/正向强制（真气密）

> 总指挥背景（先读，直接续 P1 终局 #6）：
> P1 终局给清关节点放了闸（可用 warehouse/delivery 素材做"清关前准备"上下文），并建了确定性门禁
> `overclaim_completion_issues` 拦"仓库画面谎称已清关"。**但验收时已诚实下调一格**：那是**有限子串黑名单**，
> 只对列举的完成词气密。独立实测 **6 句自然完成说法漏过**：`已出关 / 通关手续办妥 / 顺利过关 /
> 货物已顺利放行 / 海关放行了货物 / 已经清关完毕`。对物流品牌，受监管虚假宣称的残余风险仍在。
>
> **本指令 = 把门禁从"黑名单拦禁词"翻成"白名单正向强制"——真气密。**
> 核心思路：**借用上下文的危险场景，不再"检测有没有说错话"，而是"根本不给它自由说话的机会"**——
> 非-customs 素材（warehouse/delivery/staff/facility）出现在 customs 节点下时，**其口播强制走
> `safe_customs_preparation_copy` 安全准备模板**，模型改写的那句直接不采用。白名单外无自由文本 = 无从宣称已清关。
>
> **代价（必须让总指挥知情，已在 README #6 备注写明这是白名单方案的固有代价）**：
> 被强制的这些 scene **失去文案多样性**（都用准备式模板的有限变体）。但代价被**牢牢限制在"借来的上下文"
> 这一小撮危险 scene**——真 customs 素材、非清关节点的 scene 完全不受影响，照常自由改写。
> 这正是白名单相对"黑名单扩词"（廉价但永远追不全自然语言）的价值：**用极小范围的僵化换真气密**。
>
> ⚠️ 行号可能漂移，落地前用函数名/字符串锚点二次定位，以实际代码为准。

---

## 现状核实（P1 终局已落地的地基，本次在其上翻转策略）

- `hotspot_video_planner.py`：`_eligible_owned_categories`（约 L154）清关节点返回
  `{"customs","warehouse","delivery"}`（放闸，不动）；`safe_customs_preparation_copy(category,
  max_chars, min_chars)`（约 L555）已存在，产出纯准备式文案（本次复用它做强制模板）；
  `plan_followup_scenes`（约 L790）已给 owned 镜头设 `primary_category`（守卫覆盖前提，已具备）。
- `hotspot_preview_narration.py`：`overclaim_completion_issues(voiceover, primary_category,
  logistics_nodes)`（黑名单，本次**保留但降级为二道防线**）+ `apply_overclaim_guard`
  （命中回退，接入 B2 预览链）。
- `app.py`：`_generate_topic_brief_video`（约 L2075）在质检后、渲染前调 `apply_overclaim_guard`（B1 生产链）。

**关键判断**：白名单强制的接入点与黑名单**同址**（`apply_overclaim_guard` 及其两个调用点），
改的是**判定逻辑**——从"扫文本命中禁词才回退"改成"命中危险场景即强制模板，无条件"。

---

## 改动 ①：新增"危险场景"正向判定（白名单核心）

在 `hotspot_preview_narration.py`（`overclaim_completion_issues` 附近）新增一个**纯函数**，
判定一条 scene 是否属于"借用清关上下文的非-customs 素材"——**只看素材类别与节点，不看文本**：

```python
BORROWED_CUSTOMS_CONTEXT = frozenset({"warehouse", "delivery", "staff", "facility"})
CUSTOMS_NODES = frozenset({"清关", "customs", "关税"})

def requires_safe_customs_copy(primary_category: str, logistics_nodes: list[str]) -> bool:
    """当一条 scene 用非-customs 素材出现在 customs 节点下(借用清关上下文)时返回 True。
    此时该 scene 的口播必须强制走安全准备模板——不检测文本，直接剥夺其自由宣称的机会(真气密)。
    真 customs 素材(primary_category=='customs')返回 False——它有权正常改写。"""
    if primary_category not in BORROWED_CUSTOMS_CONTEXT:
        return False
    return any(node in CUSTOMS_NODES for node in (logistics_nodes or []))
```

**语义**：这与黑名单 `overclaim_completion_issues` 的**触发范围完全相同**（同样是"非-customs 素材 ×
customs 节点"），差别在**动作**：黑名单"再看文本有没有禁词"，白名单"命中即强制"，跳过文本检测这一漏点。

---

## 改动 ②：`apply_overclaim_guard` 升级为"正向强制 + 黑名单兜底"

`hotspot_preview_narration.apply_overclaim_guard(generated_scenes, scenes, logistics_nodes)`
现状是遍历 scene、`overclaim_completion_issues` 命中才回退。**改为两层**：

```python
for gen, src in zip(generated_scenes, scenes):
    category = src.get("primary_category") or ""
    if requires_safe_customs_copy(category, logistics_nodes):
        # 第一道(白名单/正向强制)：借来上下文的危险 scene，无条件用安全模板，
        # 模型那句连看都不看——真气密。
        safe = safe_customs_preparation_copy(category, max_chars=..., min_chars=...)
        record = {"scene": ..., "category": category, "mode": "whitelist_forced",
                  "original_voiceover": gen.get("voiceover"), "safe_copy": safe}
        gen["voiceover"] = safe
        gen["text_overlay"] = safe.rstrip("。")[:24]
        records.append(record)
        continue
    # 第二道(黑名单兜底)：其余 scene 保留原有过度宣称检测(防御纵深，不删)。
    issues = overclaim_completion_issues(gen.get("voiceover",""), category, logistics_nodes)
    if issues:
        ... # 沿用 P1 终局的回退逻辑，mode="blacklist_fallback"
```

**要点**：
- 白名单在前、黑名单在后：借用上下文的 scene 走强制（气密）；其余 scene（真 customs 素材、
  或根本非清关节点里偶发的越界）仍有黑名单兜底（防御纵深，**不删黑名单**）。
- `safe_customs_preparation_copy` 的 `max_chars/min_chars` 沿用 P1 终局在该调用处已用的字数边界
  （避免预览字数超限——P1 终局踩过这坑，安全模板不加开场前缀）。
- record 里用 `mode` 区分 `whitelist_forced` / `blacklist_fallback`，便于验收统计"多少条被强制、
  多少条靠兜底"，也便于观察白名单是否覆盖了绝大多数危险 scene（理想是兜底命中数≈0）。

---

## 改动 ③：两条生产链接入点无需改结构（同址生效）

- B1 生产链 `app.py:_generate_topic_brief_video`（约 L2075）：仍调 `apply_overclaim_guard(...)`，
  函数内部升级后自动生效，**调用点一字不改**。report 里的 `overclaim_guard` 记录现在会含 `mode` 字段。
- B2 预览链 `hotspot_preview_narration.generate_narration`：两个 return 点仍调 `apply_overclaim_guard`，
  同样自动生效。`build_messages` 的 `locked_scenes` 已带 `primary_category`（P1 终局已补，不动）。

**红线**：不改选片、不改放闸集合、不改 `safe_customs_preparation_copy` 模板文案本身。

---

## 硬边界（不得越界）

- **不删黑名单** `overclaim_completion_issues`：它降级为第二道防线（防御纵深），仍要单测保绿。
- **强制范围严格限定**：只有 `primary_category ∈ {warehouse,delivery,staff,facility}` 且节点命中
  {清关/customs/关税} 的 scene 被强制；真 customs 素材、非清关节点的 scene **绝不受影响**（否则误伤文案多样性）。
- 不改放闸 `_eligible_owned_categories`、不改 `rank()`/选片、不动 `safe_customs_preparation_copy` 文案。
- 强制判定必须**纯确定性、不看文本、不引入模型调用**。

---

## 测试（气密性的唯一证明，必须比 P1 终局更强）

扩展 `tests/test_overclaim_guard.py`，至少覆盖：

1. **P1 终局漏过的 6 句现在被强制**：对 warehouse/delivery × 清关节点的 scene，
   voiceover 分别为 `已出关 / 通关手续办妥 / 顺利过关 / 货物已顺利放行 / 海关放行了货物 / 已经清关完毕`，
   经 `apply_overclaim_guard` 后**全部被替换成安全模板**（`mode=whitelist_forced`），断言最终文本
   不含任一句原文——**这是本次升级的核心证据**（证明白名单堵住了黑名单的 6 个漏点）。
2. **强制不看文本**：warehouse × 清关节点、voiceover 是**完全无害的准备式文案**（如"备货待清关"）→
   仍被替换成安全模板（`whitelist_forced`）。证明白名单是"剥夺自由文本"而非"检测违规"——这是它气密的原理，
   也是它牺牲多样性的地方，需在断言注释里点明这是预期行为。
3. **范围正确不误伤**：
   - 真 customs 素材（`primary_category="customs"`）× 清关节点 → **不强制**，模型文案原样保留；
   - 非清关节点（如末端/仓储话题）的 warehouse scene → **不强制**，原样保留。
4. **黑名单兜底仍活**：构造一条不触发白名单但含完成词的 scene（如某越界场景）→ `overclaim_completion_issues`
   仍命中、仍回退（`mode=blacklist_fallback`），证明防御纵深没删。
5. **B1 端到端**：customs brief + 仅 warehouse 素材，模型链产出含"已经清关完毕"（P1 终局漏过的词，可 mock）→
   断言成片该 scene 文案被强制成准备式、report 记 `whitelist_forced`、**成片绝无任何完成型宣称**。
6. **多样性代价可见**：断言被强制的多条 scene 文案取自 `safe_customs_preparation_copy` 的有限变体
   （证明代价确实存在但被限定在危险 scene），如实记录变体数量供总指挥知情。
7. 全量 `pytest` 绿（存量 8 个无关失败沿用基线，如实注明）。

---

## 回报格式（给总指挥验收）

- 改动 diff / commit hash（强调黑名单未删、仅降级为第二道防线）。
- **P1 终局 6 句漏点现在全部被强制**的断言输出（逐句）——这是升级成立的硬证据。
- 强制范围证据：真 customs 素材 / 非清关节点的 scene 未被触碰的断言。
- `whitelist_forced` vs `blacklist_fallback` 命中计数（理想：真实 brief 里兜底≈0，说明白名单已覆盖危险面）。
- 多样性代价说明：被强制 scene 用了几种安全模板变体。
- 一句话确认：**是否已从"检测有没有说错话"升级为"根本不给借用上下文的 scene 说错话的机会"，
  即受监管完成型宣称是否已真气密（不再依赖禁词枚举）。**
