"""
Style learner — analyzes scraped posts to extract what makes content perform.
Generates a StyleGuide per niche that feeds into caption generation.
"""

import re
from collections import Counter
from datetime import datetime


def _extract_hook(caption: str) -> str:
    """Return the first sentence or first 60 chars of a caption."""
    if not caption:
        return ""
    first = caption.split("\n")[0].split(".")[0].split("!")[0].split("?")[0]
    return first[:80].strip()


def _caption_length_bucket(caption: str) -> str:
    n = len(caption or "")
    if n < 60:   return "very_short"
    if n < 150:  return "short"
    if n < 300:  return "medium"
    return "long"


def _emoji_count(text: str) -> int:
    # rough emoji detection
    return len(re.findall(
        r"[\U0001F300-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F]",
        text or ""
    ))


def learn_style(niche: str, app) -> dict:
    """
    Analyze all scraped posts for a niche, find patterns in top performers,
    and return a guidelines dict. Also saves a StyleGuide record to the DB.
    """
    with app.app_context():
        from models import db, ScrapedPost, ReferenceAccount, StyleGuide

        # Get all scraped posts for this niche (via niche_hint or global accounts)
        niche_accounts = ReferenceAccount.query.filter(
            ReferenceAccount.is_active == True,
            (ReferenceAccount.niche_hint == niche) | (ReferenceAccount.niche_hint == None)
        ).all()
        account_ids = [a.id for a in niche_accounts]

        if not account_ids:
            return {}

        all_posts = ScrapedPost.query.filter(
            ScrapedPost.account_id.in_(account_ids)
        ).all()

        if len(all_posts) < 5:
            print(f"[style_learner] Not enough data for {niche} ({len(all_posts)} posts)")
            return {}

        # Sort by engagement rate
        sorted_posts = sorted(all_posts, key=lambda p: p.engagement_rate or 0, reverse=True)
        top_20_pct   = sorted_posts[:max(1, len(sorted_posts) // 5)]
        bottom_20_pct = sorted_posts[-max(1, len(sorted_posts) // 5):]

        def analyze(posts):
            hooks        = [_extract_hook(p.caption) for p in posts if p.caption]
            lengths      = [_caption_length_bucket(p.caption) for p in posts]
            emoji_counts = [_emoji_count(p.caption) for p in posts]
            all_hashtags = [tag for p in posts for tag in (p.hashtags or [])]
            top_hashtags = [tag for tag, _ in Counter(all_hashtags).most_common(15)]
            avg_eng      = sum(p.engagement_rate or 0 for p in posts) / max(len(posts), 1)
            avg_likes    = sum(p.likes or 0 for p in posts) / max(len(posts), 1)
            avg_views    = sum(p.views or 0 for p in posts) / max(len(posts), 1)

            # Hook pattern detection
            hook_starters = Counter()
            for h in hooks:
                words = h.split()
                if words:
                    hook_starters[words[0].lower()] += 1

            return {
                "avg_engagement":     round(avg_eng, 4),
                "avg_likes":          round(avg_likes),
                "avg_views":          round(avg_views),
                "top_length":         Counter(lengths).most_common(1)[0][0] if lengths else "short",
                "avg_emojis":         round(sum(emoji_counts) / max(len(emoji_counts), 1), 1),
                "top_hashtags":       top_hashtags,
                "common_hook_starts": [w for w, _ in hook_starters.most_common(10)],
                "example_hooks":      hooks[:5],
            }

        top_analysis    = analyze(top_20_pct)
        bottom_analysis = analyze(bottom_20_pct)

        # What separates winners from losers
        guidelines = {
            "niche":               niche,
            "total_posts_analyzed": len(all_posts),
            "top_performer_stats": top_analysis,
            "low_performer_stats": bottom_analysis,
            "recommendations": {
                "caption_length":    top_analysis["top_length"],
                "emoji_target":      top_analysis["avg_emojis"],
                "best_hashtags":     top_analysis["top_hashtags"][:10],
                "winning_hook_words": top_analysis["common_hook_starts"][:5],
                "example_hooks":     top_analysis["example_hooks"],
            },
            "generated_at": datetime.utcnow().isoformat(),
        }

        # Save to DB
        guide = StyleGuide.query.filter_by(niche=niche).first()
        if guide:
            guide.guidelines   = guidelines
            guide.generated_at = datetime.utcnow()
        else:
            guide = StyleGuide(niche=niche, guidelines=guidelines)
            db.session.add(guide)
        db.session.commit()

        print(f"[style_learner] {niche}: analyzed {len(all_posts)} posts, "
              f"top eng={top_analysis['avg_engagement']}")
        return guidelines


def learn_all_niches(app):
    """Re-run style learning for all 7 niches."""
    niches = ["trading", "fitness", "crime", "sports", "anatomy", "everything", "kids"]
    for niche in niches:
        try:
            learn_style(niche, app)
        except Exception as e:
            print(f"[style_learner] Error for {niche}: {e}")


def get_style_guide(niche: str) -> dict:
    """Return the current style guidelines for a niche (called from caption generator)."""
    try:
        from models import StyleGuide
        guide = StyleGuide.query.filter_by(niche=niche).first()
        return guide.guidelines if guide and guide.guidelines else {}
    except Exception:
        return {}
