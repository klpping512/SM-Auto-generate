"""配置 Twitter 账号凭据。运行: python setup_twitter.py"""
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from database import get_accounts, create_account, update_account_credentials, get_conn

# ============ 在这里填入你的凭据 ============
CLIENT_ID = "hbxvbRV6z8SvJYYX3PQ3zkHeWTbvaHlYvno9eMsUUtRLH_4Bf"
ACCESS_TOKEN = "填入你的 Access Token"
REFRESH_TOKEN = "填入你的 Refresh Token"
# CLIENT_SECRET 如果有的话填，没有就留空
CLIENT_SECRET = ""
# ============================================


def setup():
    """配置或更新 Twitter 账号。"""
    # 检查是否已有 Twitter 账号
    accounts = get_accounts("twitter")

    creds = {
        "client_id": CLIENT_ID,
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
        "token_obtained_at": __import__("time").time(),
        "expires_in": 7200,  # 2 小时
    }
    if CLIENT_SECRET:
        creds["client_secret"] = CLIENT_SECRET

    if accounts:
        # 更新已有账号
        account = accounts[0]
        account_id = account["account_id"]
        print(f"更新已有 Twitter 账号: {account['name']} (account_id={account_id})")
        update_account_credentials(account_id, json.dumps(creds))
    else:
        # 创建新账号
        print("创建新 Twitter 账号...")
        create_account(
            platform="twitter",
            name="X/Twitter 主账号",
            account_id="twitter_main",
            config_summary="X API v2 OAuth2.0",
            credentials=json.dumps(creds),
        )

    print("✅ Twitter 账号配置完成！")
    print(f"   Client ID: {CLIENT_ID[:20]}...")
    print(f"   Access Token: {ACCESS_TOKEN[:20]}...")
    print(f"   Refresh Token: {REFRESH_TOKEN[:20]}...")
    if CLIENT_SECRET:
        print(f"   Client Secret: {CLIENT_SECRET[:10]}...")
    else:
        print("   Client Secret: (无，使用 PKCE 模式)")


if __name__ == "__main__":
    setup()
