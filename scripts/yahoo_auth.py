"""
One-time Yahoo OAuth2 handshake.

    python scripts/yahoo_auth.py

After running, the token is cached and the app auto-refreshes it.
"""
import base64
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

key    = os.getenv("YAHOO_CONSUMER_KEY")
secret = os.getenv("YAHOO_CONSUMER_SECRET")
token_file = os.getenv("YAHOO_TOKEN_FILE", ".yahoo_token.json")
redirect_uri = "https://example.com"

if not key or not secret:
    print("ERROR: YAHOO_CONSUMER_KEY and YAHOO_CONSUMER_SECRET must be set in .env")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)

# ── Step 1: Build authorization URL ──────────────────────────────────────────
params = {
    "client_id":     key,
    "redirect_uri":  redirect_uri,
    "response_type": "code",
}
auth_url = "https://api.login.yahoo.com/oauth2/request_auth?" + urlencode(params)

print()
print("=" * 60)
print("  Yahoo Fantasy — One-time Authorization")
print("=" * 60)
print()
print("Opening browser. If it doesn't open, copy this URL manually:")
print()
print(" ", auth_url)
print()

webbrowser.open(auth_url)

# ── Step 2: Get the code from the redirect URL ────────────────────────────────
print("After you click Allow, your browser will redirect to example.com.")
print("That page will load normally — ignore what it says.")
print()
print("Look at your browser's address bar. The URL will look like:")
print("  https://example.com/?code=XXXXXXXXXX")
print()

raw = input("Paste that FULL URL (or just the code after ?code=) here: ").strip()

# Accept either the full URL or just the code
if raw.startswith("http"):
    parsed = urlparse(raw)
    code = parse_qs(parsed.query).get("code", [""])[0]
else:
    code = raw

if not code:
    print("ERROR: could not extract a code. Try again.")
    sys.exit(1)

# ── Step 3: Exchange code for access + refresh tokens ────────────────────────
print()
print("Exchanging code for token...")

credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
resp = requests.post(
    "https://api.login.yahoo.com/oauth2/get_token",
    headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": redirect_uri,
    },
    timeout=15,
)

if not resp.ok:
    print(f"ERROR: token exchange failed ({resp.status_code}): {resp.text}")
    sys.exit(1)

token_data = resp.json()

# ── Step 4: Save in yahoo_oauth-compatible format ─────────────────────────────
token_record = {
    "consumer_key":    key,
    "consumer_secret": secret,
    "access_token":    token_data["access_token"],
    "refresh_token":   token_data["refresh_token"],
    "token_type":      token_data.get("token_type", "bearer"),
    "expires_in":      token_data.get("expires_in", 3600),
    "token_time":      time.time(),
}
Path(token_file).write_text(json.dumps(token_record, indent=2))

print()
print("✓ Token saved to:", token_file)
print("  You can now open http://localhost:5001/fantasy")
