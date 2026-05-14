import requests


TIKTOK_BASE = "https://open.tiktokapis.com/v2"


def upload_video(credentials: dict, video_url: str, title: str = "", description: str = "") -> dict:
    """
    Upload a video to TikTok via the Content Posting API (URL-based pull upload).
    Requires: access_token, open_id in credentials.
    TikTok API access requires approval — if not approved, returns a manual_required flag.
    """
    access_token = credentials.get("access_token")
    if not access_token:
        return {"manual_required": True, "reason": "No TikTok access token configured"}

    # Step 1: Initialize upload
    init_resp = requests.post(
        f"{TIKTOK_BASE}/post/publish/video/init/",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "post_info": {
                "title": title[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        },
        timeout=30,
    )

    if init_resp.status_code == 401:
        return {"manual_required": True, "reason": "TikTok token expired or invalid"}

    if init_resp.status_code == 403:
        return {
            "manual_required": True,
            "reason": "TikTok Content Posting API not approved for this app — upload manually",
        }

    init_resp.raise_for_status()
    data = init_resp.json().get("data", {})
    publish_id = data.get("publish_id")

    return {"publish_id": publish_id, "status": "processing"}


def get_upload_status(credentials: dict, publish_id: str) -> dict:
    access_token = credentials.get("access_token")
    resp = requests.post(
        f"{TIKTOK_BASE}/post/publish/status/fetch/",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})
