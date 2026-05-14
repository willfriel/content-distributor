"""
Instagram public profile scraper using instaloader.
Scrapes captions, hashtags, and engagement metrics from reference accounts.
No login required for public profiles (rate-limited to ~30 posts/account).
"""

import re
import time
import itertools
from datetime import datetime


def scrape_account(handle: str, max_posts: int = 30) -> list[dict]:
    """
    Scrape recent posts from a public Instagram account.
    Returns list of post dicts with caption, likes, comments, views, etc.
    """
    try:
        import instaloader
    except ImportError:
        print("[scraper] instaloader not installed")
        return []

    handle = handle.lstrip("@")
    L      = instaloader.Instaloader(
        download_pictures   = False,
        download_videos     = False,
        download_video_thumbnails = False,
        download_geotags    = False,
        download_comments   = False,
        save_metadata       = False,
        compress_json       = False,
        quiet               = True,
    )

    posts = []
    try:
        profile = instaloader.Profile.from_username(L.context, handle)
        for post in itertools.islice(profile.get_posts(), max_posts):
            caption   = post.caption or ""
            hashtags  = re.findall(r"#\w+", caption)
            views     = post.video_view_count if post.is_video else 0
            reach     = max(views, post.likes * 10, 1)  # rough reach estimate
            eng_rate  = round((post.likes + post.comments * 2) / reach * 100, 4)

            posts.append({
                "shortcode":       post.shortcode,
                "caption":         caption,
                "hashtags":        hashtags,
                "likes":           post.likes,
                "comments":        post.comments,
                "views":           views,
                "engagement_rate": eng_rate,
                "posted_at":       post.date_utc,
            })
            time.sleep(1.5)  # respect rate limits

    except Exception as e:
        print(f"[scraper] Failed to scrape @{handle}: {e}")

    print(f"[scraper] @{handle}: scraped {len(posts)} posts")
    return posts


def scrape_all_accounts(app):
    """Scrape all active reference accounts and store results in DB."""
    with app.app_context():
        from models import db, ReferenceAccount, ScrapedPost

        accounts = ReferenceAccount.query.filter_by(
            is_active=True, platform="instagram"
        ).all()

        for account in accounts:
            posts = scrape_account(account.handle, max_posts=30)
            new_count = 0

            for p in posts:
                if ScrapedPost.query.filter_by(shortcode=p["shortcode"]).first():
                    continue  # already scraped
                sp = ScrapedPost(
                    account_id      = account.id,
                    shortcode       = p["shortcode"],
                    caption         = p["caption"],
                    hashtags        = p["hashtags"],
                    likes           = p["likes"],
                    comments        = p["comments"],
                    views           = p["views"],
                    engagement_rate = p["engagement_rate"],
                    posted_at       = p["posted_at"],
                )
                db.session.add(sp)
                new_count += 1

            account.last_scraped_at = datetime.utcnow()
            account.post_count      = ScrapedPost.query.filter_by(account_id=account.id).count() + new_count
            db.session.commit()
            print(f"[scraper] @{account.handle}: {new_count} new posts stored")

            time.sleep(5)  # pause between accounts

        print(f"[scraper] Done scraping {len(accounts)} accounts")
