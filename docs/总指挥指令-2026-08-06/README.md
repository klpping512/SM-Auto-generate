# 总指挥指令登记 —— 2026-08-06

> 前置诊断：见 memory `hotspot-server-stale-json-residual`。
> 第一动作：**重启 app**（08-04 13:03 旧进程未重启 → 08-05 修复生产未生效），重启后验三件事（5 台频道、duration 不覆盖、单条策展 JSON 可解析）。
> **收束存档**：[全套链路收束盘点](链路收束盘点-2026-08-06.md)——08-04/05/06 三批改动全链核验，链路设计已闭环。

## 指令列表

| # | 指令 | 状态 | 说明 |
|---|------|------|------|
| 1 | 策展 JSON 失败：原始返回 dump + 一次性重试 | **已执行** | 落库诊断 + max_calls=2 一次性重试（use_cache=False）；保留频道 10 条 requeue 后全出合法 JSON（0 hook）；诊断表暂无现场（未触发解析失败）。文件：`策展JSON失败-原始返回dump+一次性重试-Cursor执行指令.md` |
| 2 | za-stock 受控开闸 + 文案门禁兜底 + cap→20000 | **已执行+验收过** | `za_stock_license` 受控进池 + 口播强制安全模板 + cap 20000；清关验收 category_match↑、za customs 可用资产 24。**总指挥独立复核**（复跑真实 `diagnose_owned_matching`）：category_match=351 / after_dedup=155、category_inventory={facility:53,brand:3} 无 customs、verdict=healthy；新测 4 条，全量 811 passed。commit `46ea006`。文件：`za-stock-受控开闸-Cursor执行指令.md` |
| 3 | za-stock 素材库展示层修复 | **已执行+验收过** | source_label 三元（免版权素材）+ assets.html 拆两节（ownedAssets/materialSections 独立守卫 + 空态兜底）+ 蓝色 za-stock pill tag + 新测试；总指挥复核打回后补齐空态回归与规格测试，复验通过。文件：`za-stock-素材库展示层修复-Cursor执行指令.md`。 |
| 4 | 入库选片 JSON 加固 | **已执行（待验收）** | selection/audit 两段：parse 升级 `_extract_json`（剥 think/平衡 JSON，行级校验未动）+ 一次性重试（use_cache=False）+ `hook_intake_diagnostics` 落库（job_id+stage）+ dump 定性脚本；budget 单点 max_calls=2。全量 817 passed（8 存量 UI 失败不变）；dry_run 真跑 retries 全 0。文件：`入库选片JSON加固-Cursor执行指令.md`。 |

## 拍板（2026-08-06）

- **不调 `max_output_tokens=1000`，先观察。** 依据：诊断表空表无截断样本；调大会同步放大 `required_output_budget` 成本；新机制已能接住下次失败。触发：偶发失败 → dump 分类；截断为主再开下一条指令。
- **za-stock 受控开闸 + cap→20000。** 依据：za-stock 是受控来源（attribution 已写"通用背景/非南非现场/非Buffalo能力"，brand 标签为空永远排真素材后），画面补洞；口播由 `apply_overclaim_guard` 对 za-stock 来源一律强制安全模板，不构成 Buffalo 能力证明。cap 2000 只覆盖 asset_id≤314，是截断真 bug，提到 20000 覆盖全库约 1.9 万段。
- commits `94fe241` + `9391552`：已推送（push 不动运行态；新码已在 PID 39848）。

## 拍板（2026-08-06）

- **不调 `max_output_tokens=1000`，先观察。** 依据：诊断表空表无截断样本；调大会同步放大 `required_output_budget` 成本；新机制已能接住下次失败。触发：偶发失败 → dump 分类；截断为主再开下一条指令。
- commits `94fe241` + `9391552`：已推送（push 不动运行态；新码已在 PID 39848）。

## 待办（残余 / 另立）

- 914 下载 300s 超时（BDTV 演播室片，低价值）暂挂。
- ~~入库选片路径（hotspot_hook_intake `_parse_selections`/`_parse_audit`）JSON 加固另立项目~~：已执行（指令 #4），诊断表 `hook_intake_diagnostics` 已就位，后续用 `scripts/dump_hook_intake_diagnostics.py` 采样定性。
- ~~za-stock 管线~~：pull+ingest+定点处理+**受控开闸已执行**；清关诊断 `after_dedup` 含 za-stock，customs 可用资产 24。
- 可选：每周 dump 巡检（total>0 时报分类）——未默认开启。

## za-stock 定点处理验收（2026-08-06）

- `process_za_stock.py`：**61/61 ok，fail=0**；assets `ready`；primary 保持 manual（customs 24 / facility 22 / delivery 15）；产出 segments 142。
- **owned-matching 仍看不到 customs**：两道闸——① `_is_buffalo_usable_source` 排除 `za_stock_license`（合规：免版权不得当 Buffalo 自有证明）；② `list_asset_segments` 硬上限 2000 + 按 asset_id 升序，扫描到 max asset_id≈299，轮不到 866+。
- **拍板（2026-08-06）**：**受控开闸** + **cap→20000** 已执行并复验：`funnel.category_match=351`、`after_dedup=155`、za customs 可用资产 24；`category_inventory` 仍仅为被踢类别（facility/brand），不含 customs。
