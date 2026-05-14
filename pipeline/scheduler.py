"""
Daily content pipeline scheduler.
Initialised once at Flask startup via init_scheduler(app).
"""

import os
import random
import tempfile
from datetime import datetime, date
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore

from pipeline.sources  import get_candidates, NICHE_CONFIG
from pipeline.captions import generate_captions

_scheduler: BackgroundScheduler | None = None
_app = None  # set by init_scheduler; used by job wrappers so app is never pickled

# Post time windows per niche (hour, minute) in UTC
NICHE_POST_TIMES = {
    "trading":    (13, 0),
    "fitness":    (11, 0),
    "crime":      (1,  0),
    "sports":     (17, 0),
    "anatomy":    (15, 0),
    "everything": (19, 0),
    "kids":       (21, 0),
}


# ---------------------------------------------------------------------------
# Per-niche job wrappers (top-level so APScheduler can reference them cleanly)
# ---------------------------------------------------------------------------

def _job_trading():    run_pipeline_for_niche("trading",    _app)
def _job_fitness():    run_pipeline_for_niche("fitness",    _app)
def _job_crime():      run_pipeline_for_niche("crime",      _app)
def _job_sports():     run_pipeline_for_niche("sports",     _app)
def _job_anatomy():    run_pipeline_for_niche("anatomy",    _app)
def _job_everything(): run_pipeline_for_niche("everything", _app)
def _job_kids():       run_pipeline_for_niche("kids",       _app)

_JOB_FUNCS = {
    "trading":    _job_trading,
    "fitness":    _job_fitness,
    "crime":      _job_crime,
    "sports":     _job_sports,
    "anatomy":    _job_anatomy,
    "everything": _job_everything,
    "kids":       _job_kids,
}


# ---------------------------------------------------------------------------
# Credit budget helpers
# ---------------------------------------------------------------------------

def _posts_per_day(niche: str, app) -> int:
    from models import CreditBudget
    import calendar

    with app.app_context():
        budget = CreditBudget.query.filter_by(service="posts_per_day", niche=niche).first()
        if not budget:
            return 1

        today     = date.today()
        days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
        remaining = max(budget.monthly_limit - budget.current_usage, 0)
        return max(1, remaining // max(days_left, 1))


# ---------------------------------------------------------------------------
# Core pipeline job
# ---------------------------------------------------------------------------

def run_pipeline_for_niche(niche: str, app):
    """Download a video, generate captions, and post. Called by the scheduler."""
    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, CreditBudget, ContentQueue
        from server  import _run_job, _inject_affiliate_links

        print(f"[pipeline] Starting job for niche={niche} at {datetime.utcnow()}")

        run = PipelineRun(niche=niche, status="running", started_at=datetime.utcnow())
        db.session.add(run)
        db.session.commit()

        try:
            niche_obj = Niche.query.filter_by(name=niche, is_active=True).first()
            if not niche_obj:
                run.status = "skipped"; run.note = "niche not found"
                db.session.commit(); return

            accounts = SocialAccount.query.filter_by(niche_id=niche_obj.id, is_active=True).all()
            if not accounts:
                run.status = "skipped"; run.note = "no active accounts"
                db.session.commit(); return

            yt_key     = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_CLIENT_ID")
            candidates = get_candidates(niche, youtube_api_key=yt_key)

            real = [c for c in candidates if c.get("source_type") != "ai_pending" and c.get("url")]
            if not real:
                run.status = "skipped"; run.note = "no video candidates found"
                db.session.commit(); return

            pick       = random.choice(real[:5])
            video_path = _download_video(pick["url"])
            if not video_path:
                run.status = "failed"; run.note = f"download failed: {pick['url']}"
                db.session.commit(); return

            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            dest = static_dir / f"pipeline_{niche}_{run.id}.mp4"
            Path(video_path).rename(dest)

            base_url  = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
            video_url = f"{base_url}/static/videos/{dest.name}"
            title     = pick.get("title", f"{niche} video")[:100]

            cap_a, cap_b = generate_captions(niche, title)

            item = ContentQueue(
                niche_id       = niche_obj.id,
                video_url      = video_url,
                title          = title,
                description    = cap_a,
                platforms      = list({a.platform for a in accounts}),
                use_opusclip   = False,
                status         = "pending",
                upload_results = {},
                clipped_urls   = [],
            )
            db.session.add(item)
            db.session.commit()

            caption_variants = [cap_a, cap_b]
            _run_job(
                item.id, accounts, False,
                content_type = pick.get("source_type", "sourced"),
                account_caps = {a.id: caption_variants[i % 2] for i, a in enumerate(accounts)},
                account_vars = {a.id: "A" if i % 2 == 0 else "B" for i, a in enumerate(accounts)},
                ab_test_id   = None,
            )

            budget = CreditBudget.query.filter_by(service="posts_per_day", niche=niche).first()
            if budget:
                budget.current_usage += 1
                db.session.commit()

            run.status       = "completed"
            run.note         = f"posted: {title[:60]}"
            run.video_url    = video_url
            run.completed_at = datetime.utcnow()
            db.session.commit()
            print(f"[pipeline] Completed {niche}: {title[:60]}")

        except Exception as e:
            import traceback
            run.status = "failed"
            run.note   = str(e)[:500]
            db.session.commit()
            print(f"[pipeline] ERROR {niche}: {e}")
            traceback.print_exc()


def _download_video(url: str) -> str | None:
    """Download a video to a temp file using yt-dlp. Returns path or None."""
    try:
        import yt_dlp
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        ydl_opts = {
            "format":              "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl":             tmp.name,
            "quiet":               True,
            "merge_output_format": "mp4",
            "max_filesize":        150 * 1024 * 1024,
            "extractor_args":      {"youtube": {"player_client": ["web"]}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return tmp.name if Path(tmp.name).exists() and Path(tmp.name).stat().st_size > 0 else None
    except Exception as e:
        print(f"[pipeline] Download failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Scheduler init
# ---------------------------------------------------------------------------

def init_scheduler(app):
    global _scheduler, _app
    if _scheduler and _scheduler.running:
        return _scheduler

    _app = app  # store reference so job wrappers can reach it without pickling

    _scheduler = BackgroundScheduler(
        jobstores={"default": MemoryJobStore()},
        timezone="UTC",
    )

    for niche, (hour, minute) in NICHE_POST_TIMES.items():
        _scheduler.add_job(
            func             = _JOB_FUNCS[niche],
            trigger          = "cron",
            hour             = hour,
            minute           = minute,
            id               = f"pipeline_{niche}",
            replace_existing = True,
            misfire_grace_time = 3600,
        )

    _scheduler.start()
    print(f"[scheduler] Started — {len(NICHE_POST_TIMES)} daily jobs scheduled")
    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def trigger_now(niche: str, app):
    """Manually trigger a pipeline run for a niche right now."""
    import threading
    threading.Thread(target=run_pipeline_for_niche, args=[niche, app], daemon=True).start()
