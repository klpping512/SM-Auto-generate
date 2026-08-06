"""小红书矩阵差异化守卫：只拦不排程，零表结构新增。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime

# 运营可调硬规则（改常量即可，不碰逻辑）
XHS_ACCOUNT_DAILY_MAX = 2
XHS_ASSET_MATRIX_MAX = 3


def content_fingerprint(title: str, body: str) -> str:
    """canonical 归一化后 sha256。同文案 → 同指纹。"""
    text = f"{title or ''}\n{body or ''}"
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[#\s]+", "", text)
    # 去掉符号类（含 emoji），保留文字与数字
    text = "".join(
        ch for ch in text
        if not unicodedata.category(ch).startswith("So")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _today_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _attachments_of(item: dict) -> list[dict]:
    raw = item.get("attachments")
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [a for a in parsed if isinstance(a, dict)] if isinstance(parsed, list) else []
    return []


def _asset_ids_of(item: dict) -> list[int]:
    ids: list[int] = []
    for att in _attachments_of(item):
        aid = att.get("asset_id")
        if aid is None:
            continue
        try:
            ids.append(int(aid))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))


def _list_today_published_xhs(db) -> list[dict]:
    """今日已成功发布的小红书条目（JOIN queue 拿账号与附件）。"""
    today = _today_local()
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT q.id, q.title, q.body, q.attachments, q.target_account_id,
                   pl.published_at
            FROM publish_log pl
            JOIN queue q ON q.id = pl.queue_id
            WHERE pl.platform = 'xiaohongshu'
              AND pl.status = 'published'
              AND date(pl.published_at) = date(?)
            """,
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def account_daily_count(db, account_id: int) -> int:
    """今日该账号已成功发布的小红书条数。"""
    if account_id is None:
        return 0
    today = _today_local()
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM publish_log pl
            JOIN queue q ON q.id = pl.queue_id
            WHERE pl.platform = 'xiaohongshu'
              AND pl.status = 'published'
              AND q.target_account_id = ?
              AND date(pl.published_at) = date(?)
            """,
            (int(account_id), today),
        ).fetchone()
    return int(row["cnt"] if row else 0)


def asset_matrix_count(db, asset_ids: list[int]) -> dict[int, int]:
    """今日已发布条目中，各 asset_id 出现过的不同账号数（同账号多篇计 1）。"""
    wanted = {int(a) for a in asset_ids if a is not None}
    if not wanted:
        return {}
    counts: dict[int, set[int]] = {aid: set() for aid in wanted}
    for row in _list_today_published_xhs(db):
        account_id = row.get("target_account_id")
        if account_id is None:
            continue
        try:
            account_id = int(account_id)
        except (TypeError, ValueError):
            continue
        for aid in _asset_ids_of(row):
            if aid in counts:
                counts[aid].add(account_id)
    return {aid: len(accounts) for aid, accounts in counts.items()}


def fingerprint_published_today(db, fingerprint: str) -> bool:
    for row in _list_today_published_xhs(db):
        if content_fingerprint(row.get("title") or "", row.get("body") or "") == fingerprint:
            return True
    return False


def check(item: dict, db, account_id: int | None) -> tuple[bool, str]:
    """返回 (允许, 人话原因)。任一规则触发即拦。"""
    if account_id is not None and account_daily_count(db, int(account_id)) >= XHS_ACCOUNT_DAILY_MAX:
        return False, f"单号今日已达上限 {XHS_ACCOUNT_DAILY_MAX}，请明日再发或换号"

    fp = content_fingerprint(item.get("title") or "", item.get("body") or "")
    if fingerprint_published_today(db, fp):
        return False, "同文案今日已在矩阵发布，请差异化后再发"

    asset_ids = _asset_ids_of(item)
    matrix = asset_matrix_count(db, asset_ids)
    for aid, n in matrix.items():
        if n >= XHS_ASSET_MATRIX_MAX:
            return False, f"素材 #{aid} 今日已达 {XHS_ASSET_MATRIX_MAX} 号上限，请换图后再发"

    return True, ""
