import requests
from pathlib import Path
import yt_dlp
from moviepy import VideoFileClip, concatenate_videoclips

OUTPUT_DIR   = Path("C:/Users/Willb/Projects/content-distributor/static/videos")
SOURCE_PATH  = OUTPUT_DIR / "spongebob_source.mp4"
OUTPUT_PATH  = OUTPUT_DIR / "spongebob_reel.mp4"
FLASK_URL    = "http://localhost:5000"
NGROK_URL    = "https://chef-outbound-banked.ngrok-free.dev"
YT_URL       = "https://www.youtube.com/watch?v=XinP8U6rmys"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Step 1: Download
print("Downloading...", flush=True)
ydl_opts = {
    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "outtmpl": str(SOURCE_PATH),
    "quiet": True,
    "merge_output_format": "mp4",
    "extractor_args": {"youtube": {"player_client": ["web"]}},
    "js_runtimes": {"node": {}},
}
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([YT_URL])
print("Downloaded.", flush=True)

# Step 2: Cut into 0.5s rapid cuts spread across first 4 minutes
print("Editing...", flush=True)
source = VideoFileClip(str(SOURCE_PATH))

TARGET_W, TARGET_H = 1080, 1920
CLIP_DUR   = 0.5
N_CLIPS    = 28   # 28 × 0.5s = 14 seconds total
SPREAD_END = min(240, source.duration)  # spread across first 4 minutes
interval   = SPREAD_END / N_CLIPS

def make_vertical(c):
    sw, sh = c.size
    src_ratio    = sw / sh
    target_ratio = TARGET_W / TARGET_H
    if src_ratio > target_ratio:
        new_w = int(sh * target_ratio)
        x1 = (sw - new_w) // 2
        c = c.cropped(x1=x1, x2=x1 + new_w)
    else:
        new_h = int(sw / target_ratio)
        y1 = (sh - new_h) // 2
        c = c.cropped(y1=y1, y2=y1 + new_h)
    return c.resized((TARGET_W, TARGET_H))

clips = []
for i in range(N_CLIPS):
    ts = i * interval
    if ts + CLIP_DUR <= source.duration:
        c = source.subclipped(ts, ts + CLIP_DUR)
        c = make_vertical(c)
        clips.append(c)

final = concatenate_videoclips(clips, method="compose")
final.write_videofile(
    str(OUTPUT_PATH), fps=30, codec="libx264",
    audio_codec="aac", logger=None
)
final.close()
source.close()
print("Edited.", flush=True)

# Step 3: Post to northedge accounts
print("Posting...", flush=True)
caption = (
    "POV: you told Spongebob something funny at 2am 😂🧽\n\n"
    "#spongebob #spongebobmemes #funny #viral #memes #foryoupage "
    "#fyp #comedy #nickelodeon #laughing #trending #shorts #reels "
    "#northedge #cartoon #relatable"
)

resp = requests.post(f"{FLASK_URL}/api/upload/quick", json={
    "video_url":    f"{NGROK_URL}/static/videos/spongebob_reel.mp4",
    "title":        "Spongebob can't stop laughing 😂",
    "description":  caption,
    "niche":        "everything",
    "use_opusclip": False,
    "wait":         True,
})
result = resp.json()
print("Status:", result.get("status"))
for platform, accounts in result.get("upload_results", {}).items():
    for acc, res in accounts.items():
        r = res[0] if res else {}
        print(f"  {platform} | {acc} | {r.get('url') or r.get('video_id') or r.get('error')}")
