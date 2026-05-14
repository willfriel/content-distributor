import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from server import app
from models import db, SocialAccount
from integrations.youtube import _build_service
import requests as http_requests

YT_VIDEO_ID   = "sd8CHWwlFNo"
IG_MEDIA_ID   = "18201229702349559"
IG_GRAPH_BASE = "https://graph.instagram.com/v21.0"

with app.app_context():
    # --- Delete YouTube video ---
    yt_account = SocialAccount.query.filter_by(account_name="NorthEdge10", platform="youtube").first()
    if yt_account:
        try:
            svc = _build_service(yt_account.get_credentials())
            svc.videos().delete(id=YT_VIDEO_ID).execute()
            print(f"YouTube: deleted {YT_VIDEO_ID}")
        except Exception as e:
            print(f"YouTube delete failed: {e}")
    else:
        print("YouTube: NorthEdge10 account not found")

    # --- Delete Instagram post ---
    ig_account = SocialAccount.query.filter_by(account_name="northedge11", platform="instagram").first()
    if ig_account:
        creds = ig_account.get_credentials()
        resp = http_requests.delete(
            f"{IG_GRAPH_BASE}/{IG_MEDIA_ID}",
            params={"access_token": creds["access_token"]},
        )
        if resp.ok and resp.json().get("success"):
            print(f"Instagram: deleted {IG_MEDIA_ID}")
        else:
            print(f"Instagram delete failed: {resp.text}")
    else:
        print("Instagram: northedge11 account not found")
