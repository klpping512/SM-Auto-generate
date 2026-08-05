# Transnet 回填 H-hit（铁律 E）

> 生成时间：2026-08-05 16:18:57 CST
> 口径：tech_fail / materialization_retryable 从 H-hit 分母剔除；仅已下载且策展终态母片进分母。

## 抓取硬化结果

- scanned: 17
- downloadable: 10（目标 ≥10）
- retryable: 7
- accepted_media_ids: [875, 876, 877, 878, 879, 880, 881, 882, 883, 884]
- health_error: （无）

## 铁律 E 分台统计（Transnet NPA）

- mothers_scanned: 17
- tech_fail: 0
- prefiltered_skip: 0
- materialization_retryable: 7
- metadata_ready_inflight: 0
- curated_denominator: 10
- no_qualified_hooks: 6
- mothers_with_confirmed_hooks: 4
- confirmed_hooks: 8
- **H-hit_mother_share: 0.4**

### 母片明细（875–884）

| media_id | title | confirmed_hooks | 结果 |
|---:|---|---:|---|
| 875 | HIGHLIGHTS OF THE TNPA CHRISTENING CEREMONY | 3 | 确认Hook |
| 876 | Chief Marine Engineer Officer : Londeka Dlamini | 0 | no_qualified_hooks |
| 877 | From Corporate Office to Marine Operations | 0 | no_qualified_hooks |
| 878 | HIGHLIGHTS OF THE TNPA CHRISTENING CEREMONY | 2 | 确认Hook |
| 879 | Renewable Energy Specialist : Amanda Makgoga | 0 | no_qualified_hooks |
| 880 | Only TNPA Female Hydrographer Shares Her Journey | 1 | 确认Hook |
| 881 | All-women marine crew \| Port of Port Elizabeth | 0 | no_qualified_hooks |
| 882 | TNPA Christens two new launch boats… Cape Town | 0 | no_qualified_hooks |
| 883 | TNPA Transport Month | 2 | 确认Hook |
| 884 | TNPA OM Music video: Senza Kwenzeke | 0 | no_qualified_hooks |

## 预热

```json
{
  "status": "materialized",
  "candidate_count": 10,
  "requested_media_ids": [
    875,
    876,
    877,
    878,
    879,
    880,
    881,
    882,
    883,
    884
  ],
  "metadata_candidate_ids": [
    884,
    883,
    882,
    881,
    880,
    879,
    878,
    877,
    876,
    875
  ],
  "metadata": {
    "requested": 10,
    "ready": 10,
    "cached": 0,
    "failed": []
  },
  "decision_pool_ids": [
    884,
    883,
    882,
    881,
    880,
    879,
    878,
    877,
    876,
    875
  ],
  "intake": {
    "mode": "all_authorized_video_analysis",
    "source_metadata": {
      "requested": 10,
      "ready": 10,
      "cached": 0,
      "failed": []
    },
    "curator": "planner_text + critic"
  },
  "selected_media_ids": [
    884,
    883,
    882,
    881,
    880,
    879,
    878,
    877,
    876,
    875
  ],
  "materialized": [
    {
      "media_id": 884,
      "asset_id": 754,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 883,
      "asset_id": 759,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 2,
      "progress_detail": "内置模型已筛出 2 条精华 Hook 片段"
    },
    {
      "media_id": 882,
      "asset_id": 761,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 881,
      "asset_id": 766,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 880,
      "asset_id": 769,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 1,
      "progress_detail": "内置模型已筛出 1 条精华 Hook 片段"
    },
    {
      "media_id": 879,
      "asset_id": 774,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 878,
      "asset_id": 776,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 2,
      "progress_detail": "内置模型已筛出 2 条精华 Hook 片段"
    },
    {
      "media_id": 877,
      "asset_id": 783,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 876,
      "asset_id": 790,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 0,
      "progress_detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 875,
      "asset_id": 793,
      "download_status": "downloaded",
      "processing_status": "ready",
      "hook_count": 3,
      "progress_detail": "内置模型已筛出 3 条精华 Hook 片段"
    }
  ],
  "final_items": [
    {
      "media_id": 875,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 793,
      "confirmed_hooks": 3,
      "title": "HIGHLIGHTS OF THE TNPA CHRISTENING CEREMONY",
      "detail": "内置模型已筛出 3 条精华 Hook 片段"
    },
    {
      "media_id": 876,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 790,
      "confirmed_hooks": 0,
      "title": "Chief Marine Engineer Officer : Londeka Dlamini | #BreakingBoundaries",
      "detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 877,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 783,
      "confirmed_hooks": 0,
      "title": "From Corporate Office to Marine Operations | #BreakingBoundaries",
      "detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 878,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 776,
      "confirmed_hooks": 2,
      "title": "HIGHLIGHTS OF THE TNPA CHRISTENING CEREMONY",
      "detail": "内置模型已筛出 2 条精华 Hook 片段"
    },
    {
      "media_id": 879,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 774,
      "confirmed_hooks": 0,
      "title": "Renewable Energy Specialist : Amanda Makgoga | #BreakingBoundaries",
      "detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 880,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 769,
      "confirmed_hooks": 1,
      "title": "Only TNPA Female Hydrographer Shares Her Journey | #BreakingBoundaries",
      "detail": "内置模型已筛出 1 条精华 Hook 片段"
    },
    {
      "media_id": 881,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 766,
      "confirmed_hooks": 0,
      "title": "All-women marine crew | Port of Port Elizabeth",
      "detail": ""
    },
    {
      "media_id": 882,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 761,
      "confirmed_hooks": 0,
      "title": "TNPA Christens two new launch boats to boost marine operations in the Port of Cape Town.",
      "detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    },
    {
      "media_id": 883,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 759,
      "confirmed_hooks": 2,
      "title": "TNPA Transport Month",
      "detail": ""
    },
    {
      "media_id": 884,
      "download_status": "downloaded",
      "processing_status": "ready",
      "asset_id": 754,
      "confirmed_hooks": 0,
      "title": "TNPA OM Music video: Senza Kwenzeke",
      "detail": "镜头已分析，但内置模型未筛出可复用 Hook"
    }
  ]
}
```

## 原始 JSON

```json
{
  "fetch": {
    "channels": 1,
    "downloadable": 10,
    "retryable": 7,
    "scanned": 17,
    "accepted_media_ids": [
      875,
      876,
      877,
      878,
      879,
      880,
      881,
      882,
      883,
      884
    ],
    "source_health": [
      {
        "name": "YouTube · Transnet NPA",
        "status": "ok",
        "items": 10,
        "downloadable": 10,
        "retryable": 7,
        "scanned": 17,
        "error": ""
      }
    ]
  },
  "stats": {
    "publisher": "Transnet NPA",
    "since": "2026-08-02T08:18:57.030375+00:00",
    "mothers_scanned": 17,
    "tech_fail": 0,
    "prefiltered_skip": 0,
    "materialization_retryable": 7,
    "metadata_ready_inflight": 0,
    "curated_denominator": 10,
    "no_qualified_hooks": 6,
    "mothers_with_confirmed_hooks": 4,
    "confirmed_hooks": 8,
    "H_hit_mother_share": 0.4,
    "retryable_ids": [
      874,
      873,
      872,
      871,
      858,
      857,
      856
    ],
    "downloadable_media_ids": [
      884,
      883,
      882,
      881,
      880,
      879,
      878,
      877,
      876,
      875
    ],
    "tech_fail_samples": []
  }
}
```
