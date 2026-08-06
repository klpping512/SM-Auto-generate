# 总指挥指令 批7 v2 ｜ chat 一键生产打通：生产视频链路（对比自动降级科普 + 生产接线）

> 日期：2026-08-06 ｜ 状态：**已产出，待 opencode 执行**
> 拍板：阶段 1 先行（常青自动出片+对比降级）；**hook 链路分开解决**（generic 开场池 + 匹配放宽 = 批12，本批不做）。
> 目标用户：商务团队（完全不懂技术）。预期链路：**打字 → 出视频 → 自动发布**。任何需要技术知识（候选商/来源/报价单）的中间步骤对他们是失败的。
> 红线：**不拆对比门禁**（防编造排名/报价）；只把「门禁触发后的死路」改为「自动降级到安全路径继续生产」。

---

## 〇、范围与跨链边界（先读）

本批 = **生产视频链路**：`打字 → 意图分类 → 对比降级 → 内容生成 → Hook 接线 → 出片`。改动集中 `chat_intent.py` / `app.py`(`ai_chat`) / `static/chat.html` / 测试。

**跨链依赖（不在本批）**：常青/降级话题能否自动锁定开场 Hook 并亮出「创建60秒视频项目」按钮，依赖 **hook 链路**：generic_logistics 开场池（#33）+ 空 profile 话题匹配放宽（#34），已拆为**批12**（`批12-hook链路-常青开场池与匹配放宽-opencode执行指令.md`），分开解决。

| 链 | 负责 | 文档 |
|---|---|---|
| 生产视频链路 | 本批（批7 v2） | 本文档 #28-#32 |
| hook 素材链路（开场池+匹配放宽） | 批12 | `批12-hook链路-常青开场池与匹配放宽-opencode执行指令.md` |
| hook 素材链路（门禁扩词/回纳现场源/清残留） | 批11 | `整合-回纳现场源与门禁扩词-opencode执行指令.md`（执行中） |

**执行顺序建议**：本批与批12 同改 `app.py` 但不同函数（本批 `ai_chat`、批12 `_marketing_hook_candidates`/`_retrieve_confirmed_chat_hooks`）。为避免并行冲突，**同一 opencode 会话连续执行 批12 → 批7 v2**（或反向均可），不要两批并行开两个会话。批11 已在跑（只碰 `_is_confirmed_renderable_hotspot_hook` 门禁词表 + `hotspot_media.py` prefilter，与本批函数不重叠，但会平移行号）——**定位一律用函数名/代码锚点，不用绝对行号**。

---

## 一、拍板与验证证据（2026-08-06，总指挥已用实际代码验证）

| 验证项 | 结果 |
|---|---|
| 原始输入 `南非本地快递对比评测` | mode=comparison_research，evidence=insufficient（三 pattern 全 False） |
| 降级标题 `南非本地快递怎么选？关键维度科普` | mode=**evergreen**，**零残留 COMPARISON_MARKERS**，不会再进对比门禁（无死循环） |
| 降级后是否走正常生产链 | ✅ 改写后 `content_mode=evergreen` → `should_attempt_hook_retrieval`=True → `_retrieve_confirmed_chat_hooks` use_generic 分支（app.py:4525） |
| `enforce_comparison_authenticity` 对无违规常青输出 | 原样返回、blocked=False，不误伤（ai_engine.py:765-771） |
| 门禁触发后现有前端 | `static/chat.html:357` 硬编码「对比框架证据不足，暂不可创建视频项目」——**死路** |
| **已知断点（跨链）** | 常青/降级话题的**按钮就绪**依赖 generic_logistics 开场池+匹配放宽（批12）。本批先把生产链行为改对，按钮端到端验收等批12 落地后补验 |

**拍板（2026-08-06 AskUserQuestion）**：自动降级科普、直接出视频（推荐项）。非「按钮确认」、非「框架可渲染」。

**关于「内置模型联网找对比」（用户追问，已答复）**：系统现无通用联网检索（唯一网络接入是热点 RSS）；SA 主流快递（TCG/Aramex/DHL）不公布静态价目表（全是报价计算器），模型搜不到「固定价格」做对比；错数据=竞品诋毁+虚假宣传。正确形态是「AI 代查 + 人工确认」（阶段 2，批8），且对比评测永远不进全自动发布。**本批不做联网检索**。

---

## 二、改动清单（按序执行，锚点=代码结构，行号仅供参考）

### 指令 #28：chat_intent.py 新增 `comparison_to_evergreen_topic`（确定性改写）

在 `chat_intent.py` 的 `assess_comparison_evidence` 之后新增函数：

