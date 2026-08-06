# 总指挥指令登记 —— 2026-08-06

> 前置诊断：见 memory `hotspot-server-stale-json-residual`。
> 第一动作：**重启 app**（08-04 13:03 旧进程未重启 → 08-05 修复生产未生效），重启后验三件事（5 台频道、duration 不覆盖、单条策展 JSON 可解析）。

## 指令列表

| # | 指令 | 状态 | 说明 |
|---|------|------|------|
| 1 | 策展 JSON 失败：原始返回 dump + 一次性重试 | **已执行** | 落库诊断 + max_calls=2 一次性重试（use_cache=False）；保留频道 10 条 requeue 后全出合法 JSON（0 hook）；诊断表暂无现场（未触发解析失败）。文件：`策展JSON失败-原始返回dump+一次性重试-Cursor执行指令.md` |

## 待办（诊断产出，后续拍板）

- ~~重启 app 后 requeue 保留频道 10 条 JSON 失败母片（eNCA 7 + BDTV 2 + CNBC 1，含 909/913/914）~~：已按 media-id 白名单 requeue（未跑全量 `--requeue-uncurated`，避免误触 SABC）；10/10 → `ready` + `no_qualified_hooks`。
- dump 分类计数（最近 30）：**空表**（total=0）。本轮重跑首调即合法 JSON，未写入诊断行；截断 vs 空返回需等后续偶发失败再定性。SABC 70 + 已下架频道残余不在本轮。
- 914 下载 300s 超时（BDTV 演播室片，低价值）暂挂。
- 入库选片路径（hotspot_hook_intake `_parse_selections`/`_parse_audit`）JSON 加固另立项目。
