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

from pipeline.sources   import get_candidates, NICHE_CONFIG
from pipeline.captions  import generate_captions
from pipeline.lumi_tales import NICHE_MAX_DURATION
from pipeline.longform  import run_longform_for_niche, LONGFORM_SCHEDULE

_scheduler: BackgroundScheduler | None = None
_app = None  # set by init_scheduler; used by job wrappers so app is never pickled

# Post time windows per niche (hour, minute) in UTC
NICHE_POST_TIMES = {
    "trading":    (13, 0),
    "fitness":    (11, 0),
    "crime":      (1,  0),
    "sports":     (17, 0),
    "gaming":     (15, 0),
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
def _job_gaming():     run_pipeline_for_niche("gaming",     _app)
def _job_everything(): run_pipeline_for_niche("everything", _app)
def _job_kids():       run_kids_pipeline(_app)

def _job_refresh_tokens():
    from server import refresh_instagram_tokens, refresh_youtube_tokens
    refresh_instagram_tokens()
    refresh_youtube_tokens()

def _job_scrape_and_learn():
    from integrations.instagram_scraper import scrape_all_accounts
    from pipeline.style_learner import learn_all_niches
    scrape_all_accounts(_app)
    learn_all_niches(_app)

def _job_generate_lumi_story():
    from pipeline.lumi_tales import generate_and_store
    generate_and_store(_app)

# Long-form wrappers
def _job_longform_trading():    run_longform_for_niche("trading",    _app)
def _job_longform_fitness():    run_longform_for_niche("fitness",    _app)
def _job_longform_crime():      run_longform_for_niche("crime",      _app)
def _job_longform_sports():     run_longform_for_niche("sports",     _app)
def _job_longform_gaming():     run_longform_for_niche("gaming",     _app)
def _job_longform_everything(): run_longform_for_niche("everything", _app)
def _job_longform_kids():       run_longform_for_niche("kids",       _app)

_JOB_FUNCS = {
    "trading":    _job_trading,
    "fitness":    _job_fitness,
    "crime":      _job_crime,
    "sports":     _job_sports,
    "gaming":     _job_gaming,
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
            max_dur    = NICHE_MAX_DURATION.get(niche)
            video_path = _download_video(pick["url"], max_duration=max_dur)
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

            cap_a, cap_b = generate_captions(niche, title, add_longform_cta=True)

            # Generate AI voiceover with random voice (learning system picks best over time)
            from integrations.elevenlabs import generate_voiceover, overlay_voiceover
            audio_path, chosen_voice_id, chosen_voice_name = generate_voiceover(
                text=title, niche=niche, db_session=db.session
            )
            if audio_path and str(dest).endswith(".mp4"):
                voiced = overlay_voiceover(str(dest), audio_path)
                if voiced:
                    import shutil
                    shutil.move(voiced, str(dest))

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
                voice_id     = chosen_voice_id,
                voice_name   = chosen_voice_name,
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


def run_kids_pipeline(app):
    """
    Lumi Tales dual-format pipeline:
    1. Teaser (30-45s Short) → YouTube Shorts + Instagram Reels
    2. Full video (3-5min)   → YouTube only
    Both are generated from the same story script.
    """
    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, ContentQueue, LumiStory
        from server import _run_job, _inject_affiliate_links
        from pipeline.lumi_tales import (
            get_next_story, build_teaser, build_full_video,
            generate_and_store, mark_posted
        )
        from integrations.elevenlabs import generate_voiceover, overlay_voiceover
        from integrations import youtube as yt_integration
        from pathlib import Path

        print(f"[kids] Starting Lumi Tales pipeline at {datetime.utcnow()}")

        run = PipelineRun(niche="kids", status="running", started_at=datetime.utcnow())
        db.session.add(run)
        db.session.commit()

        try:
            niche_obj = Niche.query.filter_by(name="kids", is_active=True).first()
            if not niche_obj:
                run.status = "skipped"; run.note = "kids niche not found"
                db.session.commit(); return

            yt_accounts = SocialAccount.query.filter_by(
                niche_id=niche_obj.id, platform="youtube", is_active=True
            ).all()
            ig_accounts = SocialAccount.query.filter_by(
                niche_id=niche_obj.id, platform="instagram", is_active=True
            ).all()

            if not yt_accounts:
                run.status = "skipped"; run.note = "no YouTube accounts for kids"
                db.session.commit(); return

            # Get or generate today's story
            story = get_next_story(app)
            if not story:
                print("[kids] No ready stories — generating one now")
                generate_and_store(app)
                story = get_next_story(app)
            if not story:
                run.status = "failed"; run.note = "story generation failed"
                db.session.commit(); return

            story_id   = story["id"]
            teaser     = build_teaser(story)
            full_video = build_full_video(story)

            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            base_url   = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")

            # ----------------------------------------------------------------
            # Step 1 — TEASER (Short for YouTube + Instagram Reel)
            # ----------------------------------------------------------------
            teaser_candidates = get_candidates("kids")
            teaser_real       = [c for c in teaser_candidates if c.get("url") and
                                  c.get("source_type") != "ai_pending"]
            if teaser_real:
                teaser_vid = _download_video(teaser_real[0]["url"])
                if teaser_vid:
                    # Trim to 45s max using moviepy
                    try:
                        from moviepy.editor import VideoFileClip
                        clip = VideoFileClip(teaser_vid)
                        if clip.duration > 45:
                            clip = clip.subclip(0, 45)
                        dest_t = static_dir / f"lumi_teaser_{story_id}.mp4"
                        clip.write_videofile(str(dest_t), codec="libx264",
                                             audio_codec="aac", logger=None)
                        clip.close()
                        teaser_vid = str(dest_t)
                    except Exception as e:
                        print(f"[kids] Teaser trim failed: {e}")

                    # Add voiceover
                    audio_path, v_id, v_name = generate_voiceover(
                        teaser["narration"], niche="kids", db_session=db.session
                    )
                    if audio_path:
                        voiced = overlay_voiceover(teaser_vid, audio_path)
                        if voiced:
                            import shutil
                            shutil.move(voiced, teaser_vid)

                    teaser_url = f"{base_url}/static/videos/lumi_teaser_{story_id}.mp4"

                    teaser_item = ContentQueue(
                        niche_id=niche_obj.id, video_url=teaser_url,
                        title=story["title"], description=teaser["caption"],
                        platforms=["youtube", "instagram"], use_opusclip=False,
                        status="pending", upload_results={}, clipped_urls=[],
                    )
                    db.session.add(teaser_item)
                    db.session.commit()

                    # Upload as Short to YouTube + Reel to Instagram
                    _run_job(
                        teaser_item.id,
                        yt_accounts + ig_accounts,
                        False,
                        content_type = "lumi_short",
                        voice_id     = v_id,
                        voice_name   = v_name,
                    )
                    print(f"[kids] Teaser posted: {story['title']}")

            # ----------------------------------------------------------------
            # Step 2 — FULL VIDEO (YouTube only, 3-5 min)
            # ----------------------------------------------------------------
            full_candidates = get_candidates("kids")
            full_real       = [c for c in full_candidates if c.get("url") and
                                c.get("source_type") != "ai_pending"]
            if full_real:
                full_vid = _download_video(full_real[0]["url"])
                if full_vid:
                    # Pad/loop to fill ~3 min using moviepy
                    try:
                        from moviepy.editor import VideoFileClip, concatenate_videoclips
                        clip = VideoFileClip(full_vid)
                        target = 180  # 3 min minimum
                        if clip.duration < target:
                            loops  = int(target // clip.duration) + 1
                            clip   = concatenate_videoclips([clip] * loops)
                            clip   = clip.subclip(0, target)
                        dest_f = static_dir / f"lumi_full_{story_id}.mp4"
                        clip.write_videofile(str(dest_f), codec="libx264",
                                             audio_codec="aac", logger=None)
                        clip.close()
                        full_vid = str(dest_f)
                    except Exception as e:
                        print(f"[kids] Full video extend failed: {e}")

                    # Full narration voiceover
                    audio_path, v_id, v_name = generate_voiceover(
                        full_video["narration"], niche="kids", db_session=db.session
                    )
                    if audio_path:
                        voiced = overlay_voiceover(full_vid, audio_path)
                        if voiced:
                            import shutil
                            shutil.move(voiced, full_vid)

                    full_url = f"{base_url}/static/videos/lumi_full_{story_id}.mp4"

                    full_item = ContentQueue(
                        niche_id=niche_obj.id, video_url=full_url,
                        title=story["title"], description=full_video["caption"],
                        platforms=["youtube"], use_opusclip=False,
                        status="pending", upload_results={}, clipped_urls=[],
                    )
                    db.session.add(full_item)
                    db.session.commit()

                    _run_job(
                        full_item.id, yt_accounts, False,
                        content_type = "lumi_full",
                        voice_id     = v_id,
                        voice_name   = v_name,
                    )
                    print(f"[kids] Full video posted: {story['title']}")

            mark_posted(story_id, teaser_item.id if teaser_real else None, app)

            run.status       = "completed"
            run.note         = f"Lumi Tales: {story['title']}"
            run.completed_at = datetime.utcnow()
            db.session.commit()

        except Exception as e:
            import traceback
            run.status = "failed"; run.note = str(e)[:500]
            db.session.commit()
            print(f"[kids] ERROR: {e}")
            traceback.print_exc()


def _download_video(url: str, max_duration: int = None) -> str | None:
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
        if max_duration:
            ydl_opts["match_filter"] = yt_dlp.utils.match_filter_func(
                f"duration <= {max_duration}"
            )
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
            func               = _JOB_FUNCS[niche],
            trigger            = "cron",
            hour               = hour,
            minute             = minute,
            id                 = f"pipeline_{niche}",
            replace_existing   = True,
            misfire_grace_time = 3600,
        )

    # Refresh OAuth tokens every Monday at 3am UTC
    _scheduler.add_job(
        func               = _job_refresh_tokens,
        trigger            = "cron",
        day_of_week        = "mon",
        hour               = 3,
        minute             = 0,
        id                 = "token_refresh",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )

    # Generate a new Lumi Tales story every day at 8am UTC (ahead of 9pm post time)
    _scheduler.add_job(
        func               = _job_generate_lumi_story,
        trigger            = "cron",
        hour               = 8,
        minute             = 0,
        id                 = "lumi_generate",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )

    # Scrape reference accounts + re-learn style every Wednesday at 4am UTC
    _scheduler.add_job(
        func               = _job_scrape_and_learn,
        trigger            = "cron",
        day_of_week        = "wed",
        hour               = 4,
        minute             = 0,
        id                 = "scrape_and_learn",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )

    # Long-form YouTube jobs — 2x/week per niche per LONGFORM_SCHEDULE
    _LONGFORM_WRAPPERS = {
        "trading":    _job_longform_trading,
        "fitness":    _job_longform_fitness,
        "crime":      _job_longform_crime,
        "sports":     _job_longform_sports,
        "anatomy":    _job_longform_anatomy,
        "everything": _job_longform_everything,
        "kids":       _job_longform_kids,
    }
    for lf_niche, slots in LONGFORM_SCHEDULE.items():
        for idx, (dow, hour) in enumerate(slots):
            _scheduler.add_job(
                func               = _LONGFORM_WRAPPERS[lf_niche],
                trigger            = "cron",
                day_of_week        = dow,
                hour               = hour,
                minute             = 0,
                id                 = f"longform_{lf_niche}_{idx}",
                replace_existing   = True,
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
