"""
oauth_setup.py - AmoCRM OAuth2 first-time setup
Run ONCE to get initial refresh_token for .env

Steps:
1. Create integration: AmoCRM Settings -> Integrations -> Add
2. Fill AMO_CLIENT_ID, AMO_CLIENT_SECRET in .env
3. Run: python oauth_setup.py
4. Open the URL in browser, authorize
5. Copy the code from redirect URL
6. Paste code -> get refresh_token
7. Save refresh_token to AMO_REFRESH_TOKEN in .env
"""
import os
import json
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

DOMAIN = os.environ.get("AMO_DOMAIN", "eriarwork2201.amocrm.ru")
CLIENT_ID = os.environ["AMO_CLIENT_ID"]
CLIENT_SECRET = os.environ["AMO_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("AMO_REDIRECT_URI", "https://your-server.com/oauth")


def get_auth_url() -> str:
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": "mugon_hr_bot",
        "mode": "popup",
    })
    return f"https://{DOMAIN}/oauth?{params}"


def exchange_code(code: str) -> dict:
    response = requests.post(
        f"https://{DOMAIN}/oauth2/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
    )
    return response.json()


if __name__ == "__main__":
    url = get_auth_url()
    print("=" * 60)
    print("MUGON HR Bot — AmoCRM OAuth2 Setup")
    print("=" * 60)
    print(f"Open in browser:\n{url}")
    print()
    code = input("Paste the authorization code: ").strip()
    tokens = exchange_code(code)
    if "access_token" in tokens:
        rt = tokens.get("refresh_token", "")
        at = tokens.get("access_token", "")[:50]
        print(f"\nSuccess!")
        print(f"access_token:  {at}...")
        print(f"refresh_token: {rt}")
        print(f"\nAdd to .env:")
        print(f"AMO_REFRESH_TOKEN={rt}")
    else:
        print(f"Error: {tokens}")
