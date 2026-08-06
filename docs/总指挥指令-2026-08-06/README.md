# 总指挥指令登记 —— 2026-08-06

> 前置诊断：见 memory `hotspot-server-stale-json-residual`。
> 第一动作：**重启 app**（08-04 13:03 旧进程未重启 → 08-05 修复生产未生效），重启后验三件事（5 台频道、duration 不覆盖、单条策展 JSON 可解析）。
> **收束存档**：[全套链路收束盘点](链路收束盘点-2026-08-06.md)——08-04/05/06 三批改动全链核验，链路设计已闭环。

## 指令列表

| # | 指令 | 状态 | 说明 |
|---|------|------|------|
| 1 | 策展 JSON 失败：原始返回 dump + 一次性重试 | **已执行** | 落库诊断 + max_calls=2 一次性重试（use_cache=False）；保留频道 10 条 requeue 后全出合法 JSON（0 hook）；诊断表暂无现场（未触发解析失败）。文件：`策展JSON失败-原始返回dump+一次性重试-Cursor执行指令.md` |

## 拍板（2026-08-06）

- **不调 `max_output_tokens=1000`，先观察。** 依据：诊断表空表无截断样本；调大会同步放大 `required_output_budget` 成本；新机制已能接住下次失败。触发：偶发失败 → dump 分类；截断为主再开下一条指令。
- commits `94fe241` + `9391552`：已推送（push 不动运行态；新码已在 PID 39848）。

## 待办（残余 / 另立）

- 914 下载 300s 超时（BDTV 演播室片，低价值）暂挂。
- 入库选片路径（hotspot_hook_intake `_parse_selections`/`_parse_audit`）JSON 加固另立项目。
- za-stock 管线（填 customs 缺口）：**2026-08-06 Pexels+Pixabay 两把 key 已写入 .env**，待跑 `pull_za_stock.py --category customs facility delivery` → `ingest_za_stock.py` → `/api/diagnostics/owned-matching?topic=清关` 验收 customs 候选 0→正（ingest 后重启 app）。
- 可选：每周 dump 巡检（total>0 时报分类）——未默认开启。

## za-stock 定点处理验收（2026-08-06）

- `process_za_stock.py`：**61/61 ok，fail=0**；assets `ready`；primary 保持 manual（customs 24 / facility 22 / delivery 15）；产出 segments 142。
- **owned-matching 仍看不到 customs**：两道闸——① `_is_buffalo_usable_source` 排除 `za_stock_license`（合规：免版权不得当 Buffalo 自有证明）；② `list_asset_segments` 硬上限 2000 + 按 asset_id 升序，扫描到 max asset_id≈299，轮不到 866+。
- 假设放行 za_stock：customs 资产可进 category_match **24**。待总指挥拍板是否开闸（及是否抬高诊断扫描上限）。
