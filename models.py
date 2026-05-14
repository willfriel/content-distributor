from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from config import Config

db = SQLAlchemy()


class Niche(db.Model):
    __tablename__ = "niches"

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    accounts = db.relationship("SocialAccount", backref="niche", lazy=True, cascade="all, delete-orphan")
    content  = db.relationship("ContentQueue",  backref="niche", lazy=True)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "display_name": self.display_name,
            "is_active": self.is_active, "account_count": len(self.accounts),
        }


class SocialAccount(db.Model):
    __tablename__ = "social_accounts"

    id                   = db.Column(db.Integer, primary_key=True)
    niche_id             = db.Column(db.Integer, db.ForeignKey("niches.id"), nullable=False)
    platform             = db.Column(db.String(20), nullable=False)
    account_name         = db.Column(db.String(200), nullable=False)
    account_id           = db.Column(db.String(200))
    encrypted_credentials = db.Column(db.Text)
    is_active            = db.Column(db.Boolean, default=True)
    needs_reauth         = db.Column(db.Boolean, default=False)
    token_expires_at     = db.Column(db.DateTime, nullable=True)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at           = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_credentials(self, credentials: dict):
        import json
        f = Config.get_fernet()
        self.encrypted_credentials = f.encrypt(json.dumps(credentials).encode()).decode()

    def get_credentials(self) -> dict:
        import json
        if not self.encrypted_credentials:
            return {}
        f = Config.get_fernet()
        return json.loads(f.decrypt(self.encrypted_credentials.encode()).decode())

    def to_dict(self, include_credentials=False):
        data = {
            "id": self.id, "niche_id": self.niche_id,
            "niche_name": self.niche.name if self.niche else None,
            "platform": self.platform, "account_name": self.account_name,
            "account_id": self.account_id, "is_active": self.is_active,
            "needs_reauth": self.needs_reauth, "created_at": self.created_at.isoformat(),
        }
        if include_credentials:
            data["credentials"] = self.get_credentials()
        return data


class ContentQueue(db.Model):
    __tablename__ = "content_queue"

    id              = db.Column(db.Integer, primary_key=True)
    niche_id        = db.Column(db.Integer, db.ForeignKey("niches.id"), nullable=False)
    video_url       = db.Column(db.Text, nullable=False)
    title           = db.Column(db.String(500))
    description     = db.Column(db.Text)
    status          = db.Column(db.String(50), default="pending")
    use_opusclip    = db.Column(db.Boolean, default=False)
    platforms       = db.Column(db.JSON, default=list)
    upload_results  = db.Column(db.JSON, default=dict)
    opusclip_job_id = db.Column(db.String(200))
    clipped_urls    = db.Column(db.JSON, default=list)
    error_message   = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at    = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id, "niche_id": self.niche_id,
            "niche_name": self.niche.name if self.niche else None,
            "video_url": self.video_url, "title": self.title,
            "description": self.description, "status": self.status,
            "use_opusclip": self.use_opusclip, "platforms": self.platforms,
            "upload_results": self.upload_results, "opusclip_job_id": self.opusclip_job_id,
            "clipped_urls": self.clipped_urls, "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ABTest(db.Model):
    __tablename__ = "ab_tests"

    id           = db.Column(db.Integer, primary_key=True)
    niche        = db.Column(db.String(100))
    content_type = db.Column(db.String(50))
    variants     = db.Column(db.JSON)        # {"A": "caption A", "B": "caption B"}
    winner       = db.Column(db.String(10))
    status       = db.Column(db.String(20), default="running")  # running / concluded
    started_at   = db.Column(db.DateTime, default=datetime.utcnow)
    concluded_at = db.Column(db.DateTime)

    posts = db.relationship("PostMetrics", backref="ab_test", lazy=True)

    def to_dict(self):
        return {
            "id": self.id, "niche": self.niche, "content_type": self.content_type,
            "variants": self.variants, "winner": self.winner, "status": self.status,
            "started_at": self.started_at.isoformat(),
            "concluded_at": self.concluded_at.isoformat() if self.concluded_at else None,
        }


class TrackedLink(db.Model):
    __tablename__ = "tracked_links"

    id          = db.Column(db.Integer, primary_key=True)
    slug        = db.Column(db.String(100), unique=True, nullable=False)
    label       = db.Column(db.String(200))
    destination = db.Column(db.Text, nullable=False)
    niche       = db.Column(db.String(100))
    link_type   = db.Column(db.String(50), default="affiliate")  # affiliate / website / social
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    clicks = db.relationship("LinkClick", backref="link", lazy=True, cascade="all, delete-orphan")

    def to_dict(self, include_stats=False):
        d = {
            "id": self.id, "slug": self.slug, "label": self.label,
            "destination": self.destination, "niche": self.niche,
            "link_type": self.link_type, "total_clicks": len(self.clicks),
            "is_active": self.is_active, "created_at": self.created_at.isoformat(),
        }
        if include_stats:
            from collections import Counter
            d["sources"] = dict(Counter(c.source for c in self.clicks))
        return d


