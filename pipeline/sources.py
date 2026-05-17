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

# ---------------------------------------------------------------------------
# Twitch niche streamer list — add new streamers here anytime
# ---------------------------------------------------------------------------
TWITCH_NICHE_STREAMERS = [
    "kaicenat",
    "adinross",
    "xqc",
    "marlon",
    "adapt",
    "n3on",
    "stableronaldo",
    "jynxzi",
    "lacy",
    "ishowspeed",
    "jidionpremium",
    "lacyoffline_",
    "joe_bartolozzi",
    "jasontheween",
]

NICHE_CONFIG = {
    "twitch": {
        "youtube_queries": [],
        "subreddits":      [],
        "rss_feeds":       [],
        "keywords":        ["twitch", "clip", "streamer", "viral", "funny", "moment"],
        "twitch_streamers": TWITCH_NICHE_STREAMERS,
        "twitch_games":     [],
    },
    "trading": {
        "youtube_queries": [
            "day trader secrets shorts",
            "how i make money trading stocks",
            "investor advice that changed my life",
            "stock market explained simple short",
            "crypto trading strategy that works",
            "wall street secrets revealed short",
            "millionaire investor speech short",
        ],
        "subreddits":      ["wallstreetbets", "investing", "Daytrading"],
        "rss_feeds":       ["https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US"],
        "keywords":        ["trading", "crypto", "stocks", "market", "investing"],
        "twitch_streamers": [],
        "twitch_games":     ["Science & Technology"],
    },
    "fitness": {
        "youtube_queries": [
            "bodybuilder workout tips short",
            "how to build muscle fast short",
            "gym mistakes beginners make shorts",
            "what to eat to build muscle short",
            "chest workout tutorial shorts",
            "fitness influencer daily routine short",
            "how to lose weight fast gym short",
            "powerlifter training tips short",
        ],
        "subreddits":      ["fitness", "bodybuilding", "weightlifting", "gym"],
        "rss_feeds":       ["https://www.menshealth.com/rss/all.xml"],
        "keywords":        ["workout", "fitness", "gym", "bodybuilder", "muscle", "nutrition", "lifting"],
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
        "youtube_queries": [
            "insane gaming clip shorts",
            "funniest gaming moments 2025",
            "pro gamer clutch moment short",
            "rage quit gaming shorts",
            "gaming world record broken short",
            "unexpected gaming moment short",
        ],
        "subreddits":      ["LivestreamFail", "gaming", "nextfuckinglevel"],
        "rss_feeds":       [],
        "keywords":        ["gaming", "gamer", "gameplay", "twitch", "esports", "streamer"],
        "twitch_streamers": ["shroud", "summit1g", "asmongold", "moistcr1tikal", "xqc"],
        "twitch_games":     ["Fortnite", "Valorant", "Apex Legends", "Call of Duty: Warzone"],
    },
    "everything": {
        "youtube_queries": [
            "unbelievable moment caught camera short",
            "shocking thing happened shorts",
            "you wont believe this viral short",
            "mind blowing moment short",
            "craziest thing ever shorts",
        ],
        "subreddits":      ["nextfuckinglevel", "interestingasfuck", "WTF", "PublicFreakout"],
        "rss_feeds":       [],
        "keywords":        ["viral", "unbelievable", "shocking", "crazy", "insane"],
        "twitch_streamers": ["xqc", "moistcr1tikal", "hasanabi"],
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
    if not queries:
        return []
    query   = random.choice(queries)

    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part":              "snippet",
                "q":                 query,
                "type":              "video",
                "videoDuration":     "short",      # under 4 minutes
                "videoLicense":      "creativeCommon",
                "order":             "viewCount",
                "relevanceLanguage": "en",         # English only — avoids foreign-language clips
                "maxResults":        max_results,
                "key":               api_key,
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
            # Get best portrait file capped at 1080p to avoid huge 4K downloads
            files = sorted(v.get("video_files", []),
                           key=lambda f: f.get("height", 0), reverse=True)
            portrait = next((f for f in files if 720 <= f.get("height", 0) <= 1080), None)
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

    # Pexels stock video — only niches that genuinely need b-roll
    # Excluded: trading (returns random finance b-roll, not informational),
    #           fitness (returns clipboard/static props, not workout content)
    if niche in ("gaming", "sports", "kids", "twitch", "everything"):
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

    # Sort so highest-quality sources surface first:
    # twitch clips are pre-filtered for entertainment; pexels is last resort
    _QUALITY = {"twitch": 0, "reddit": 1, "youtube": 2, "pexels": 3, "brainrot": 4}
    candidates.sort(key=lambda c: _QUALITY.get(c.get("source_type", ""), 5))
    return [c for c in candidates if c]
