import time
import requests
from pathlib import Path
from moviepy import ColorClip, TextClip, CompositeVideoClip

FLASK_URL  = "https://content-distributor.onrender.com"
NGROK_URL  = "https://content-distributor.onrender.com"
VIDEOS_DIR = Path(__file__).parent / "static" / "videos"

NICHES = [
    {
        "niche":        "trading",
        "account_name": "TradingBot",
        "content":      "trading tips, market breakdowns, and crypto content",
        "hashtags":     "#trading #crypto #stocks #forex #investing #daytrading #finance #money #wealth #tradingbot",
        "filename":     "welcome_trading.mp4",
    },
    {
        "niche":        "fitness",
        "account_name": "FitnessNerd",
        "content":      "fitness tips, workouts, and science-backed health content",
        "hashtags":     "#fitness #workout #health #gym #fitnessmotivation #exercise #nutrition #fitlife #gains #sweatscience",
        "filename":     "welcome_fitness.mp4",
    },
    {
        "niche":        "crime",
        "account_name": "Midnight Mysteries",
        "content":      "true crime, mysteries, and unsolved cases",
        "hashtags":     "#truecrime #mystery #crime #unsolved #crimestories #thriller #coldcase #crimepodcast #darkweb #midnight",
        "filename":     "welcome_crime.mp4",
    },
    {
        "niche":        "sports",
        "account_name": "Athlete Rendezvous",
        "content":      "sports highlights, breakdowns, and athlete content",
        "hashtags":     "#sports #athlete #highlights #sportslife #sportsnews #athletic #training #competition #fitness #game",
        "filename":     "welcome_sports.mp4",
    },
    {
        "niche":        "gaming",
        "account_name": "pipeline_gaming_",
        "content":      "daily gaming clips, viral moments, and stories from the world of gaming",
        "hashtags":     "#gaming #gamer #twitch #fyp #viral #gameplay #esports #gamingmoments #streamer #clips",
        "filename":     "welcome_gaming.mp4",
    },
    {
        "niche":        "everything",
        "account_name": "NorthEdge",
        "content":      "a mix of everything — trading, fitness, sports, and more",
        "hashtags":     "#lifestyle #trending #viral #content #mixed #entertainment #daily #northedge #variety #everything",
        "filename":     "welcome_everything.mp4",
    },
    {
        "niche":        "kids",
        "account_name": "Lumi Kids",
        "content":      "fun kids entertainment, stories, and cartoons",
        "hashtags":     "#kids #children #family #kidsentertainment #cartoon #storytime #lumikids #kidsvideo #fun #educational",
        "filename":     "welcome_kids.mp4",
    },
]


def generate_video(output_path, account_name, content_description, duration=12):
    text = (
        f"Hey guys, welcome to\n{account_name}!\n\n"
        f"I'm gonna be posting\n{content_description}\n\n"
        f"Hope you guys enjoy!"
    )
    bg  = ColorClip(size=(1080, 1920), color=(0, 0, 0), duration=duration)
    txt = (
        TextClip(
            text=text,
            font_size=65,
            color="white",
            font="C:/Windows/Fonts/arial.ttf",
            size=(900, None),
            method="caption",
            text_align="center",
        )
        .with_position("center")
        .with_duration(duration)
    )
    out = CompositeVideoClip([bg, txt])
    out.write_videofile(str(output_path), fps=24, codec="libx264", audio=False, logger=None)
    out.close()


def post_to_niche(niche_data, video_url):
    caption = (
        f"Hey guys, welcome to {niche_data['account_name']}! "
        f"I'm gonna be posting {niche_data['content']}. "
        f"Hope you guys enjoy!\n\n{niche_data['hashtags']}"
    )
    resp = requests.post(f"{FLASK_URL}/api/upload/quick", json={
        "video_url":    video_url,
        "title":        f"Welcome to {niche_data['account_name']}!",
        "description":  caption,
        "niche":        niche_data["niche"],
        "use_opusclip": False,
        "wait":         False,
    })
    return resp.json()


if __name__ == "__main__":
    import sys
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    # Optionally filter to a single niche: python make_welcome_posts.py gaming
    filter_niche = sys.argv[1] if len(sys.argv) > 1 else None
    niches_to_run = [nd for nd in NICHES if not filter_niche or nd["niche"] == filter_niche]

    for nd in niches_to_run:
        print(f"\n{'='*55}")
        print(f"  {nd['account_name']}  ({nd['niche']})")
        print(f"{'='*55}")

        output_path = VIDEOS_DIR / nd["filename"]

        print("  Generating video...", end=" ", flush=True)
        generate_video(output_path, nd["account_name"], nd["content"])
        print("done")

        video_url = f"{FLASK_URL}/static/videos/{nd['filename']}"
        print(f"  URL: {video_url}")

        print("  Queuing upload...", end=" ", flush=True)
        result = post_to_niche(nd, video_url)
        job_id = result.get("content_id", "?")
        status = result.get("status", result)
        print(f"done  (job #{job_id} — {status})")

        time.sleep(1)

    print(f"\n{'='*55}")
    print("  All welcome posts queued! Check localhost:5000 for status.")
    print(f"{'='*55}\n")
