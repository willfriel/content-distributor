import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv()
from server import app
from models import SocialAccount
import requests as http_requests

IG_MEDIA_ID   = "18201229702349559"
IG_GRAPH_BASE = "https://graph.instagram.com/v21.0"

with app.app_context():
    ig_account = SocialAccount.query.filter_by(account_name="northedge11", platform="instagram").first()
    if ig_account:
        creds = ig_account.get_credentials()
        resp = http_requests.delete(
            f"{IG_GRAPH_BASE}/{IG_MEDIA_ID}",
            params={"access_token": creds["access_token"]},
        )
        print(f"Instagram response: {resp.status_code} {resp.text}")
    else:
        print("northedge11 Instagram account not found")
