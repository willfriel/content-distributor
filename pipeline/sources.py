"""
Content sourcing for the automated pipeline.
Each function returns a list of candidate dicts:
  { url, title, duration_seconds, source_type, niche }
"""

import os
import random
import tempfile
import requests
import feedparser

# ---------------------------------------------------------------------------
# Niche configuration
# ---------------------------------------------------------------------------

NICHE_CONFIG = {
    "trading": {
        "youtube_queries": ["trading tips shorts", "crypto analysis 2025", "stock market explained short"],
        "subreddits":      ["wallstreetbets", "investing", "CryptoCurrency"],
        "rss_feeds":       ["https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US"],
        "keywords":        ["trading", "crypto", "stocks", "market", "investing"],
        "twitch_streamers": [],
        "twitch_games":     ["Science & Technology"],
    },
    "fitness": {
        "youtube_queries": ["fitness tips short", "workout motivation shorts", "nutrition advice gym",
                            "mindset motivation short", "self improvement shorts", "discipline habits short"],
        "subreddits":      ["fitness", "gym", "bodybuilding", "selfimprovement", "motivation"],
        "rss_feeds":       ["https://www.menshealth.com/rss/all.xml"],
        "keywords":        ["workout", "fitness", "gym", "nutrition", "health", "mindset", "discipline", "habits"],
        "twitch_streamers": ["jujimufu", "ldlc_kawhi", "naturally_stefany"],
        "twitch_games":     ["Fitness & Health"],
    },
    "crime": {
        "youtube_queries": ["true crime short", "unsolved mystery short", "cold case story"],
        "subreddits":      ["UnresolvedMysteries", "TrueCrime", "mystery"],
        "rss_feeds":       ["https://feeds.megaphone.fm/CSN4919452500"],
        "keywords":        ["crime", "mystery", "unsolved", "cold case", "thriller"],
        "twitch_streamers": [],
        "twitch_games":     ["Grand Theft Auto V", "Demonologist"],
    },
    "sports": {
        "youtube_queries": ["sports moments shorts", "best sports plays", "athlete story short"],
        "subreddits":      ["sports", "nba", "soccer"],
        "rss_feeds":       ["https://www.espn.com/espn/rss/news"],
        "keywords":        ["sports", "athlete", "game", "championship", "highlights"],
        "twitch_streamers": ["espn", "nba", "nfl"],
        "twitch_games":     ["EA Sports FC 25", "NBA 2K25", "Madden NFL 25"],
    },
    "gaming": {
        "youtube_queries": ["gaming moments shorts", "funny gaming clip", "speedrun world record short",
                            "gaming fail shorts", "best plays gaming short"],
        "subreddits":      ["gaming", "LivestreamFail", "pcgaming", "Unexpected"],
        "rss_feeds":       [],
        "keywords":        ["gaming", "gamer", "gameplay", "twitch", "esports", "streamer"],
        "twitch_streamers": ["shroud", "summit1g", "asmongold", "moistcr1tikal", "xqc"],
        "twitch_games":     ["Fortnite", "Valorant", "Minecraft", "Apex Legends", "Call of Duty: Warzone"],
    },
    "everything": {
        "youtube_queries": ["viral short 2025", "funny moments shorts", "interesting facts short"],
        "subreddits":      ["interestingasfuck", "oddlysatisfying", "nextfuckinglevel"],
        "rss_feeds":       [],
        "keywords":        ["viral", "interesting", "funny", "satisfying", "amazing"],
        "twitch_streamers": ["xqc", "moistcr1tikal", "northernlion", "hasanabi"],
        "twitch_games":     ["Just Chatting"],
    },
    "kids": {
        "youtube_queries": ["kids story short", "cartoon funny short", "children education short"],
        "subreddits":      ["KidsAreFuckingStupid", "aww"],
        "rss_feeds":       [],
        "keywords":        ["kids", "children", "cartoon", "story", "fun"],
        "twitch_streamers": ["graystillplays", "stampylonghead"],
        "twitch_games":     ["Minecraft", "Just Dance 2024 Edition", "Fortnite"],
    },
}


# ---------------------------------------------------------------------------
# YouTube source
# ---------------------------------------------------------------------------

