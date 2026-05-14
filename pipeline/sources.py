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
    },
    "fitness": {
        "youtube_queries": ["fitness tips short", "workout motivation shorts", "nutrition advice gym"],
        "subreddits":      ["fitness", "gym", "bodybuilding"],
        "rss_feeds":       ["https://www.menshealth.com/rss/all.xml"],
        "keywords":        ["workout", "fitness", "gym", "nutrition", "health"],
    },
    "crime": {
        "youtube_queries": ["true crime short", "unsolved mystery short", "cold case story"],
        "subreddits":      ["UnresolvedMysteries", "TrueCrime", "mystery"],
        "rss_feeds":       ["https://feeds.megaphone.fm/CSN4919452500"],
        "keywords":        ["crime", "mystery", "unsolved", "cold case", "thriller"],
    },
    "sports": {
        "youtube_queries": ["sports moments shorts", "best sports plays", "athlete story short"],
        "subreddits":      ["sports", "nba", "soccer"],
        "rss_feeds":       ["https://www.espn.com/espn/rss/news"],
        "keywords":        ["sports", "athlete", "game", "championship", "highlights"],
    },
    "anatomy": {
        "youtube_queries": ["anatomy explained short", "medical facts shorts", "human body science"],
        "subreddits":      ["medicine", "anatomy", "biology"],
        "rss_feeds":       ["https://medlineplus.gov/rss.html"],
        "keywords":        ["anatomy", "physiology", "medical", "body", "science"],
    },
    "everything": {
        "youtube_queries": ["viral short 2025", "funny moments shorts", "interesting facts short"],
        "subreddits":      ["interestingasfuck", "oddlysatisfying", "nextfuckinglevel"],
        "rss_feeds":       [],
        "keywords":        ["viral", "interesting", "funny", "satisfying", "amazing"],
    },
    "kids": {
        "youtube_queries": ["kids story short", "cartoon funny short", "children education short"],
        "subreddits":      ["KidsAreFuckingStupid", "aww"],
        "rss_feeds":       [],
        "keywords":        ["kids", "children", "cartoon", "story", "fun"],
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

def fetch_reddit_candidates(niche: str, max_results: int = 5) -> list[dict]:
    """Pull top video posts from niche subreddits (no auth needed)."""
    config     = NICHE_CONFIG.get(niche, {})
    subreddits = config.get("subreddits", [])
    if not subreddits:
        return []

    subreddit = random.choice(subreddits)
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/top.json",
            params={"t": "day", "limit": 25},
            headers={"User-Agent": "content-distributor/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json().get("data", {}).get("children", [])
        candidates = []
        for post in posts:
            d = post.get("data", {})
            # Only video posts
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

    # YouTube CC clips (best quality, direct video URL)
    if youtube_api_key:
        candidates += fetch_youtube_candidates(niche, youtube_api_key)

    # Reddit video posts
    candidates += fetch_reddit_candidates(niche)

    # RSS as AI topic seeds (lower priority)
    rss = fetch_rss_topics(niche)
    candidates += [fetch_ai_candidate(niche, t["title"]) for t in rss if t["title"]]

    return [c for c in candidates if c]
