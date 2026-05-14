import requests

FLASK_URL = "http://localhost:5000"
NGROK_URL = "https://chef-outbound-banked.ngrok-free.dev"

NICHES = [
    {"niche": "trading",   "account": "TradingBot",          "filename": "welcome_trading.mp4",    "title": "Welcome to TradingBot!"},
    {"niche": "fitness",   "account": "FitnessNerd",         "filename": "welcome_fitness.mp4",    "title": "Welcome to FitnessNerd!"},
    {"niche": "crime",     "account": "Midnight Mysteries",  "filename": "welcome_crime.mp4",      "title": "Welcome to Midnight Mysteries!"},
    {"niche": "sports",    "account": "Athlete Rendezvous",  "filename": "welcome_sports.mp4",     "title": "Welcome to Athlete Rendezvous!"},
    {"niche": "anatomy",   "account": "Sweat Science",       "filename": "welcome_anatomy.mp4",    "title": "Welcome to Sweat Science!"},
    {"niche": "everything","account": "NorthEdge",           "filename": "welcome_everything.mp4", "title": "Welcome to NorthEdge!"},
    {"niche": "kids",      "account": "Lumi Kids",           "filename": "welcome_kids.mp4",       "title": "Welcome to Lumi Kids!"},
]

for nd in NICHES:
    video_url = f"{NGROK_URL}/static/videos/{nd['filename']}"
    resp = requests.post(f"{FLASK_URL}/api/upload/quick", json={
        "video_url":    video_url,
        "title":        nd["title"],
        "niche":        nd["niche"],
        "platform":     "youtube",
        "use_opusclip": False,
        "wait":         True,
    })
    result = resp.json()
    status = result.get("status", "?")
    yt_results = result.get("upload_results", {}).get("youtube", {})
    for ch, res in yt_results.items():
        r = res[0] if res else {}
        url = r.get("url", r.get("error", "unknown"))
        print(f"{nd['niche']:12} | {ch:25} | {status:10} | {url}")
