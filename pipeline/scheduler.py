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
    "twitch":     (20, 0),  # 8 PM UTC / 4 PM EST
}


# ---------------------------------------------------------------------------
# Per-niche job wrappers (top-level so APScheduler can reference them cleanly)
# ---------------------------------------------------------------------------

def _job_trading():    run_pipeline_for_niche("trading",    _app)
def _job_fitness():    run_pipeline_for_niche("fitness",    _app)
def _job_crime():      run_crime_pipeline(_app)
def _job_sports():     run_pipeline_for_niche("sports",     _app)
def _job_gaming():     run_pipeline_for_niche("gaming",     _app)
def _job_everything(): run_pipeline_for_niche("everything", _app)
def _job_kids():       run_kids_pipeline(_app)
def _job_twitch():     run_pipeline_for_niche("twitch",     _app)

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

def _job_lumi_build():
    """Generate a fresh Lumi episode concept then dispatch to GitHub Actions."""
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[lumi-scheduler] ANTHROPIC_API_KEY not set")
        return

    # Guard: skip if an episode is already building
    with _app.app_context():
        from models import LumiStory
        in_flight = LumiStory.query.filter(
            LumiStory.status.in_(["generating", "dispatched"])
        ).first()
        if in_flight:
            print(f"[lumi-scheduler] Skipping — episode #{in_flight.id} already in flight")
            return

    try:
        import anthropic, json
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content":
                "Give me ONE fresh Lumi Tales kids episode idea. "
                "Return only valid JSON: {\"title\": \"...\", \"moral\": \"...\"} "
                "Title: 4-6 words, catchy. Moral: one simple sentence for toddlers. "
                "No explanation, just JSON."
            }],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        concept = json.loads(raw)
        title = concept["title"]
        moral = concept["moral"]
    except Exception as e:
        print(f"[lumi-scheduler] Concept generation failed: {e}")
        return

    from integrations.lumi_builder import trigger_episode
    trigger_episode(title, moral, _app)
    print(f"[lumi-scheduler] Dispatched: '{title}'")

# Long-form wrappers
def _job_longform_trading():    run_longform_for_niche("trading",    _app)
def _job_longform_fitness():    run_longform_for_niche("fitness",    _app)
def _job_longform_crime():      run_longform_for_niche("crime",      _app)
def _job_longform_sports():     run_longform_for_niche("sports",     _app)
def _job_longform_gaming():     run_longform_for_niche("gaming",     _app)
def _job_longform_everything(): run_longform_for_niche("everything", _app)
def _job_longform_kids():       run_longform_for_niche("kids",       _app)

