import requests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


def fetch_youtube_metrics(creds_dict: dict, video_id: str) -> dict:
    creds = Credentials(
        token=creds_dict.get("access_token"),
        refresh_token=creds_dict.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_dict.get("client_id"),
        client_secret=creds_dict.get("client_secret"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    svc = build("youtube", "v3", credentials=creds)
    r = svc.videos().list(part="statistics", id=video_id).execute()
    items = r.get("items", [])
    if not items:
        return {}
    s = items[0].get("statistics", {})
    views = int(s.get("viewCount", 0))
    return {
        "views":    views,
        "likes":    int(s.get("likeCount", 0)),
        "comments": int(s.get("commentCount", 0)),
        "shares":   0,
        "saves":    int(s.get("favoriteCount", 0)),
        "reach":    views,
    }


def fetch_instagram_metrics(creds_dict: dict, media_id: str) -> dict:
    token = creds_dict.get("access_token")
    r = requests.get(
        f"https://graph.instagram.com/v21.0/{media_id}/insights",
        params={
            "metric":       "plays,reach,likes,comments,shares,saved",
            "access_token": token,
        },
        timeout=30,
    )
    if not r.ok:
        # Insights unavailable (< 24h after posting or wrong media type) — fall back to basic fields
        r2 = requests.get(
            f"https://graph.instagram.com/v21.0/{media_id}",
            params={"fields": "like_count,comments_count", "access_token": token},
            timeout=30,
        )
        if not r2.ok:
            return {}
        d = r2.json()
        return {
            "views": 0, "likes": d.get("like_count", 0),
            "comments": d.get("comments_count", 0),
            "shares": 0, "saves": 0, "reach": 0,
        }

    metrics = {}
    for item in r.json().get("data", []):
        name = item["name"]
        val  = (item.get("values", [{}])[0].get("value", 0)
                if "values" in item else item.get("value", 0))
        metrics[name] = val

    return {
        "views":    metrics.get("plays", metrics.get("impressions", 0)),
        "likes":    metrics.get("likes", 0),
        "comments": metrics.get("comments", 0),
        "shares":   metrics.get("shares", 0),
        "saves":    metrics.get("saved", 0),
        "reach":    metrics.get("reach", 0),
    }
