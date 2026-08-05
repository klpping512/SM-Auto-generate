# audit 模型 A/B（P2）——待跑，未改代码

> 日期：2026-08-05
> 状态：**未执行**（按总指挥指令：先 A/B，确认不掉质后再改 audit 路由）

## 目标

仅针对 `hotspot_hook_curator` 的 audit/预审一路，评估从 `mimo-v2.5-pro`（推理）切到更快非推理模型是否掉质。真正的 Hook 策展（`planner_text`）保持不动。

## 方法（待执行）

1. 固定同一组已下载母片（建议 ≥20 条，含边界案例：演播室、港口现场、音乐片边缘标题）。
2. Run A：audit 用当前推理模型，记录 `confirmed_hooks` 集合（event identity / start_ms/end_ms）。
3. Run B：audit 临时指向快速模型，其余不变，记录同样集合。
4. 差异 = symmetric difference / union；阈值：差异 <5% 视为不掉质。
5. 人工抽看被快速模型放过/砍掉的边界案例。

## 结果（空，待填）

| 指标 | Run A（推理） | Run B（快速） |
|---|---|---|
| mothers | — | — |
| confirmed_hooks | — | — |
| 集合差异率 | — | — |

边界案例人工结论：—

## 拍板门槛

- 集合差异 <5%，且边界案例人工 OK → 允许 `SA_HOTSPOT_AUDIT_FAST=1` 合入。
- 否则回退，保持 audit 走推理模型。

## 备注

本文件仅为 A/B 占位；**尚未改任何 audit 路由代码**。环境开关 `SA_HOTSPOT_AUDIT_FAST` 已写入 `.env.example`（默认 0），实现待绿灯后再做。
