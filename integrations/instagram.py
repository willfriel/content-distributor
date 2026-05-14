import requests


GRAPH_BASE = "https://graph.instagram.com/v21.0"


def upload_reel(credentials: dict, video_url: str, caption: str = "") -> dict:
    """Upload a video as a Reel to an Instagram Business account via Graph API."""
    access_token = credentials["access_token"]
    ig_user_id = credentials["instagram_user_id"]

    # Step 1: Create a media container
    container_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        params={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=60,
    )
    container_resp.raise_for_status()
    container_id = container_resp.json()["id"]

    # Step 2: Poll until the container is ready
    _wait_for_container(ig_user_id, container_id, access_token)

    # Step 3: Publish
    publish_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=30,
    )
    publish_resp.raise_for_status()
    media_id = publish_resp.json()["id"]

    return {"media_id": media_id, "url": f"https://www.instagram.com/p/{media_id}/"}


def update_bio(credentials: dict, bio: str) -> dict:
    access_token = credentials["access_token"]
    ig_user_id   = credentials["instagram_user_id"]
    resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}",
        params={"access_token": access_token},
        data={"biography": bio},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _wait_for_container(ig_user_id: str, container_id: str, access_token: str, max_attempts: int = 20):
    import time

    for _ in range(max_attempts):
        resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram media container failed: {resp.json()}")
        time.sleep(10)

    raise TimeoutError("Instagram media container did not finish processing in time")
