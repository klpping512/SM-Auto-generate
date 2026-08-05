# Cursor 执行指令 · 画面素材分类统一 · 阶段C：收敛第三份词表到真源

> 总指挥背景（先读，续指令 #1 阶段 A+B）：
> 阶段 A+B 已把两份 `CATEGORY_KEYWORDS` 漂移修好、指向真源 `asset_taxonomy.py`（commit 0cbd945+d423323）。
> 验收发现**第三份**"关键词→分类"词表 `asset_processing.py:97 CATEGORY_TERMS` 未并入，且它才是
> **素材入库时真正的规则分类器**（`classify_evidence` → `process_asset_job` 写 `asset_segments.primary_category`）。
> 它仍含「访谈」、customs 类用「关税」（真源用「通关」）——口径漂移。
>
> **∴ 阶段C = 把第三份收敛到真源 + 删「访谈」+ 统一「关税→通关」，且不回退入库分类行为。**
>
> **⚠️ 最关键的对抗发现（决定本指令必须两步走，不能一句 alias 了事）**：
> 直接把 `CATEGORY_TERMS = asset_taxonomy.CATEGORY_KEYWORDS` 会**隐性回退入库行为**——真源把
> 「港口/船舶/port」放在 `DELIVERY_TAG_VALUES`（能力标签，L42-58）、「terminal/container terminal/装卸」
> 放在 warehouse 侧能力标签，**不在 `CATEGORY_KEYWORDS` 主分类词表里**；而第三份 `CATEGORY_TERMS`
> 把这些当**主分类关键词**。直接 alias 会让入库打分器 `_category_scores` 丢掉这些命中——
> **港口/码头/船舶素材的 primary_category 会从 delivery/warehouse 掉到 other**。
> 所以必须**先给真源补词、再 alias**，否则是看不见的分类退化。
>
> ⚠️ 行号可能漂移，落地前用函数名/字符串锚点二次定位，以实际代码为准。

---

## 现状核实（三/四份词表清点）

| 词表 | 位置 | 方向 | 角色 | 本次处置 |
|------|------|------|------|---------|
| `CATEGORY_KEYWORDS`（真源） | `asset_taxonomy.py:81-108` | 关键词→分类 | 目标口径 | **补 5 个词**（见改动①） |
| `CATEGORY_TERMS`（第三份） | `asset_processing.py:97-105` | 关键词→分类 | **入库真规则分类器**（`_category_scores`→`classify_evidence`→`process_asset_job`） | **改为 alias 真源**（改动②） |
| `category_tags`（第四份） | `semantic_matching.py:116-124` | 分类→标签（反向） | `_tag_map`→`_score` 打分 | **仅对齐键集，不合并**（改动③，形态不同） |
| `ai_engine.py:22` 等 | 多处 | 节点/话题词 | 非"画面素材关键词→分类" | **不动**（阶段C 不碰） |

两份主分类词表键名一致（7 个英文键 warehouse/delivery/customs/brand/staff/facility/customer）、
值形态一致（`tuple[str,...]`）、无循环 import（`asset_processing` 未 import `asset_taxonomy`，
`asset_taxonomy` 不 import 任何本仓模块）——**语法上零障碍，唯一障碍是上述语义覆盖差异**。

---

## 改动 ①（必须先做）：给真源补回入库需要的主分类词

文件 `asset_taxonomy.py` `CATEGORY_KEYWORDS`（约 L81-108）。把第三份 `CATEGORY_TERMS` 里
**只在它有、且属于画面主分类语义**的词补进真源对应类，避免 alias 后入库分类退化：

- `delivery` 类补：`"港口"`, `"船舶"`, `"port"`（第三份 L99 有，真源无 → 不补则港口/船舶素材掉到 other）。
- `warehouse` 类补：`"terminal"`, `"container terminal"`, `"装卸"`（第三份 L98 有，真源无）。

**不补**的（第三份独有但真源刻意剔除/归他处，保持真源口径）：
- `staff` 的「访谈」——**这正是要删的噪词**，绝不补进真源。
- customs 的「关税」——真源用「通关」是既定口径；「关税」作为**节点名**已在 `NODE_CATEGORY_RULES`(L24-39)
  映射到 customs，作为**画面关键词**收敛掉是预期（画面里极少直接出现"关税"字样）。
- `facility` 的 forklift / customer 的 签收·delivery receipt 等：真源已有等价覆盖（叉车 / 客户·反馈），
  由 Cursor 逐词核对，**真源已含等价语义的就不补**，只补真源确实缺、且会影响入库结果的词。

**要点**：补词前后跑一次改动⑤的入库回归，确认港口/码头素材分类不变。

---

