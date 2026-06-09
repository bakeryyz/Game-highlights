"""
One-time Yahoo OAuth2 handshake.
Run this once to authorize the app and cache the token:

    python scripts/yahoo_auth.py

Requires YAHOO_CONSUMER_KEY, YAHOO_CONSUMER_SECRET, and YAHOO_TOKEN_FILE in .env.
"""
import json
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

key = os.getenv("YAHOO_CONSUMER_KEY")
secret = os.getenv("YAHOO_CONSUMER_SECRET")
token_file = os.getenv("YAHOO_TOKEN_FILE", ".yahoo_token.json")

if not key or not secret:
    print("ERROR: YAHOO_CONSUMER_KEY and YAHOO_CONSUMER_SECRET must be set in .env")
    print("Register a read-only app at: https://developer.yahoo.com/apps/create/")
    sys.exit(1)

# Write consumer credentials to the token file so yahoo_oauth can read them
creds = {"consumer_key": key, "consumer_secret": secret}
Path(token_file).write_text(json.dumps(creds))
print(f"Wrote credentials to {token_file}")
print("Opening browser for Yahoo authorization...")

from yahoo_oauth import OAuth2
oauth = OAuth2(None, None, from_file=token_file)

if oauth.token_is_valid():
    print("Authorization successful! Token cached to:", token_file)
    print("You can now run the app.")
else:
    print("Authorization may not have completed. Check the token file.")
