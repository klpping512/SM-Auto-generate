"""恢复存量素材中已由 OCR/描述明确识别到的 Buffalo 品牌露出标签。

本脚本只读既有镜头证据并写入 ``brand: Buffalo`` 标签；不会修改素材主分类，
也不会发起外部模型调用。可安全重复执行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asset_processing
import database as db


def main() -> None:
    db.init_db()
    result = db.backfill_visible_brand_tags("Buffalo", asset_processing.BUFFALO_BRAND_MARKERS)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
