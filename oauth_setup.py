"""
oauth_setup.py - AmoCRM OAuth2 Initial Setup Script
Run this ONCE to get the initial refresh_token for your .env file.

Usage:
    1. Fill CLIENT_ID, CLIENT_SECRET, AMO_DOMAIN, REDIRECT_URI in .env
    2. Run: python oauth_setup.py
    3. Open the printed URL in your browser
    4. Authorize the app in AmoCRM
    5. You will be redirected to REDIRECT_URI?code=XXXX
    6. Copy the 'code' parameter from the URL
    7. Paste it when prompted
    8. Script prints refresh_token - copy it to .env as AMO_REFRESH_TOKEN
"""

import os
import json
import webbrowser
import aiohttp
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Configuration - read from .env or set here
AMO_DOMAIN    = os.environ.get("AMO_DOMAIN", "yourname.amocrm.ru")
CLIENT_ID     = os.environ.get("AMO_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AMO_CLIENT_SECRET", "")
REDIRECT_URI  = os.environ.get("AMO_REDIRECT_URI", "https://yoursite.com/oauth/callback")


def get_auth_url() -> str:
    """Build the OAuth2 authorization URL for AmoCRM."""
    params = (
        f"?client_id={CLIENT_ID}"
        f"&state=mugon_setup"
        f"&mode=post_message"
        f"&redirect_uri={REDIRECT_URI}"
    )
    return "https://www.amocrm.ru/oauth" + params


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://{AMO_DOMAIN}/oauth2/access_token",
            json={
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  REDIRECT_URI,
            }
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Token exchange failed {resp.status}: {data}")
            return data


async def main():
    print("=" * 60)
    print("MUGON HR Bot - AmoCRM OAuth2 Setup")
    print("=" * 60)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Set AMO_CLIENT_ID and AMO_CLIENT_SECRET in .env first")
        return

    auth_url = get_auth_url()
    print(f"\n1. Opening authorization URL...")
    print(f"   {auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        print("   (Could not open browser - copy URL above manually)")

    print("2. Authorize the app in AmoCRM")
    print("3. You will be redirected to:", REDIRECT_URI)
    print("4. Copy the 'code' from the redirect URL\n")

    code = input("Paste authorization code here: ").strip()

    if not code:
        print("ERROR: No code entered")
        return

    print("\nExchanging code for tokens...")
    try:
        tokens = await exchange_code(code)
    except Exception as e:
        print(f"ERROR: {e}")
        return

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in    = tokens.get("expires_in", 86400)

    print("\n" + "=" * 60)
    print("SUCCESS! Copy this to your .env file:")
    print("=" * 60)
    print(f"AMO_REFRESH_TOKEN={refresh_token}")
    print()
    print(f"Access token expires in {expires_in}s - bot auto-refreshes it every 23h")
    print("=" * 60)

    # Backup tokens to file
    with open("tokens.json", "w") as f:
        json.dump({
            "access_token":  access_token,
            "refresh_token": refresh_token,
            "expires_in":    expires_in,
        }, f, indent=2)
    print("\nBackup saved to tokens.json")
    print("IMPORTANT: Add tokens.json to .gitignore!")


if __name__ == "__main__":
    asyncio.run(main())
