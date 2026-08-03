"""配置 Twitter 账号凭据。运行: python setup_twitter.py

将下方占位符替换为本地凭据；切勿把真实 Client ID / Token 提交到仓库。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import get_accounts, create_account, update_account_credentials

# ============ 从环境变量或下方占位符填入凭据 ============
CLIENT_ID = os.environ.get("TWITTER_CLIENT_ID", "YOUR_TWITTER_CLIENT_ID")
ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
REFRESH_TOKEN = os.environ.get("TWITTER_REFRESH_TOKEN", "YOUR_REFRESH_TOKEN")
CLIENT_SECRET = os.environ.get("TWITTER_CLIENT_SECRET", "")
# ======================================================


def setup():
    """配置或更新 Twitter 账号。"""
    if CLIENT_ID.startswith("YOUR_") or ACCESS_TOKEN.startswith("YOUR_"):
        raise SystemExit(
            "请先设置 TWITTER_CLIENT_ID / TWITTER_ACCESS_TOKEN / TWITTER_REFRESH_TOKEN "
            "环境变量，或编辑本脚本中的占位符（不要提交真实凭据）。"
        )
    accounts = get_accounts("twitter")

    creds = {
        "client_id": CLIENT_ID,
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
        "token_obtained_at": __import__("time").time(),
        "expires_in": 7200,
    }
    if CLIENT_SECRET:
        creds["client_secret"] = CLIENT_SECRET

    if accounts:
        account = accounts[0]
        account_id = account["account_id"]
        print(f"更新已有 Twitter 账号: {account['name']} (account_id={account_id})")
        update_account_credentials(account_id, json.dumps(creds))
        print("凭据已更新")
    else:
        print("创建新 Twitter 账号...")
        create_account(
            platform="twitter",
            name="X/Twitter 主账号",
            account_id="twitter_main",
            config_summary="X API v2 OAuth2.0",
            credentials=json.dumps(creds),
        )
        print("凭据已创建")
    print("Twitter 账号配置完成（请勿把真实凭据提交到仓库）")


if __name__ == "__main__":
    setup()
