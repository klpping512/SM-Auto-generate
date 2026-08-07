# 总指挥指令登记 — 2026-08-07

| # | 批次 | 状态 | 摘要 |
|---|---|---|---|
| 16 | 批16 内容资产库面修复（Hook 分区 + 媒体格去噪 + 停 RSS 图片灌入） | **已执行（opencode 落地）+ 验收 1-7 完成** | 治 08-07 三件事：①"几乎全部无 Hook"墙 = 794 张 RSS 图片噪音卡 + 288 张模型正常拒收卡 → 媒体格默认排除 image + 默认"全部素材"视图；②"混进 buffalo 素材" = 批12 generic_logistics 常青开场池 14 条（buffalo 自有 8 + za-stock 6）以"热点 Hook"卡混在新闻 Hook 区 → 按 hook_kind 拆分独立"常青开场池"区块 + 打标；③生产链链路没坏（匹配链功能隔离已核验），坏在库面 → 纯展示层修复。验收：API 口径 98（84 timely + 14 generic）✅；停灌验证（image 跳过/video_link 保留/794 存量未删）✅；pytest 全量 885 passed / 8 存量基线失败不变 ✅；app 已重启（PID 85567）。文件：`批16-内容资产库面修复-qcoder执行指令.md`。 |
| 17 | 批17 热点 Hook 生产打标签 + 时效入链（卡片三标签 + 匹配新鲜度 + published_at 入链） | **已产出，待 qcoder 执行；前置=批16 先落地** | 落实"分析好的热点素材直接打标签、保时效性/效率"：A 卡片打标签（时效徽标今日~超30天/常青 + 场景中文 chips + 来源标签，`_decorate_hotspot_event` 补父热点 published_at 与 source_label）；B 时效入匹配链（timely_event 加分 <24h+8/<3d+5/<7d+2/≥30d−3，排序键从乱序字符串 published_at 改为 published_ts）；C published_at 入链修复（下载路径捕获 upload_date 前进回填 + 新脚本回填 422 条 youtube 空日期旧热点，含 104 合格 Hook 父热点）。实测：154 confirmed timely clips 中 151 父热点无日期；RSS 源 303 条 RFC2822、youtube 源全空（channel scan 用 --flat-playlist）。文件：`批17-热点Hook生产打标签与时效入链-qcoder执行指令.md`。 |