def _job_health_check():
    """
    Runs every 2 hours.
    1. Clears stuck "running" pipeline runs (server crashed mid-job).
    2. Finds failed runs from the last 24 h and retries the niche,
       unless a successful run already happened today or the failure
       was a content issue (no candidates / download failed for every pick).
    """
    import threading, time as _time
    with _app.app_context():
        from models import db, PipelineRun
        now    = datetime.utcnow()

        # --- Step 1: clear stuck "running" runs older than 30 min ---
        stuck = PipelineRun.query.filter(
            PipelineRun.status     == "running",
            PipelineRun.started_at <  now - __import__("datetime").timedelta(minutes=30),
        ).all()
        for run in stuck:
            print(f"[health] Clearing stuck run #{run.id} ({run.niche}) — was running since {run.started_at}")
            run.status = "failed"
            run.note   = (run.note or "") + " | cleared-stuck"
        if stuck:
            db.session.commit()

        # --- Step 2: find failed runs not yet retried ---
        window      = now - __import__("datetime").timedelta(hours=24)
        failed_runs = PipelineRun.query.filter(
            PipelineRun.status     == "failed",
            PipelineRun.started_at >= window,
        ).all()

        # Skip niches that had a successful run today
        successful_today = {
            r.niche for r in PipelineRun.query.filter(
                PipelineRun.status     == "completed",
                PipelineRun.started_at >= now - __import__("datetime").timedelta(hours=24),
            ).all()
        }

        # Skip content-side failures (no videos to fetch — retrying immediately won't help)
        _content_skip = ("no video candidates", "download failed", "niche not found", "no active accounts")

        retried = set()
        for run in failed_runs:
            niche = run.niche
            note  = (run.note or "").lower()
            if niche in retried:
                continue
            if niche in successful_today:
                continue
            if "retried" in note:
                continue
            if any(s in note for s in _content_skip):
                print(f"[health] Skipping content-side failure for {niche}: {run.note}")
                continue
            if niche not in NICHE_POST_TIMES and niche != "crime":
                continue

            print(f"[health] ⚠️  Failed run #{run.id} ({niche}): {run.note} — scheduling retry")
            run.note = (run.note or "") + " | retried"
            db.session.commit()
            retried.add(niche)

            def _retry(n=niche):
                _time.sleep(60)  # brief pause so the health check itself doesn't hold the lock
                if n == "crime":
                    run_crime_pipeline(_app)
                else:
                    run_pipeline_for_niche(n, _app)

            threading.Thread(target=_retry, daemon=True).start()
            _time.sleep(5)   # stagger multiple retries so they queue up, not pile up

        if retried:
            print(f"[health] Retrying niches: {', '.join(retried)}")
        else:
            print(f"[health] ✅ All pipeline runs look healthy")


