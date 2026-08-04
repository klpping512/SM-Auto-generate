# Cursor 执行指令 · P1 终局：清关/关税放闸(preparation 模式) + 文案门禁

> 总指挥背景（先读，这条比之前都关键，涉及受监管宣称）：
> P1 诊断结案——匹配失败唯一真因是 customs 内容缺口。总指挥拍板走**放闸(preparation 模式)**：
> 让清关/关税节点可用 warehouse/delivery 素材作「备货待清关/发运前准备」上下文，**但口播绝不能宣称
> 「已清关/已完成」**——对物流品牌这是虚假宣称红线。
>
> **已核实的硬事实（决定本指令范围）**：系统当前**没有任何确定性文案门禁**。末端早已放闸，但防过度宣称
> 只靠 app.py 生产 prompt 里一句自然语言（L1942-1943），无硬校验；唯一的确定性黑名单
> `hotspot_preview_narration.deterministic_evidence_issues`(L262-292) 只拦油价/道路两类，**不含任何
> 「已清关/已送达」词，且生产主链根本不调用它**。所以"放闸+门禁"里的门禁**今天是零、必须新建**。
>
> **∴ 本指令三件套必须一起交付、缺一不可**：①放闸 ②安全兜底模板 ③确定性过度宣称拦截器（并接入生产链）。
> **严禁只做①就上线**——那等于裸奔，还是在受监管的清关话题上裸奔。
>
> 关键可行性（已核实）：生产链在生成旁白时**能拿到每条 scene 的 `primary_category` + `logistics_nodes`**
> （app.py:1428 `_compact_topic_evidence` 已带 category），所以按 scene 精准拦截**做得成**。
>
> ⚠️ 下述行号可能因近期改动漂移，Cursor 落地前用函数名/锚点字符串二次定位，以实际代码为准。

---

## 改动 ①：放闸 —— `hotspot_video_planner.py` `_eligible_owned_categories`（约 L160-161）

**现状：**
```python
    if any(node in {"清关", "customs", "关税"} for node in nodes):
        return {"customs"}
```
**改为：**
```python
    if any(node in {"清关", "customs", "关税"} for node in nodes):
        # 放闸(preparation 模式)：无真 customs 素材时，允许 warehouse(备货)/delivery(发运)
        # 作为"清关前准备"上下文。customs 真素材仍由 rank() 的节点标签相关性优先选中；
        # 这里放宽准入的同时，必须由文案门禁(改动③)确保口播只说准备、不宣称已清关。
        return {"customs", "warehouse", "delivery"}
```
**为何不含 staff/facility**：人脸/叉车对"清关准备"无叙事价值、只增噪；warehouse+delivery 是最紧的合理集。
（`_owned_candidates:271` / `_owned_image_candidates:533` 会自动按新集合放行，无需另改。）

---

## 改动 ②：安全兜底模板 —— `hotspot_video_planner.py` `_voiceover`（约 L557-580）

末端已有确定性安全分支（约 L574-578：category ∈ {warehouse,staff,facility} 且节点命中末端时，写死
「配送前的…先把异常留在仓内」）。**照此为 customs 节点加一个平行分支**，作为模型改写前的安全基线：

- 触发条件：`category in {"warehouse", "delivery"}` 且 `nodes ∩ {"清关","customs","关税"}` 非空。
- 返回写死的**准备式**文案，例如：
  `f"清关前的{labels}：先在仓内把单证与货物备齐，等待海关放行。"`
- **红线**：模板文案本身**绝不含**「已清关/清关完成/已放行/已通关」等完成词，只用「备齐/等待/清关前/发运前」这类准备词。

（注意：模型改写链会覆盖此模板，所以模板安全 ≠ 最终安全——最终安全靠改动③。模板是"即便门禁回退也有话可说"的底。）

---

## 改动 ③：确定性过度宣称拦截器（核心，今天为零）

### 3.1 新增拦截函数

在 `hotspot_preview_narration.py`（`deterministic_evidence_issues` 附近，约 L262）新增一个**独立、纯函数、可单测**的检查，签名建议：

```python
def overclaim_completion_issues(voiceover: str, primary_category: str, logistics_nodes: list[str]) -> list[str]:
    """当一条 scene 用非-customs 素材(warehouse/delivery/staff/facility)在 customs 节点下，
    却在旁白里宣称已完成受监管结果时，返回问题列表(非空即违规)。纯确定性，可单测。"""
```

**匹配规则（务必按此，避免误杀准备式文案）**：