```python
def comparison_to_evergreen_topic(topic: str) -> str:
    """把对比评测题材确定性改写为安全科普视角。

    保证：输出永远不含 COMPARISON_MARKERS（chat_intent.COMPARISON_MARKERS），
    因此重走 classify_content_mode 不会再进对比门禁（无死循环）。
    """
    raw = " ".join(str(topic or "").split())
    if not raw:
        return "南非物流怎么选？关键维度科普"
    if "南非" in raw and any(
        w in raw for w in ("快递", "物流", "货运", "清关", "仓储", "配送")
    ):
        return "南非本地快递怎么选？关键维度科普" if "快递" in raw else "南非物流怎么选？关键维度科普"
    if any(w in raw for w in ("快递", "物流", "货运", "配送")):
        return "本地快递怎么选？关键维度科普"
    return "物流服务怎么选？关键维度科普"
```

自检：
```bash
python3 -c "import chat_intent as c; [print(t, '->', c.comparison_to_evergreen_topic(t), '|', c.classify_content_mode(c.comparison_to_evergreen_topic(t))) for t in ['南非本地快递对比评测','最近哪家快递最好','', '对比 The Courier Guy 和 Fastway 的价格']]"
```
输出必须全部为 evergreen/general_copy，且改写结果不含 `对比/评测/排行/哪家/最好/性价比/实测` 等 COMPARISON_MARKERS 子串。

### 指令 #29：app.py `ai_chat` 降级逻辑（门禁触发 → 自动降级走正常生产链）

`app.py` `ai_chat`（:4955 起）。找到这几行（锚点）：

```python
    evidence = chat_intent.assess_comparison_evidence(
        messages, topic=latest_topic, context=req.context or "",
    )
    platforms = [p.value for p in req.platforms]
    authenticity_blocked = False
    brand_assets_insufficient = False
```

**①** 在 `brand_assets_insufficient = False` 之后追加一行：

```python
    degraded_from_comparison = False
```

**②** 在紧邻其后的 `if content_mode == "comparison_research" and evidence["evidence_state"] != "sufficient":` **之前**插入降级块：

```python
    if content_mode == "comparison_research" and evidence["evidence_state"] != "sufficient":
        # 商务团队一键生产：对比题材无真实资料时，自动降级为科普视角，
        # 重写消息后走正常生产链（通用物流 Hook 兜底 → 可创建视频项目）。
        degraded_from_comparison = True
        latest_topic = chat_intent.comparison_to_evergreen_topic(latest_topic)
        messages = list(messages)
        if messages and messages[-1].get("role") == "user":
            messages[-1] = {**messages[-1], "content": latest_topic}
        content_mode = chat_intent.classify_content_mode(latest_topic)
        event_anchor = chat_intent.assess_event_anchor(latest_topic, context=req.context or "")
```

> 说明：改写后 `content_mode` 必为 evergreen/general_copy，原 `if comparison_research...` 框架分支（紧随其后）成为**安全网**，正常不再触发。**不删**该分支——若未来词表变化导致改写结果仍被分为 comparison_research，仍走框架，不死机。

### 指令 #30：app.py 加固 + 响应字段

1. 在 ai_chat 的 if/else 生产分支结束之后、`for item in outputs:` 循环之前（锚点：`if content_mode == "comparison_research":` 那段 `enforce_comparison_authenticity` 之后的同一缩进层级）追加：

```python
    if degraded_from_comparison:
        outputs, _ = ai_engine.enforce_comparison_authenticity(
            outputs, {"sufficient": False, "evidence_state": "insufficient"},
        )
```

> 防模型在常青文案里残留「排名第一/实测/最稳」等无依据表述；无违规时原样返回，不误伤。

2. 在 `ai_chat` 返回 dict（锚点：`"content_mode": content_mode,` 之后）加两个字段：

```python
        "degraded_from_comparison": degraded_from_comparison,
        "degradation_message": (
            "对比评测需要真实报价/时效资料，已自动切换为科普视角生成视频；"
            "如手头有官方报价单或测试记录，可点『补充评测资料』生成正式对比评测。"
            if degraded_from_comparison else ""
        ),
```

### 指令 #31：static/chat.html 降级提示条

在 `resultCardMarkup` 中 `const tabs=...` 之前构建提示条：

```js
  const degradedNotice=result?.degraded_from_comparison
    ?`<div class="result-warning"><strong>已自动切换科普视角</strong><p>对比评测需要真实报价/时效资料，已自动切换为科普视角生成视频；如手头有官方报价单或测试记录，可点『补充评测资料』生成正式对比评测。</p><button class="btn btn-secondary btn-sm" onclick="promptComparisonEvidence()">补充评测资料</button></div>`
    :'';
```

并在返回模板中、`${resultStateCard(result,id)}` **之前**插入 `${degradedNotice}`：

```js
  return `<div class="result-card" id="result-${id}">${degradedNotice}${resultStateCard(result,id)}${showHotspotBanner?hotspotRetrievalMarkup(result,id):''}<div class="result-tabs">${tabs}</div>${panels}</div>`;
```

> 效果：商务用户看到「已自动切换科普视角」提示 + 常规出片按钮（generic 池落地后显示）；想出正式对比评测的可点「补充评测资料」（模板含"服务商/价格/来源"等词 → 命中 CANDIDATE+PRICE+SOURCE → evidence 变 sufficient → 走正式对比路径，既有逻辑，无需改动）。

