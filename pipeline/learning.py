"""
Learning system — closes the feedback loop between posting and performance.

Three scheduled operations:
  harvest_metrics()    — pull real IG/YT engagement 24-72h after posting
  update_source_scores() — recompute avg engagement per source type/subtype per niche
  get_top_hooks(niche) — return top-performing hook texts for injection into generation
"""

from datetime import datetime, timedelta


def harvest_metrics(app):
    """Pull real engagement data for posts 24-72h old that haven't been refreshed yet."""
    with app.app_context():
        from models import db, PostMetrics, SocialAccount
        from integrations import analytics as analytics_integration

        now          = datetime.utcnow()
        window_open  = now - timedelta(hours=72)
        window_close = now - timedelta(hours=24)
        stale_cutoff = now - timedelta(hours=12)

        posts = PostMetrics.query.filter(
            PostMetrics.posted_at  >= window_open,
            PostMetrics.posted_at  <= window_close,
            db.or_(
                PostMetrics.metrics_fetched_at == None,
                PostMetrics.metrics_fetched_at <  stale_cutoff,
            )
        ).all()

        print(f"[learning] Harvesting metrics for {len(posts)} posts")
        updated = 0

        for pm in posts:
            try:
                account = SocialAccount.query.get(pm.account_id)
                if not account:
                    continue
                creds = account.get_credentials()

                if pm.platform == "instagram":
                    data = analytics_integration.fetch_instagram_metrics(creds, pm.post_id)
                elif pm.platform == "youtube":
                    data = analytics_integration.fetch_youtube_metrics(creds, pm.post_id)
                else:
                    continue

                if not data:
                    continue

                pm.views    = data.get("views",    pm.views)
                pm.likes    = data.get("likes",    pm.likes)
                pm.comments = data.get("comments", pm.comments)
                pm.shares   = data.get("shares",   pm.shares)
                pm.saves    = data.get("saves",    pm.saves)
                pm.reach    = data.get("reach",    pm.reach)
                pm.compute_engagement()
                pm.metrics_fetched_at = now
                updated += 1

            except Exception as e:
                print(f"[learning] Metric fetch failed for post {pm.id}: {e}")

        db.session.commit()
        print(f"[learning] Updated {updated}/{len(posts)} posts")

        # Check for viral milestones now that metrics are fresh
        try:
            from pipeline.notify import check_viral_posts
            check_viral_posts(app)
        except Exception as e:
            print(f"[learning] Viral check failed: {e}")

        return updated


def update_source_scores(app):
    """
    Recompute SourceScore rows from PostMetrics.
    Only rows with real engagement data (score > 0) are included.
    """
    with app.app_context():
        from models import db, PostMetrics, SourceScore
        from sqlalchemy import func

        rows = db.session.query(
            PostMetrics.niche,
            PostMetrics.source_type,
            PostMetrics.source_subtype,
            func.avg(PostMetrics.engagement_score).label("avg_eng"),
            func.avg(PostMetrics.views).label("avg_views"),
            func.count(PostMetrics.id).label("count"),
        ).filter(
            PostMetrics.source_type    != None,
            PostMetrics.engagement_score > 0,
        ).group_by(
            PostMetrics.niche,
            PostMetrics.source_type,
            PostMetrics.source_subtype,
        ).all()

        now = datetime.utcnow()
        for row in rows:
            if not row.niche or not row.source_type:
                continue
            score = SourceScore.query.filter_by(
                niche          = row.niche,
                source_type    = row.source_type,
                source_subtype = row.source_subtype,
            ).first()
            if not score:
                score = SourceScore(
                    niche          = row.niche,
                    source_type    = row.source_type,
                    source_subtype = row.source_subtype,
                )
                db.session.add(score)
            score.post_count     = row.count
            score.avg_engagement = round(row.avg_eng or 0, 4)
            score.avg_views      = int(row.avg_views or 0)
            score.updated_at     = now

        db.session.commit()
        print(f"[learning] Updated {len(rows)} source scores")


def get_top_hooks(niche: str, limit: int = 5) -> list[str]:
    """
    Return top-performing hook texts for a niche, sorted by engagement score.
    Injected into _generate_hook() as proven examples for Claude to learn from.
    Returns [] if no data or outside app context.
    """
    try:
        from models import PostMetrics
        rows = (
            PostMetrics.query
            .filter(
                PostMetrics.niche         == niche,
                PostMetrics.hook_text     != None,
                PostMetrics.engagement_score > 0,
            )
            .order_by(PostMetrics.engagement_score.desc())
            .limit(limit * 3)
            .all()
        )
        seen, hooks = set(), []
        for pm in rows:
            h = (pm.hook_text or "").strip()
            if h and h not in seen:
                seen.add(h)
                hooks.append(h)
            if len(hooks) >= limit:
                break
        return hooks
    except Exception:
        return []


def get_source_weight(niche: str, source_type: str,
                      source_subtype: str | None = None) -> float:
    """
    Return a sort weight for a content candidate.
    Formula: 1.0 + (avg_engagement * 20), clamped to [0.5, 3.0].
    Requires ≥3 posts before influencing ranking; returns 1.0 otherwise.
    """
    try:
        from models import SourceScore
        score = SourceScore.query.filter_by(
            niche          = niche,
            source_type    = source_type,
            source_subtype = source_subtype,
        ).first()
        if score and score.post_count >= 3:
            return max(0.5, min(3.0, 1.0 + score.avg_engagement * 20))
    except Exception:
        pass
    return 1.0
