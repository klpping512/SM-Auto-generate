# 总指挥指令登记 —— 2026-08-06

> 前置诊断：见 memory `hotspot-server-stale-json-residual`。
> 第一动作：**重启 app**（08-04 13:03 旧进程未重启 → 08-05 修复生产未生效），重启后验三件事（5 台频道、duration 不覆盖、单条策展 JSON 可解析）。

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
- 可选：每周 dump 巡检（total>0 时报分类）——未默认开启。
