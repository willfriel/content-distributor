import os
import tempfile
import requests
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _build_service(credentials: dict):
    creds = Credentials(
        token=credentials.get("access_token"),
        refresh_token=credentials.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=credentials.get("client_id"),
        client_secret=credentials.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


CATEGORY_IDS = {
    "trading":    "22",  # People & Blogs
    "fitness":    "17",  # Sports
    "crime":      "22",
    "sports":     "17",
    "gaming":     "20",  # Gaming
    "everything": "22",
    "kids":       "1",   # Film & Animation
}


def upload_video(credentials: dict, video_url: str, title: str, description: str = "",
                 tags: list = None, niche: str = "everything",
                 is_short: bool = False, made_for_kids: bool = False) -> dict:
    """Download video from URL and upload it to a YouTube channel."""
    service = _build_service(credentials)

    # Shorts need #Shorts in the title for YouTube to classify them
    upload_title = (title[:93] + " #Shorts") if is_short else title[:100]
    category_id  = CATEGORY_IDS.get(niche, "22")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
        resp = requests.get(video_url, stream=True, timeout=120)
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        resp.close()

    try:
        body = {
            "snippet": {
                "title":       upload_title,
                "description": description,
                "tags":        (tags or []) + (["Shorts"] if is_short else []),
                "categoryId":  category_id,
            },
            "status": {
                "privacyStatus":          "public",
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        media    = MediaFileUpload(tmp_path, mimetype="video/mp4", resumable=True)
        req      = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()

        return {"video_id": response["id"], "url": f"https://youtu.be/{response['id']}"}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def update_channel_description(credentials: dict, channel_id: str, description: str) -> dict:
    service = _build_service(credentials)
    body = {
        "id": channel_id,
        "brandingSettings": {"channel": {"description": description}},
    }
    resp = service.channels().update(part="brandingSettings", body=body).execute()
    return {"channel_id": resp.get("id"), "updated": True}


def update_channel_banner(credentials: dict, banner_url: str) -> dict:
    """Upload a banner image from a URL and apply it to the channel."""
    import tempfile
    service = _build_service(credentials)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        r = requests.get(banner_url, stream=True, timeout=60)
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=8192):
            tmp.write(chunk)
        r.close()

    try:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(tmp_path, mimetype="image/jpeg", resumable=False)
        banner_resp = service.channelBanners().insert(media_body=media).execute()
        banner_external_url = banner_resp.get("url")

        channel_resp = service.channels().update(
            part="brandingSettings",
            body={"id": banner_resp.get("channelId"), "brandingSettings": {"image": {"bannerExternalUrl": banner_external_url}}},
        ).execute()
        return {"updated": True, "banner_url": banner_external_url}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