## 改动 ②：第三份改为 alias 真源（抄阶段 B 做法）

文件 `asset_processing.py`：

1. 顶部 import 区（现有 `import database` / `model_router` 约 L19-20 附近）加 `import asset_taxonomy`。
2. 删除 `CATEGORY_TERMS` 的字面定义（L97-105），改为向后兼容别名：

```python
# 收敛到单一真源(阶段C)。入库规则分类器 _category_scores 直接读真源，
# 「访谈」随之移除、customs 口径统一为「通关」。港口/船舶/terminal/装卸 已在改动①补回真源，
# 故入库分类行为不回退。
CATEGORY_TERMS = asset_taxonomy.CATEGORY_KEYWORDS
```

3. 确认 `_category_scores`（L232/238）、`classify_evidence`（L285/304/316）无需再改——
   它们读 `CATEGORY_TERMS.items()`，别名后自动读真源（键集一致、`other` 兜底逻辑 L244-246 不受影响）。

---

## 改动 ③：`semantic_matching.category_tags` 对齐（不合并，只统一键集）

文件 `semantic_matching.py` `category_tags`（L116-124，已有阶段C TODO 注释）。
**它是 `分类→标签` 反向映射（`dict[category, dict[dimension, set]]`），与真源 `关键词→分类` 不同构，
不能等号合并**。本次只做"键集与真源一致"的轻对齐：

- 键集对齐到真源 `CATEGORIES` 的语义分类（补齐缺失的 `brand`，或显式注释为何某类无标签映射）；
- 值里引用的标签词（如「仓库作业/道路运输」）确认取自 `asset_processing.TAG_TERMS` 的标准词、与真源分类语义不打架；
- 保留该结构独立（它服务 `_score` 打分，非入库分类）。

**若对齐 `category_tags` 会牵动较多 `test_semantic_matching.py` 断言（L193/205-231/249-251），
可作为本指令的可选第二步**——总指挥接受"改动③单独一个 commit、甚至下一轮再做"，
**优先保证改动①②（入库真源收敛）先落地**，因为那才是"访谈/关税"漂移的根。

---

## 硬边界（不得越界）

- **必须先补真源（①）再 alias（②）**：严禁只做②——那会让港口/船舶素材分类掉到 other（隐性回退）。
- 不动 `ai_engine.py:22` 等节点/话题词表（非画面素材分类，超出阶段C 范围）。
- 不改 `classify_evidence` 的 model 分支（高置信视觉模型覆盖，L301-314）。
- `category_tags`（③）只对齐键集、不与真源合并成同一字典。
- 不改 `asset_segments` 表结构、不改 `process_asset_job` 调用方。

---

## 测试（证明"收敛不回退入库行为"）

1. **入库分类不回退（核心）**：`tests/test_asset_processing.py:43-56` 现有用例
   （transcript「仓库团队正在分拣货物」+ ocr「CONTAINER TERMINAL」→ primary_category ∈ {warehouse,delivery}）
   **必须仍绿**；补充一条**港口/船舶**用例（ocr/transcript 含「港口」「船舶」→ 断言 primary_category==delivery），
   证明改动①补词生效、alias 后港口素材不掉 other。
2. **「访谈」已消除**：构造含「访谈」的 staff 文本，断言分类不再因「访谈」命中 staff（改由 员工/团队 等真源词命中）；
   grep 确认代码内「访谈」仅剩历史文档、`asset_processing.py:102` 已消失。
3. **customs 口径统一**：确认 `CATEGORY_TERMS["customs"]` 经 alias 后含「通关」不含「关税」（画面关键词侧）；
   「关税」作为节点仍由 `NODE_CATEGORY_RULES` 正确映射 customs（不受影响）。
4. **别名一致性**：断言 `asset_processing.CATEGORY_TERMS is asset_taxonomy.CATEGORY_KEYWORDS`（同一对象）。
5. 若同批做改动③：`test_semantic_matching.py:193/205-231/249-251` 相应更新并保绿。
6. 全量 `pytest` 绿（存量 8 个无关失败沿用基线，如实注明）。

---

## 回报格式（给总指挥验收）

- 改动 diff / commit hash（标明①补词、②alias、③是否本批做）。
- **入库不回退证据**：港口/船舶素材分类前后对比（改动前 CATEGORY_TERMS 命中 → 改动后经真源补词仍命中 delivery）。
- 「访谈」消除证据 + customs 口径「关税→通关」统一证据。
- `CATEGORY_TERMS is CATEGORY_KEYWORDS` 别名断言输出。
- 一句话确认：**第三份入库分类器是否已收敛到单一真源、「访谈」与「关税」漂移是否消除、且港口/码头等素材入库分类未回退。**
