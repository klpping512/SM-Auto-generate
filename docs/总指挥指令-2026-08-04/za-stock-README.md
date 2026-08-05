# za-stock 免版权素材管线 — 怎么跑

## 1. 设 key

在项目 `.env` 写入（缺哪个源就跳过哪个）：

```
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
LOCAL_ASSET_ROOT=/path/to/素材根   # 可选；缺省 ~/Desktop/视频&图片素材
```

代理复用 `SA_HOTSPOT_PROXY`（或 `SA_YOUTUBE_PROXY`）。

## 2. 首轮优先填洞

```bash
# 只下 customs / facility / delivery（别先抓风景）
python3 scripts/pull_za_stock.py --category customs facility delivery --per-query 3

# 人工快扫 customs 目录后入库
python3 scripts/ingest_za_stock.py
```

下载落点：`<LOCAL_ASSET_ROOT>/za-stock/<category>/za_<category>_<source>_<id>.mp4` + 同名 `.json` sidecar。

## 3. 验收

```bash
# 服务起来后
curl -s 'http://127.0.0.1:8080/api/diagnostics/owned-matching?topic=清关' | python3 -m json.tool
```

预期：customs 候选从 0 变为正。再扫 `topic=末端` / `topic=运输` 确认别的闸没乱。

## 4. provenance 铁律

- 免版权片 = 通用背景，不得宣称南非现场
- 不得暗示 Buffalo 自有能力
- customs 口播只说「备货待清关」，不宣称已清关