### 指令 #32：测试更新

`tests/test_chat_intent.py`：

1. **新增** 2 条用例：
   - `test_comparison_to_evergreen_topic_never_reenters_comparison_gate`：对 `["南非本地快递对比评测", "最近哪家快递最好", "对比 The Courier Guy 和 Fastway 的价格", ""]` 各输入断言：`classify_content_mode(改写结果)` 不是 `comparison_research`，且改写结果不含 `COMPARISON_MARKERS` 任一子串。
   - `test_comparison_to_evergreen_topic_returns_safe_defaults`：空输入 → 默认标题；输出均以「怎么选？关键维度科普」结尾。

2. **更新** `test_ai_chat_comparison_without_evidence_returns_framework_and_skips_discovery` 为降级后新行为，改名 `test_ai_chat_comparison_without_evidence_degrades_to_evergreen_production`：
   - fake_chat 返回改为**良性**常青内容（title `"南非物流科普"`、body `"科普内容"`、无"4家/实测/最稳"等词，否则会被 #30 加固降级成框架，破坏断言）。
   - 断言改为：`payload["degraded_from_comparison"] is True`；`payload["content_mode"] == "evergreen"`；`payload["result_state"] != "framework_pending_evidence"`；`called["chat"] == 1`；`called["hooks"] == 1`（evergreen → should_attempt_hook_retrieval=True）；`payload["outputs"][0]["result_kind"] != "framework"`；`tmp_db.list_hotspot_discovery_requests() == []`（evergreen 不 enqueue discovery）。
   - 保留原断言里「对比门禁仍保护正式对比」的语义由 `test_derive_result_state_priority` 覆盖（`comparison_research + insufficient → framework_pending_evidence` 仍在，勿删）。

**不动的测试**：`test_build_comparison_framework_has_no_fake_review_language`、`test_framework_with_candidate_names_still_leaves_prices_blank`、`test_enforce_comparison_authenticity_downgrades_fabricated_review`、`test_chat_ui_uses_result_state_card_without_conflicting_peer_status`（框架安全网保留，字符串断言仍成立）。

---

## 三、验收口径（分两层）

### A. 本批验收（生产链路，可独立验证）

1. 按 #28→#32 顺序执行，每步完成自检。
2. **定向测试**：`pytest tests/test_chat_intent.py -q` 全绿（含更新后的降级用例）。
3. **全量回归** `pytest -q`：与基线（875 passed / 8 存量失败）对比，8 个存量失败逐条一致，**不得新增**。
4. **运行时手测**（app 重启后，浏览器）：
   - 输入 `南非本地快递对比评测` → 出现「已自动切换科普视角」提示条 + 文案「南非本地快递怎么选」类 + **不再出现**「对比框架证据不足，暂不可创建视频项目」；`degraded_from_comparison=true`、`content_mode=evergreen`、`result_state != framework_pending_evidence`。
   - 输入 `对比 The Courier Guy 和 Aramex，官网报价隔日达 R89，来源官网价目表 2026-07-01` → 仍走**正式对比路径**（有资料不被降级），出正式内容，`degraded_from_comparison=false`。

### B. 端到端验收（依赖批12，落地后补验）

5. 输入 `海外仓是什么` / `南非本地快递怎么选？关键维度科普` / `南非跨境物流怎么入门` → 出现「创建60秒视频项目」按钮 → 进入视频工作台可渲染，渲染首段=选定的 generic_logistics 开场画面。**此项需批12 落地后方可通过，本批不背锅。**

---

## 四、明确不做（防漂移）

- ❌ **不拆对比门禁**；`assess_comparison_evidence`、`build_comparison_framework`、`enforce_comparison_authenticity`、`framework_pending_evidence` 状态全部保留（安全网）。
- ❌ **不接联网检索/「AI 代查+人工确认」**（阶段 2=批8；且对比评测不做全自动发布）。
- ❌ **不做 generic_logistics 开场池（#33）与匹配放宽（#34）**——那是 hook 链路，批12。
- ❌ 不改编辑器侧证据门禁（`editor.html` evidenceGateBanner / `editor-transfer.js` evidence_status）。
- ❌ 不动其他内容模式（hotspot/evergreen/general_copy）既有路径。
- ❌ 不新增选题库/预审选题（商务团队自由输入是拍板路径）。

---

## 五、回滚

`git revert <批7 v2 提交>` 恢复：改写函数、ai_chat 降级块、加固与响应字段、前端提示条、测试更新。回滚后回到「对比框架 + 补充评测资料」原行为。回滚前提：本批与批11/批12 无同函数冲突（若并行会话动 `app.py` 的 `ai_chat` 需先合并）。

---

## 六、交付口径

- 完成标记：#28-#32 落地 + 重启 + 验收 A1-A4 逐条打勾；B5 标注「待批12 补验」。
- 提交信息：`批7 v2 生产链路：#28改写函数 #29-30降级走生产链+加固+响应字段 #31前端提示条 #32测试`。
- 同步 Obsidian 改进日志 + 更新指令索引 README。