- 仅当 `primary_category` ∈ {"warehouse","delivery","staff","facility"}（即非真 customs 素材）
  **且** `logistics_nodes` 命中 {"清关","customs","关税"} 时才检查。
- 命中"完成型宣称"即违规。**完成词表（子串匹配即可）**：
  ```
  CUSTOMS_DONE = ("已清关","清关完成","完成清关","已通关","通关完成","已放行","海关放行","货物放行","已报关完成")
  DELIVERY_DONE = ("已送达","已交付","已签收","派送完成","已妥投","妥投完成","送达客户")  # 顺带堵上末端今天的裸奔
  ```
- **允许的准备式措辞（必须不被误判，写进单测）**：
  `备货待清关 / 清关前 / 发运前准备 / 等待海关放行 / 待通关 / 准备清关 / 备齐单证`。
  （设计上完成词均含「已…」或「…完成」，准备词用「待/等待/前/准备」，子串黑名单天然不重叠——但**必须用单测证明**。）

### 3.2 接入生产主链 B1（今天完全无后置校验 —— 这是最要命的缺口）

在 `app.py` topic-brief 视频路由、模型改写产出各 scene 最终 voiceover 之后、字数硬边界(约 L1964-1965)与进入渲染之前，**对每条 owned 素材 scene 调用 `overclaim_completion_issues`**（category 取该 scene 的 `primary_category`，nodes 取 brief 的 `logistics_nodes`）。

- **命中处理（气密关键）**：**不放行模型那句**，回退为改动②的安全兜底模板文案（同一 scene 的确定性准备式文案），并在渲染 report 里记录一条 `overclaim_guard` 命中项（含原句、category、替换后文案）。这样保证**任何过度宣称都不可能进渲染**。

### 3.3 接入预览链 B2 并补 category

`hotspot_preview_narration.build_messages` 的 `locked_scenes`（约 L136-147）**当前只传 evidence_type、不传 primary_category** → 模型无法区分 warehouse 上下文。请：
- 在 `locked_scenes` 每条补上 `"primary_category": ...`；
- 预览生成后同样调用 `overclaim_completion_issues` 做后置校验，命中则回退安全文案（与 B1 一致）。

---

## 硬边界（不得越界）

- 三件套（①②③）**必须同一批提交**；**不允许只交①放闸**。
- 不改选片排序 `rank()`、不改闸门执行点 `_owned_candidates`/`_owned_image_candidates` 的过滤结构（它们读新 eligible 集即可）。
- 不动 `truth_guard.py`、`video_evaluator`（与本主题无关）。
- 拦截器必须是**纯确定性子串匹配**，不得引入模型调用。

---

## 测试（这是"门禁气密"的唯一证明，必须充分）

新增 `tests/test_overclaim_guard.py`，至少覆盖：

1. **完成词全部被拦**：`CUSTOMS_DONE` + `DELIVERY_DONE` 每个词，在 category=warehouse/delivery + node=清关 组合下，`overclaim_completion_issues` 返回非空。
2. **准备式措辞全部放行**：`备货待清关 / 清关前发运准备 / 等待海关放行 / 发运前把单证备齐 / 待通关` 等，返回空（**证明不误杀**）。
3. **作用域正确**：同样的完成词，若 `primary_category="customs"`（真 customs 素材）或 node 不含清关/关税 → 不拦（真素材有权说完成；非清关话题不受此约束）。
4. **B1 生产链集成测试**：构造一个 customs brief + 仅 warehouse owned 素材，模型链产出含"已清关"的 voiceover（可 mock 模型返回），断言最终渲染 report 里该 scene 文案被回退成准备式、且 `overclaim_guard` 命中被记录、**成片不含"已清关"**。
5. **放闸生效**：customs brief 下 `_eligible_owned_categories` 返回 `{"customs","warehouse","delivery"}`；`_owned_candidates` 能选出 warehouse 素材（改动前为 0）。
6. 全量 `pytest` 绿（存量 8 个无关失败可沿用基线，如实注明）。

---

## 回报格式（给总指挥验收）

- 三件套 diff / commit hash（强调是否同批提交）。
- 单测结果：完成词 N 个全拦、准备词 M 个全放行的具体清单。
- B1 集成测试证据：一条"模型说了已清关 → 被回退 → 成片不含已清关"的端到端断言输出。
- 放闸前后对比：某 customs brief 的 `_owned_candidates` 数量 0 → X。
- 一句话确认：**是否可以保证任何过度宣称都进不了渲染**（气密性自评）。
