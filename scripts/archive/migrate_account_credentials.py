"""One-shot: encrypt legacy plaintext accounts.credentials rows to enc:v1:.

Safe to re-run. Does not rotate keys. Requires cryptography + existing key file
or CREDENTIAL_ENCRYPTION_KEY.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db
from credential_store import encrypt_credentials, looks_encrypted


def main() -> None:
    db.init_db()
    before_plain = 0
    before_enc = 0
    migrated = 0
    skipped_empty = 0
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, credentials FROM accounts").fetchall()
        for row in rows:
            account_id = int(row["id"])
            stored = str(row["credentials"] or "{}")
            if looks_encrypted(stored):
                before_enc += 1
                continue
            before_plain += 1
            stripped = stored.strip()
            if stripped in ("", "{}", "null", "None"):
                # Still encrypt empty JSON so at-rest format is uniform.
                pass
            encrypted = encrypt_credentials(stored)
            if encrypted == stored:
                continue
            conn.execute(
                "UPDATE accounts SET credentials = ? WHERE id = ?",
                (encrypted, account_id),
            )
            migrated += 1
            if stripped in ("", "{}", "null", "None"):
                skipped_empty += 1
        conn.commit()
        after_rows = conn.execute("SELECT credentials FROM accounts").fetchall()
    after_plain = sum(1 for r in after_rows if not looks_encrypted(str(r["credentials"] or "{}")))
    after_enc = sum(1 for r in after_rows if looks_encrypted(str(r["credentials"] or "{}")))
    print(
        json.dumps(
            {
                "before_plaintext": before_plain,
                "before_encrypted": before_enc,
                "migrated": migrated,
                "empty_or_placeholder_encrypted": skipped_empty,
                "after_plaintext": after_plain,
                "after_encrypted": after_enc,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if after_plain:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