_JOB_FUNCS = {
    "trading":    _job_trading,
    "fitness":    _job_fitness,
    "crime":      _job_crime,
    "sports":     _job_sports,
    "gaming":     _job_gaming,
    "everything": _job_everything,
    "kids":       _job_kids,
    "twitch":     _job_twitch,
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
    from pipeline.locks import heavy_op
    if not heavy_op.acquire(blocking=False):
        print(f"[pipeline] Skipping {niche} — another heavy operation is already running")
        return
    try:
        _run_pipeline_for_niche_inner(niche, app)
    finally:
        heavy_op.release()


def _run_pipeline_for_niche_inner(niche: str, app):
    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, CreditBudget, ContentQueue
        from server  import _run_job, _inject_affiliate_links
        from datetime import timedelta

        # Dedupe guard — skip if a run already started for this niche in the last 10 min
        # Prevents double-posting during Render zero-downtime deploys (two instances overlap)
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        recent = PipelineRun.query.filter(
            PipelineRun.niche       == niche,
            PipelineRun.status      == "running",
            PipelineRun.started_at  >= cutoff,
        ).first()
        if recent:
            print(f"[pipeline] Skipping {niche} — run #{recent.id} already in progress")
            return

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
            import shutil as _shutil
            _shutil.move(video_path, str(dest))

            base_url  = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
            video_url = f"{base_url}/static/videos/{dest.name}"
            title     = pick.get("title", f"{niche} video")[:100]

            cap_a, cap_b = generate_captions(niche, title, add_longform_cta=True)
            cap_a, cap_b = _append_credit(cap_a, cap_b, pick)

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
            teaser_item = None
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

            mark_posted(story_id, teaser_item.id if teaser_item else None, app)

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


def _source_credit(candidate: dict) -> str:
    """Return a one-line attribution string for the bottom of a caption."""
    src = candidate.get("source_type", "")
    if src == "youtube":
        url = candidate.get("url", "")
        vid = url.split("v=")[-1].split("&")[0] if "v=" in url else ""
        return f"Credit: youtube.com/watch?v={vid}" if vid else "Credit: youtube.com"
    if src in ("reddit", "brainrot"):
        sub = candidate.get("subreddit", "Reddit")
        return f"Credit: r/{sub} (Reddit)"
    if src == "pexels":
        return "Credit: Pexels (pexels.com)"
    if src == "twitch":
        streamer = candidate.get("streamer", "")
        return f"Credit: {streamer} on Twitch" if streamer else "Credit: Twitch"
    return ""


def _append_credit(cap_a: str, cap_b: str, candidate: dict) -> tuple[str, str]:
    credit = _source_credit(candidate)
    if not credit:
        return cap_a, cap_b
    def _add(cap: str) -> str:
        return f"{cap}\n\n{credit}" if cap else credit
    return _add(cap_a), _add(cap_b)


def run_crime_pipeline(app):
    """
    Crime/horror niche pipeline:
    1. Generate a scary story via Claude
    2. Narrate with ElevenLabs
    3. Download a brainrot background clip from Reddit
    4. Composite: darkened brainrot + narration + subtitle word overlay
    5. Post to all crime accounts
    """
    from pipeline.locks import heavy_op
    if not heavy_op.acquire(blocking=False):
        print("[pipeline] Skipping crime — another heavy operation is already running")
        return
    try:
        _run_crime_pipeline_inner(app)
    finally:
        heavy_op.release()


def _run_crime_pipeline_inner(app):
    with app.app_context():
        from models import db, Niche, SocialAccount, PipelineRun, ContentQueue, CreditBudget
        from server  import _run_job
        from pipeline.crime_story import generate_story_text, build_crime_short
        from pipeline.captions    import generate_captions
        from pipeline.sources     import fetch_brainrot_clip
        from integrations.elevenlabs import generate_voiceover
        from datetime import timedelta

        niche = "crime"

        cutoff = datetime.utcnow() - timedelta(minutes=10)
        recent = PipelineRun.query.filter(
            PipelineRun.niche      == niche,
            PipelineRun.status     == "running",
            PipelineRun.started_at >= cutoff,
        ).first()
        if recent:
            print(f"[crime] Skipping — run #{recent.id} already in progress")
            return

        print(f"[crime] Starting story pipeline at {datetime.utcnow()}")
        run = PipelineRun(niche=niche, status="running", started_at=datetime.utcnow())
        db.session.add(run)
        db.session.commit()

        narration_path = None
        brainrot_path  = None
        video_path     = None

        try:
            niche_obj = Niche.query.filter_by(name=niche, is_active=True).first()
            if not niche_obj:
                run.status = "skipped"; run.note = "niche not found"
                db.session.commit(); return

            accounts = SocialAccount.query.filter_by(niche_id=niche_obj.id, is_active=True).all()
            if not accounts:
                run.status = "skipped"; run.note = "no active accounts"
                db.session.commit(); return

            # 1. Generate scary story
            title, story_text = generate_story_text(target_seconds=70)
            print(f"[crime] Story generated: {title[:60]}")

            # 2. Narrate with ElevenLabs
            narration_path, v_id, v_name = generate_voiceover(
                text=story_text, niche="crime", db_session=db.session
            )
            if not narration_path:
                run.status = "failed"; run.note = "narration generation failed"
                db.session.commit(); return

            # 3. Download a brainrot background clip
            brainrot_candidate = None
            for candidate in fetch_brainrot_clip(max_results=5):
                url = candidate.get("url")
                if url:
                    brainrot_path = _download_video(url)
                    if brainrot_path:
                        brainrot_candidate = candidate
                        break

            if not brainrot_path:
                run.status = "failed"; run.note = "brainrot clip download failed"
                db.session.commit(); return

            # 4. Build composite crime short
            video_path = build_crime_short(brainrot_path, narration_path, story_text)
            if not video_path:
                run.status = "failed"; run.note = "crime short build failed"
                db.session.commit(); return

            # 5. Move to static dir
            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            dest = static_dir / f"pipeline_crime_{run.id}.mp4"
            import shutil as _shutil
            _shutil.move(video_path, str(dest))
            video_path = None  # moved, no longer needs cleanup

            base_url  = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
            video_url = f"{base_url}/static/videos/{dest.name}"

            cap_a, cap_b = generate_captions("crime", title, add_longform_cta=True)
            if brainrot_candidate:
                cap_a, cap_b = _append_credit(cap_a, cap_b, brainrot_candidate)

            item = ContentQueue(
                niche_id=niche_obj.id, video_url=video_url,
                title=title, description=cap_a,
                platforms=list({a.platform for a in accounts}),
                use_opusclip=False, status="pending",
                upload_results={}, clipped_urls=[],
            )
            db.session.add(item)
            db.session.commit()

            _run_job(
                item.id, accounts, False,
                content_type = "crime_story",
                account_caps = {a.id: ([cap_a, cap_b][i % 2]) for i, a in enumerate(accounts)},
                account_vars = {a.id: "A" if i % 2 == 0 else "B" for i, a in enumerate(accounts)},
                ab_test_id   = None,
                voice_id     = v_id,
                voice_name   = v_name,
            )

            budget = CreditBudget.query.filter_by(service="posts_per_day", niche=niche).first()
            if budget:
                budget.current_usage += 1
                db.session.commit()

            run.status       = "completed"
            run.note         = f"crime story: {title[:60]}"
            run.video_url    = video_url
            run.completed_at = datetime.utcnow()
            db.session.commit()
            print(f"[crime] Completed: {title[:60]}")

        except Exception as e:
            import traceback
            run.status = "failed"; run.note = str(e)[:500]
            db.session.commit()
            print(f"[crime] ERROR: {e}")
            traceback.print_exc()
        finally:
            for p in [narration_path, brainrot_path, video_path]:
                try:
                    if p and os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass


def _download_video(url: str, max_duration: int = None) -> str | None:
    """Download a video to a temp file. Returns path or None."""
    import re

    # Twitch clip URLs — yt-dlp is unreliable; use GQL downloader directly
    twitch_clip_pattern = re.compile(r"twitch\.tv/(?:[^/]+/clip/|clips\.twitch\.tv/)([A-Za-z0-9_-]+)")
    twitch_match = twitch_clip_pattern.search(url)
    if twitch_match:
        clip_slug = twitch_match.group(1)
        try:
            from integrations.twitch_eventsub import _download_twitch_clip
            return _download_twitch_clip(clip_slug)
        except Exception as e:
            print(f"[pipeline] Twitch GQL download failed for {clip_slug}: {e}")
            return None

    # Direct MP4 URLs (Pexels, etc.) — stream download via requests
    if re.search(r"pexels\.com|\.mp4(\?|$)", url):
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            import requests as _req
            with _req.get(url, stream=True, timeout=120,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
                r.raise_for_status()
                with open(tmp.name, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
            size = Path(tmp.name).stat().st_size
            return tmp.name if size > 0 else None
        except Exception as e:
            print(f"[pipeline] Direct download failed for {url}: {e}")
            return None

    # General fallback — yt-dlp
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

    # Build a full Lumi episode (GitHub Actions) Mon/Wed/Fri at 10am UTC
    # GitHub produces video + Short + thumbnail, Render posts to YouTube automatically
    _scheduler.add_job(
        func               = _job_lumi_build,
        trigger            = "cron",
        day_of_week        = "mon,wed,fri",
        hour               = 10,
        minute             = 0,
        id                 = "lumi_build",
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
        "gaming":     _job_longform_gaming,
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

    # Health check — every 2 hours: clear stuck runs, retry failed niches
    _scheduler.add_job(
        func               = _job_health_check,
        trigger            = "interval",
        hours              = 2,
        id                 = "health_check",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )

    _scheduler.start()
    print(f"[scheduler] Started — {len(NICHE_POST_TIMES)} daily jobs + health check every 2h")
    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def trigger_now(niche: str, app):
    """Manually trigger a pipeline run for a niche right now."""
    import threading
    if niche == "crime":
        threading.Thread(target=run_crime_pipeline, args=[app], daemon=True).start()
    else:
        threading.Thread(target=run_pipeline_for_niche, args=[niche, app], daemon=True).start()
