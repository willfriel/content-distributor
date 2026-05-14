import requests
from config import Config


class OpusClipClient:
    def __init__(self):
        self.api_key = Config.OPUSCLIP_API_KEY
        self.base_url = Config.OPUSCLIP_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def create_clip_job(self, video_url: str, title: str = None) -> dict:
        """Submit a video URL to OpusClip for clipping into shorts."""
        payload = {
            "video_url": video_url,
            "target_duration": 60,  # seconds — adjust per niche if needed
        }
        if title:
            payload["title"] = title

        resp = requests.post(
            f"{self.base_url}/clip",
            json=payload,
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_job_status(self, job_id: str) -> dict:
        """Poll a clipping job for status and output URLs."""
        resp = requests.get(
            f"{self.base_url}/clip/{job_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_clips(self, job_id: str) -> list[str]:
        """Return download URLs from a completed job."""
        data = self.get_job_status(job_id)
        status = data.get("status")
        if status != "completed":
            raise RuntimeError(f"Job {job_id} is not completed yet (status: {status})")
        # OpusClip returns clips as a list of objects with a 'url' field
        return [clip["url"] for clip in data.get("clips", [])]