def fetch_youtube_candidates(niche: str, api_key: str, max_results: int = 5) -> list[dict]:
    """Search YouTube for short-form content in the niche. Prefers CC-licensed videos."""
    config  = NICHE_CONFIG.get(niche, {})
    queries = config.get("youtube_queries", [f"{niche} shorts"])
    query   = random.choice(queries)

    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part":            "snippet",
                "q":               query,
                "type":            "video",
                "videoDuration":   "short",      # under 4 minutes
                "videoLicense":    "creativeCommon",
                "order":           "viewCount",
                "maxResults":      max_results,
                "key":             api_key,
            },
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        return [
            {
                "url":          f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                "title":        item["snippet"]["title"],
                "source_type":  "youtube",
                "niche":        niche,
                "query":        query,
            }
            for item in items
            if item.get("id", {}).get("videoId")
        ]
    except Exception as e:
        print(f"[sources] YouTube fetch failed for {niche}: {e}")
        return []


# ---------------------------------------------------------------------------
# Reddit source
# ---------------------------------------------------------------------------

def fetch_reddit_candidates(niche: str, max_results: int = 5,
                            timeframe: str = "day") -> list[dict]:
    """
    Pull top video posts from niche subreddits.
    timeframe: hour, day, week, month, year, all
    """
    config     = NICHE_CONFIG.get(niche, {})
    subreddits = config.get("subreddits", [])
    if not subreddits:
        return []

    subreddit = random.choice(subreddits)
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/top.json",
            params={"t": timeframe, "limit": 25},
            headers={"User-Agent": "content-distributor/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json().get("data", {}).get("children", [])
        candidates = []
        for post in posts:
            d = post.get("data", {})
            if not (d.get("is_video") or "v.redd.it" in d.get("url", "") or
                    "youtube.com" in d.get("url", "") or "youtu.be" in d.get("url", "")):
                continue
            candidates.append({
                "url":         d["url"],
                "title":       d.get("title", ""),
                "score":       d.get("score", 0),
                "source_type": "reddit",
                "niche":       niche,
                "subreddit":   subreddit,
                "timeframe":   timeframe,
            })
            if len(candidates) >= max_results:
                break
        return candidates
    except Exception as e:
        print(f"[sources] Reddit fetch failed for {niche} r/{subreddit}: {e}")
        return []


# ---------------------------------------------------------------------------
# RSS / News source  →  returns topic ideas, not video files
# ---------------------------------------------------------------------------

def fetch_rss_topics(niche: str, max_results: int = 3) -> list[dict]:
    """Pull headlines from RSS feeds. Used as inspiration for AI-generated videos."""
    config = NICHE_CONFIG.get(niche, {})
    feeds  = config.get("rss_feeds", [])
    topics = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_results]:
                topics.append({
                    "title":       entry.get("title", ""),
                    "summary":     entry.get("summary", "")[:300],
                    "source_type": "rss",
                    "niche":       niche,
                    "feed":        feed_url,
                })
        except Exception as e:
            print(f"[sources] RSS fetch failed for {niche}: {e}")
    return topics


# ---------------------------------------------------------------------------
# Pexels free stock video source (fitness, anatomy, general b-roll)
# ---------------------------------------------------------------------------

PEXELS_QUERIES = {
    "fitness":    ["gym workout", "exercise form", "weightlifting", "running athlete", "yoga",
                   "meditation focus", "sunrise motivation", "journaling mindset"],
    "gaming":     ["gaming setup", "neon gaming room", "controller hands", "esports arena", "rgb pc"],
    "sports":     ["sports highlights", "basketball dunk", "soccer goal", "athlete training"],
    "everything": ["satisfying", "nature timelapse", "city life", "funny animal"],
    "kids":       ["children playing", "colorful cartoon", "kid learning", "family fun"],
    "trading":    ["stock market screen", "finance charts", "city skyline night"],
    "crime":      ["dark city night", "detective mystery", "rain window dark"],
}

def fetch_pexels_candidates(niche: str, max_results: int = 3) -> list[dict]:
    """Fetch free stock videos from Pexels matching the niche."""
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []

    queries = PEXELS_QUERIES.get(niche, [niche])
    query   = random.choice(queries)

    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": max_results * 2,
                    "orientation": "portrait", "size": "medium"},
            timeout=15,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        candidates = []
        for v in videos:
            # Get the HD portrait file
            files = sorted(v.get("video_files", []),
                           key=lambda f: f.get("height", 0), reverse=True)
            portrait = next((f for f in files if f.get("height", 0) >= 720), None)
            if not portrait:
                continue
            candidates.append({
                "url":         portrait["link"],
                "title":       v.get("url", query).split("/")[-2].replace("-", " "),
                "source_type": "pexels",
                "niche":       niche,
                "query":       query,
            })
            if len(candidates) >= max_results:
                break
        return candidates
    except Exception as e:
        print(f"[sources] Pexels fetch failed for {niche}: {e}")
        return []


