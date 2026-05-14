import requests

FLASK_URL = "http://localhost:5000"

BIOS = {
    "trading":   "Trading tips, market breakdowns & crypto content. Built for traders, by traders. AI trading bot: https://usetradingbot.com/",
    "fitness":   "Science-backed fitness. No fluff, just results — workouts, nutrition, and the stuff that actually works.",
    "crime":     "True crime, cold cases, and unsolved mysteries. Some stories aren't meant to stay buried.",
    "sports":    "Your daily sports fix — game breakdowns, athlete stories, and the moments that matter.",
    "anatomy":   "Making the human body make sense. Anatomy, physiology, and medical education simplified.",
    "everything":"No niche, no limits. Trading, fitness, sports, crime — whatever's interesting goes.",
    "kids":      "Where imagination comes to life! Stories, cartoons, and entertainment made for kids.",
}

for niche, bio in BIOS.items():
    print(f"\n--- {niche} ---")
    resp = requests.post(f"{FLASK_URL}/api/accounts/update-bio", json={"niche": niche, "bio": bio})
    for r in resp.json():
        status = "OK" if r.get("result", {}).get("updated") else r.get("error", "?")[:60]
        print(f"  {r['platform']:10} | {r['account']:25} | {status}")
