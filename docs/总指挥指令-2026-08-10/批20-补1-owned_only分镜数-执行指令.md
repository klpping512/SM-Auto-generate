# 批20 补1 执行指令：owned_only 分镜数 502 修复

日期：2026-08-10
执行方：opencode（浏览器实测后补丁）
总指挥验收：对抗式复核（不认叙述，逐落点核 diff + 实测回证）

---

## 〇、背景与根因（代码实证，验收方已独立核实）

批20 浏览器实测 8/9 通过，唯 `owned_only` 出 502。根因链：

1. `hotspot_video_planner.py:805`：`owned_limit = 7 if target_duration_ms <= 60_000 else 8`——60 秒视频自有段上限 7。
2. owned_only 无热点 Hook（`plan_followup_scenes` 的 `picks=[]`）、无 context_images（app.py:855 else 分支）→ 计划场景数恰 = owned_limit = **7 段**。
3. 规划模型（mimo）**稳定输出 8 段分镜**（mix3/hotspot_owned 计划恰 8 段故通过，owned_only 计划 7 段故不通过）。
4. `app.py:1787` `_planner_json` **严格等号**校验：`len(scenes) != expected_scenes` → 抛「缺少标题、角度或有效分镜」。
5. repair 循环（app.py:2599 两次）对同一 8 段输出再校验，同样失败 → :2642 抛 502。

**修复方向**：owned_only 计划段数对齐模型稳定 8 段（L1 主修复）；校验容错超发截断（L2 保险，覆盖自有库存<8 的潜在同类失败）；规划/repair 提示词显式锁定条数（L3 提示词加固）。

---

## 一、落点 L1（主修复）：planner owned_only 放宽 owned_limit

**文件**：`hotspot_video_planner.py`
**位置**：`:805`（`owned_limit = 7 if target_duration_ms <= 60_000 else 8` 原定义点，单一定义点，就地替换）

**旧**：
```python
    owned_limit = 7 if target_duration_ms <= 60_000 else 8
```

**新**：
```python
    # 批20-补：owned_only 无热点 Hook 天然少一个槽位，但规划模型稳定输出 8 段分镜；
    # 计划仅 7 段会让 _planner_json 场景数硬校验失败（repair 同输出）→ 502。
    # owned_only 放宽到 8，与模型稳定行为对齐；含热点 Hook 的 mix3/hotspot_owned 保持原逻辑不变。
    owned_limit = 8 if chain_mode == "owned_only" else (7 if target_duration_ms <= 60_000 else 8)
```

**约束**：`chain_mode` 已是本函数 keyword-only 参数（:755），:805 在函数体内，直接引用即可。只改这一行，`owned = _diversify_owned_candidates(buffalo[:max(0, owned_limit - len(picks))] + picks)`(:850) 等下游逻辑不动。

---

## 二、落点 L2（保险）：`_planner_json` 超发容错截断

**文件**：`app.py`
**位置**：`:1787`（`_planner_json` 校验行）

**旧**：
```python
    if not title or not angle or not isinstance(scenes, list) or len(scenes) != expected_scenes:
        raise ValueError("内容规划模型缺少标题、角度或有效分镜")
```

**新**：
```python
    if not title or not angle or not isinstance(scenes, list) or len(scenes) < expected_scenes:
        raise ValueError("内容规划模型缺少标题、角度或有效分镜")
    scenes = scenes[:expected_scenes]
```

**语义**：分镜不足仍抛错（无法凭空补段，走既有 repair）；**超发**则确定性截断到 expected_scenes（模型多出的段是越界补写，丢弃即可）。对当前稳定输出（各链路恰为计划段数）是 **no-op**；只兜住"自有库存不足→计划<8→模型仍出 8"的潜在 502。截断发生在逐段校验循环之前，`voiceover_limits`/`hotspot_scene_count` 按截断后 index 对齐，无错位。

---

## 三、落点 L3（提示词加固）：规划 + repair 显式条数

**文件**：`app.py`

**L3-a｜规划 system prompt**：在 :2516（`hotspot_quota_line` 的 if/elif/else 块之后、`messages = [` 之前）定义：
```python
    scene_count_line = f"必须严格输出 {len(scenes)} 个分镜，分镜条数与 allowed_scenes 完全一致，不得多不得少。"
```
并在 :2525 行（`"每段必须提供新的具体信息。不得编造清关完成、时效、安全、覆盖率或客户结果。不得改变场景数量、不得推荐新素材。"`）之后插入 `+ scene_count_line`。

**L3-b｜repair system prompt**：在 :2577 行（`"保留既定分镜数量、顺序、事实边界和所有旁白字数上下限；"`）之后插入 `+ scene_count_line`（同一函数作用域，复用 L3-a 定义的变量，不重复定义）。

**约束**：`scene_count_line` 是 f-string，`len(scenes)` 取规划结果段数，随链路变化自动正确。只做插入，不改原句文案。

---

## 四、回归与自检（完成每项后贴实际输出）

1. **py_compile**：`python3 -m py_compile hotspot_video_planner.py app.py` 全过。
2. **静态实证**：
   - `grep -n "owned_limit" hotspot_video_planner.py` → :805 新三行分支
   - `grep -n "len(scenes) < expected_scenes\|scenes\[:expected_scenes\]" app.py` → :1787-1788
   - `grep -n "scene_count_line" app.py` → 定义 + 两处插入
3. **宿主 pytest**（如可跑）：planner/composition/video_generation 相关套件不回归。跑不了说明原因。
4. **浏览器实测（必做）**：
   - owned_only（60 秒 brief）出片不 502，成片段数 ≥7 段（理想 8 段）；
   - 回归确认 mix3、hotspot_owned 仍 8 段出片，三源/两源构成不变。

## 五、提交与纪律

1. 提交注明"批20 补1 owned_only 分镜数 502 修复"。
2. 只做上述三落点，不准顺手改别的、不准发明新函数名、不准重实现已验证正确部分。
3. 每处贴 `git diff -- <文件>` 实际输出；只回"我改了"不算数。
4. 与文档不符立即停下贴实际内容问，不猜。