# ---------------------------------------------------------------------------
# Brainrot background clips (for crime / anatomy overlay)
# Pulls satisfying/GTA clips from Reddit to use as background footage
# ---------------------------------------------------------------------------

BRAINROT_SUBREDDITS = [
    "oddlysatisfying", "perfectlycutscreams", "gtaonline",
    "softwaregore", "mildlyinfuriating", "Whatcouldgowrong",
]

def fetch_brainrot_clip(max_results: int = 3) -> list[dict]:
    """
    Fetch loopable background clips for crime/anatomy overlays.
    Pulls from satisfying/GTA subreddits — high retention, no context needed.
    """
    subreddit = random.choice(BRAINROT_SUBREDDITS)
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/top.json",
            params={"t": "week", "limit": 25},
            headers={"User-Agent": "content-distributor/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json().get("data", {}).get("children", [])
        candidates = []
        for post in posts:
            d = post.get("data", {})
            if not (d.get("is_video") or "v.redd.it" in d.get("url", "")):
                continue
            candidates.append({
                "url":         d["url"],
                "title":       d.get("title", ""),
                "source_type": "brainrot",
                "niche":       "background",
                "subreddit":   subreddit,
            })
            if len(candidates) >= max_results:
                break
        return candidates
    except Exception as e:
        print(f"[sources] Brainrot fetch failed r/{subreddit}: {e}")
        return []


# ---------------------------------------------------------------------------
# Twitch source
# ---------------------------------------------------------------------------

def fetch_twitch_candidates(niche: str, max_results: int = 5) -> list[dict]:
    """Fetch top clips from Twitch streamers and game categories for the niche."""
    config    = NICHE_CONFIG.get(niche, {})
    streamers = config.get("twitch_streamers", [])
    games     = config.get("twitch_games", [])

    if not streamers and not games:
        return []

    try:
        from integrations.twitch import get_clips_by_streamer, get_clips_by_game
    except ImportError:
        return []

    candidates = []

    # Pick one random streamer and one random game to keep variety
    if streamers:
        streamer = random.choice(streamers)
        candidates += get_clips_by_streamer(streamer, niche, max_results=3)

    if games:
        game = random.choice(games)
        candidates += get_clips_by_game(game, niche, max_results=3)

    # Sort by view count so the most viral clips surface first
    candidates.sort(key=lambda c: c.get("views", 0), reverse=True)
    return candidates[:max_results]


# ---------------------------------------------------------------------------
# AI-generated stub (activated when Higgsfield/ElevenLabs subscriptions are set)
# ---------------------------------------------------------------------------

def fetch_ai_candidate(niche: str, topic: str) -> dict | None:
    """
    Returns a candidate dict with source_type='ai_pending'.
    The scheduler checks CreditBudget before calling this.
    Real generation happens in pipeline/ai_generator.py once subscriptions are active.
    """
    return {
        "url":         None,
        "title":       topic,
        "source_type": "ai_pending",
        "niche":       niche,
    }


# ---------------------------------------------------------------------------
# Main entry point used by scheduler
# ---------------------------------------------------------------------------

def get_candidates(niche: str, youtube_api_key: str = None) -> list[dict]:
    """Return all available candidates for a niche, ranked by source quality."""
    candidates = []

    # YouTube CC clips
    if youtube_api_key:
        candidates += fetch_youtube_candidates(niche, youtube_api_key)

    # Twitch clips (viral, short-form)
    candidates += fetch_twitch_candidates(niche)

    # Pexels stock video (fitness, gaming — needs real b-roll)
    if niche in ("fitness", "gaming", "sports", "kids", "trading"):
        candidates += fetch_pexels_candidates(niche)

    # Reddit — for "everything" pull BOTH timeframes (fresh + all-time classics)
    if niche == "everything":
        candidates += fetch_reddit_candidates(niche, timeframe="day")    # last 24h
        candidates += fetch_reddit_candidates(niche, timeframe="all")    # all-time viral
    else:
        candidates += fetch_reddit_candidates(niche, timeframe="day")

    # Crime gets a brainrot background clip as an overlay option
    if niche == "crime":
        candidates += fetch_brainrot_clip()

    # RSS as AI topic seeds (lower priority)
    rss = fetch_rss_topics(niche)
    candidates += [fetch_ai_candidate(niche, t["title"]) for t in rss if t["title"]]

    return [c for c in candidates if c]