class LinkClick(db.Model):
    __tablename__ = "link_clicks"

    id         = db.Column(db.Integer, primary_key=True)
    link_id    = db.Column(db.Integer, db.ForeignKey("tracked_links.id"), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey("content_queue.id"), nullable=True)
    source     = db.Column(db.String(100))  # youtube / instagram / tiktok / linktree / direct / other
    referrer   = db.Column(db.String(500))
    user_agent = db.Column(db.String(500))
    clicked_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "link_id": self.link_id, "content_id": self.content_id,
            "source": self.source, "referrer": self.referrer,
            "clicked_at": self.clicked_at.isoformat(),
        }


class CreditBudget(db.Model):
    """Monthly credit quota per service per niche. Drives posts-per-day math."""
    __tablename__ = "credit_budgets"

    id            = db.Column(db.Integer, primary_key=True)
    service       = db.Column(db.String(100), nullable=False)  # elevenlabs / higgsfield / posts_per_day
    niche         = db.Column(db.String(100))                  # null = applies to all niches
    monthly_limit = db.Column(db.Integer, default=30)          # units (posts, chars, credits)
    current_usage = db.Column(db.Integer, default=0)
    reset_day     = db.Column(db.Integer, default=1)           # day of month to reset usage
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def posts_per_day_remaining(self) -> int:
        import calendar
        from datetime import date
        today     = date.today()
        days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
        remaining = max(self.monthly_limit - self.current_usage, 0)
        return max(1, remaining // max(days_left, 1))

    def to_dict(self):
        return {
            "id": self.id, "service": self.service, "niche": self.niche,
            "monthly_limit": self.monthly_limit, "current_usage": self.current_usage,
            "remaining": max(self.monthly_limit - self.current_usage, 0),
            "posts_per_day": self.posts_per_day_remaining(),
        }


class PipelineRun(db.Model):
    """Log of every automated pipeline execution."""
    __tablename__ = "pipeline_runs"

    id           = db.Column(db.Integer, primary_key=True)
    niche        = db.Column(db.String(100), nullable=False)
    status       = db.Column(db.String(20), default="running")  # running/completed/failed/skipped
    note         = db.Column(db.String(500))
    video_url    = db.Column(db.Text)
    started_at   = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id, "niche": self.niche, "status": self.status,
            "note": self.note, "video_url": self.video_url,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class LongFormVideo(db.Model):
    """A planned or completed long-form YouTube video."""
    __tablename__ = "longform_videos"

    id           = db.Column(db.Integer, primary_key=True)
    niche        = db.Column(db.String(100), nullable=False)
    title        = db.Column(db.String(300), nullable=False)
    description  = db.Column(db.Text)       # YouTube description with chapters
    script       = db.Column(db.JSON)       # chapters with narration + visual prompts
    tags         = db.Column(db.JSON, default=list)
    thumbnail_desc = db.Column(db.Text)     # prompt for thumbnail generation
    status       = db.Column(db.String(30), default="draft")  # draft/ready/rendering/posted/failed
    youtube_url  = db.Column(db.String(300))
    content_id   = db.Column(db.Integer, db.ForeignKey("content_queue.id"), nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    posted_at    = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id, "niche": self.niche, "title": self.title,
            "description": self.description, "script": self.script,
            "tags": self.tags, "thumbnail_desc": self.thumbnail_desc,
            "status": self.status, "youtube_url": self.youtube_url,
            "content_id": self.content_id,
            "generated_at": self.generated_at.isoformat(),
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


class LumiStory(db.Model):
    """A generated Lumi Tales story, ready for video production."""
    __tablename__ = "lumi_stories"

    id           = db.Column(db.Integer, primary_key=True)
    character    = db.Column(db.String(100), nullable=False)
    style        = db.Column(db.String(10), nullable=False)   # A, B, or C
    title        = db.Column(db.String(300), nullable=False)
    moral        = db.Column(db.String(500))
    script       = db.Column(db.JSON)    # list of scene dicts
    status       = db.Column(db.String(30), default="draft")  # draft/ready/posted
    content_id   = db.Column(db.Integer, db.ForeignKey("content_queue.id"), nullable=True)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    posted_at    = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id, "character": self.character, "style": self.style,
            "title": self.title, "moral": self.moral, "script": self.script,
            "status": self.status, "content_id": self.content_id,
            "generated_at": self.generated_at.isoformat(),
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


class ReferenceAccount(db.Model):
    """Instagram/YouTube accounts we scrape for style training."""
    __tablename__ = "reference_accounts"

    id             = db.Column(db.Integer, primary_key=True)
    handle         = db.Column(db.String(200), unique=True, nullable=False)
    platform       = db.Column(db.String(20), default="instagram")
    niche_hint     = db.Column(db.String(100))   # null = applies to all niches
    is_active      = db.Column(db.Boolean, default=True)
    last_scraped_at = db.Column(db.DateTime)
    post_count     = db.Column(db.Integer, default=0)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    posts = db.relationship("ScrapedPost", backref="account", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "handle": self.handle, "platform": self.platform,
            "niche_hint": self.niche_hint, "is_active": self.is_active,
            "post_count": self.post_count,
            "last_scraped_at": self.last_scraped_at.isoformat() if self.last_scraped_at else None,
        }


class ScrapedPost(db.Model):
    """A single post scraped from a reference account."""
    __tablename__ = "scraped_posts"

    id              = db.Column(db.Integer, primary_key=True)
    account_id      = db.Column(db.Integer, db.ForeignKey("reference_accounts.id"), nullable=False)
    shortcode       = db.Column(db.String(200), unique=True, nullable=False)
    caption         = db.Column(db.Text)
    hashtags        = db.Column(db.JSON, default=list)
    likes           = db.Column(db.Integer, default=0)
    comments        = db.Column(db.Integer, default=0)
    views           = db.Column(db.Integer, default=0)
    engagement_rate = db.Column(db.Float, default=0.0)
    posted_at       = db.Column(db.DateTime)
    scraped_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "shortcode": self.shortcode, "caption": self.caption,
            "hashtags": self.hashtags, "likes": self.likes, "comments": self.comments,
            "views": self.views, "engagement_rate": self.engagement_rate,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


class StyleGuide(db.Model):
    """Learned style guidelines per niche, generated from scraped posts."""
    __tablename__ = "style_guides"

    id           = db.Column(db.Integer, primary_key=True)
    niche        = db.Column(db.String(100), unique=True, nullable=False)
    guidelines   = db.Column(db.JSON)   # rich dict of patterns
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "niche": self.niche, "guidelines": self.guidelines,
            "generated_at": self.generated_at.isoformat(),
        }


class PostMetrics(db.Model):
    __tablename__ = "post_metrics"

    id                 = db.Column(db.Integer, primary_key=True)
    content_id         = db.Column(db.Integer, db.ForeignKey("content_queue.id"), nullable=True)
    account_id         = db.Column(db.Integer, db.ForeignKey("social_accounts.id"), nullable=False)
    niche              = db.Column(db.String(100))
    platform           = db.Column(db.String(20), nullable=False)
    post_id            = db.Column(db.String(200), nullable=False)
    caption            = db.Column(db.Text)
    content_type       = db.Column(db.String(50), default="general")
    ab_test_id         = db.Column(db.Integer, db.ForeignKey("ab_tests.id"), nullable=True)
    ab_variant         = db.Column(db.String(10))
    voice_id           = db.Column(db.String(100))
    voice_name         = db.Column(db.String(200))

    views              = db.Column(db.Integer, default=0)
    likes              = db.Column(db.Integer, default=0)
    comments           = db.Column(db.Integer, default=0)
    shares             = db.Column(db.Integer, default=0)
    saves              = db.Column(db.Integer, default=0)
    reach              = db.Column(db.Integer, default=0)
    engagement_score   = db.Column(db.Float, default=0.0)

    posted_at          = db.Column(db.DateTime, default=datetime.utcnow)
    metrics_fetched_at = db.Column(db.DateTime)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship("SocialAccount", backref="post_metrics")

    def compute_engagement(self):
        base = max(self.reach or self.views or 1, 1)
        raw  = self.likes + (self.comments * 2) + (self.shares * 3) + (self.saves * 2)
        self.engagement_score = round(raw / base * 100, 4)

    def to_dict(self):
        return {
            "id": self.id, "content_id": self.content_id, "account_id": self.account_id,
            "account_name": self.account.account_name if self.account else None,
            "niche": self.niche, "platform": self.platform, "post_id": self.post_id,
            "caption": self.caption, "content_type": self.content_type,
            "ab_test_id": self.ab_test_id, "ab_variant": self.ab_variant,
            "voice_id": self.voice_id, "voice_name": self.voice_name,
            "views": self.views, "likes": self.likes, "comments": self.comments,
            "shares": self.shares, "saves": self.saves, "reach": self.reach,
            "engagement_score": self.engagement_score,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "metrics_fetched_at": self.metrics_fetched_at.isoformat() if self.metrics_fetched_at else None,
        }
