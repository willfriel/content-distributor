"""
Long-form YouTube video pipeline.
Claude writes the script → multiple clips sourced → stitched with moviepy
→ ElevenLabs narration → uploaded as full YouTube video.
Shorts act as marketing teasers pointing back to the full video.
"""

import os
import json
import random
import tempfile
import shutil
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Niche long-form formats
# ---------------------------------------------------------------------------

LONGFORM_FORMATS = {
    "trading": {
        "format":      "trade_walkthrough",
        "title_style": "I Took This Trade LIVE — Here's Exactly What Happened",
        "structure":   ["hook", "setup", "entry", "managing_the_trade", "result", "lessons"],
        "target_mins": 12,
        "tags":        ["trading", "stocks", "crypto", "forex", "daytrading", "investing",
                        "stockmarket", "tradingsetup", "technicalanalysis", "finance"],
    },
    "fitness": {
        "format":      "fitness_and_mindset",
        "title_style": "The Complete Guide to [Topic] — Body AND Mind",
        "structure":   ["hook", "why_it_matters", "the_mental_side", "common_mistakes",
                        "correct_approach", "daily_habits", "results"],
        "target_mins": 12,
        "tags":        ["fitness", "workout", "gym", "mindset", "selfimprovement", "discipline",
                        "motivation", "habits", "mentalhealth", "grindset"],
    },
    "crime": {
        "format":      "case_breakdown",
        "title_style": "The [Case Name] — The Full Story Nobody Told You",
        "structure":   ["hook", "the_victim", "the_crime", "investigation",
                        "suspects", "key_evidence", "verdict", "aftermath"],
        "target_mins": 15,
        "tags":        ["truecrime", "crime", "mystery", "coldcase", "unsolved",
                        "criminal", "investigation", "detective", "murder", "documentary"],
    },
    "sports": {
        "format":      "story_deep_dive",
        "title_style": "The Night [Athlete/Team] Did the Impossible",
        "structure":   ["hook", "context", "the_buildup", "the_moment",
                        "the_reaction", "the_legacy", "what_it_means"],
        "target_mins": 12,
        "tags":        ["sports", "nba", "nfl", "soccer", "athlete", "highlights",
                        "sportsstories", "basketball", "football", "goat"],
    },
    "gaming": {
        "format":      "gaming_story",
        "title_style": "The Most Insane [Game/Moment] Story Nobody Told You",
        "structure":   ["hook", "the_setup", "the_grind", "the_moment",
                        "the_reaction", "the_legacy", "what_it_means"],
        "target_mins": 10,
        "tags":        ["gaming", "gamer", "twitch", "esports", "gameplay", "streamer",
                        "gamingmoments", "viral", "funny", "highlights"],
    },
    "everything": {
        "format":      "compilation",
        "title_style": "Top 10 Moments That Broke The Internet This Week",
        "structure":   ["intro", "moments_10_to_6", "moments_5_to_2",
                        "number_one", "honorable_mentions", "outro"],
        "target_mins": 8,
        "tags":        ["viral", "funny", "trending", "compilation", "bestof",
                        "internet", "moments", "satisfying", "amazing", "weekly"],
    },
    "kids": {
        "format":      "lumi_tales_full",
        "title_style": "Lumi Tales: [Story Title] ✨ Bedtime Story for Kids",
        "structure":   ["intro_card", "scene_1", "scene_2", "scene_3", "scene_4",
                        "scene_5", "scene_6", "scene_7", "scene_8", "outro"],
        "target_mins": 4,
        "tags":        ["lumitales", "kidsstories", "bedtimestory", "storytime",
                        "animatedstories", "kidsyoutube", "childrensbooks", "cartoon"],
    },
}

# Long-form post schedule: (day_of_week, hour UTC)
# Monday + Thursday for each niche, staggered so they don't all run at once
LONGFORM_SCHEDULE = {
    "trading":    [("mon", 12), ("thu", 12)],
    "fitness":    [("mon", 14), ("thu", 14)],
    "crime":      [("mon", 20), ("thu", 20)],
    "sports":     [("tue", 16), ("fri", 16)],
    "gaming":     [("tue", 18), ("fri", 18)],
    "everything": [("wed", 17), ("sat", 17)],
    "kids":       [("wed", 20), ("sat", 20)],
}


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

def generate_script(niche: str, topic: str = None) -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    fmt         = LONGFORM_FORMATS.get(niche, LONGFORM_FORMATS["everything"])
    target_secs = fmt["target_mins"] * 60
    structure   = fmt["structure"]
    topic       = topic or _default_topic(niche)

    prompt = f"""You are a YouTube scriptwriter creating long-form content for the {niche} niche.

Topic: {topic}
Format: {fmt['format']}
Target length: ~{fmt['target_mins']} minutes ({target_secs} seconds of narration)
Video structure sections: {", ".join(structure)}

Write a complete YouTube video script. Each section should have:
- Narration the host says out loud (conversational, engaging, not robotic)
- A visual description for what's shown on screen during that section
- Estimated duration in seconds

Rules:
- Hook must grab attention in the first 10 seconds — start with the most shocking/interesting point
- Conversational tone, like talking to a friend not reading a textbook
- Each section flows naturally into the next
- End with a strong CTA: "Subscribe and turn on notifications — we post [niche] content every week"
- Include chapter timestamps in the description

Reply in this EXACT JSON format (nothing else):
{{
  "title": "compelling YouTube title (max 70 chars)",
  "description": "full YouTube description with chapters (use format: 0:00 Intro\\n2:30 Section Name etc)",
  "thumbnail_desc": "describe the ideal thumbnail image in detail for an AI image generator",
  "tags": ["tag1", "tag2"],
  "chapters": [
    {{
      "section": "section_name",
      "narration": "exactly what the host says — full sentences, ~{target_secs // len(structure)} seconds worth",
      "visual": "what's shown on screen — specific enough for a video editor or AI generator",
      "duration_seconds": {target_secs // len(structure)}
    }}
  ]
}}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg    = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 3000,
            messages   = [{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        return json.loads(text)
    except Exception as e:
        print(f"[longform] Script generation failed for {niche}: {e}")
        return None


def _default_topic(niche: str) -> str:
    topics = {
        "trading":    "a high-probability breakout trade setup using price action",
        "fitness":    "why discipline beats motivation every time — and how to build it from scratch",
        "crime":      "a cold case that was solved decades later by a single overlooked clue",
        "sports":     "an underdog athlete who defied every prediction to become a legend",
        "gaming":     "the most insane clutch moment in competitive gaming history and what made it possible",
        "everything": "the top viral moments that broke the internet this week",
        "kids":       "a friendly little bear who learns that being different is a superpower",
    }
    return topics.get(niche, f"the most fascinating thing about {niche}")


# ---------------------------------------------------------------------------
# Video assembly
# ---------------------------------------------------------------------------

def assemble_video(script: dict, niche: str, app) -> str | None:
    """
    Assemble a long-form video from sourced clips + narration.
    Returns the output file path or None.
    """
    from pipeline.sources import get_candidates
    from pipeline.scheduler import _download_video
    from integrations.elevenlabs import generate_voiceover, overlay_voiceover

    chapters   = script.get("chapters", [])
    full_narr  = " ".join(c.get("narration", "") for c in chapters)

    # Source clips — get more candidates for a longer video
    candidates = [c for c in get_candidates(niche)
                  if c.get("url") and c.get("source_type") != "ai_pending"]
    if not candidates:
        print(f"[longform] No candidates for {niche}")
        return None

    # Download multiple clips, one per section (or reuse if not enough)
    clip_paths = []
    try:
        from moviepy.editor import VideoFileClip, concatenate_videoclips

        for i, chapter in enumerate(chapters):
            pick     = candidates[i % len(candidates)]
            vid_path = _download_video(pick["url"])
            if not vid_path:
                continue
            target_dur = chapter.get("duration_seconds", 60)
            try:
                clip = VideoFileClip(vid_path)
                # Loop short clips to fill the chapter duration
                if clip.duration < target_dur:
                    loops = int(target_dur // clip.duration) + 1
                    clip  = concatenate_videoclips([clip] * loops)
                clip = clip.subclip(0, min(clip.duration, target_dur))
                clip_paths.append((clip, vid_path))
            except Exception as e:
                print(f"[longform] Clip prep failed: {e}")

        if not clip_paths:
            return None

        # Stitch all chapter clips together
        final_clip = concatenate_videoclips([c for c, _ in clip_paths], method="compose")

        # Generate full narration and overlay
        audio_path, _, _ = generate_voiceover(full_narr, niche=niche)
        if audio_path:
            from moviepy.editor import AudioFileClip
            narr_audio = AudioFileClip(audio_path)
            narr_audio = narr_audio.subclip(0, min(narr_audio.duration, final_clip.duration))
            final_clip = final_clip.set_audio(narr_audio)

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        final_clip.write_videofile(tmp.name, codec="libx264", audio_codec="aac", logger=None)
        final_clip.close()

        # Cleanup source files
        for _, path in clip_paths:
            try:
                os.unlink(path)
            except OSError:
                pass

        return tmp.name

    except Exception as e:
        print(f"[longform] Assembly failed for {niche}: {e}")
        return None


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def generate_and_store(niche: str, app, topic: str = None) -> int | None:
    """Generate a long-form script and store it. Returns LongFormVideo ID."""
    script = generate_script(niche, topic)
    if not script:
        return None

    with app.app_context():
        from models import db, LongFormVideo
        fmt = LONGFORM_FORMATS.get(niche, {})
        record = LongFormVideo(
            niche          = niche,
            title          = script["title"],
            description    = script.get("description", ""),
            script         = script,
            tags           = script.get("tags") or fmt.get("tags", []),
            thumbnail_desc = script.get("thumbnail_desc", ""),
            status         = "ready",
        )
        db.session.add(record)
        db.session.commit()
        print(f"[longform] Script ready: '{script['title']}' ({niche})")
        return record.id


def run_longform_for_niche(niche: str, app):
    """Full pipeline: generate script → assemble video → upload to YouTube."""
    with app.app_context():
        from models import db, Niche, SocialAccount, ContentQueue, LongFormVideo, PipelineRun
        from server import _run_job
        from integrations import youtube as yt_integration

        print(f"[longform] Starting {niche} at {datetime.utcnow()}")

        run = PipelineRun(niche=f"{niche}_longform", status="running",
                          started_at=datetime.utcnow())
        db.session.add(run)
        db.session.commit()

        try:
            niche_obj   = Niche.query.filter_by(name=niche, is_active=True).first()
            yt_accounts = SocialAccount.query.filter_by(
                niche_id=niche_obj.id, platform="youtube", is_active=True
            ).all() if niche_obj else []

            if not yt_accounts:
                run.status = "skipped"; run.note = "no YouTube accounts"
                db.session.commit(); return

            # Generate script
            lf_id = generate_and_store(niche, app)
            if not lf_id:
                run.status = "failed"; run.note = "script generation failed"
                db.session.commit(); return

            lf = LongFormVideo.query.get(lf_id)
            lf.status = "rendering"
            db.session.commit()

            # Assemble video
            video_path = assemble_video(lf.script, niche, app)
            if not video_path:
                lf.status = "failed"; run.status = "failed"
                run.note = "video assembly failed"
                db.session.commit(); return

            # Serve via static
            static_dir = Path(app.root_path) / "static" / "videos"
            static_dir.mkdir(parents=True, exist_ok=True)
            dest = static_dir / f"longform_{niche}_{lf_id}.mp4"
            shutil.move(video_path, str(dest))
            base_url  = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
            video_url = f"{base_url}/static/videos/{dest.name}"

            # Create ContentQueue entry
            item = ContentQueue(
                niche_id=niche_obj.id, video_url=video_url,
                title=lf.title, description=lf.description,
                platforms=["youtube"], use_opusclip=False,
                status="pending", upload_results={}, clipped_urls=[],
            )
            db.session.add(item)
            db.session.commit()

            # Upload — NOT a Short, no duration limit
            _run_job(item.id, yt_accounts, False, content_type="longform")

            # Extract the YouTube URL from upload_results
            db.session.refresh(item)
            yt_url = None
            try:
                for _acct_results in (item.upload_results or {}).get("youtube", {}).values():
                    for r in _acct_results:
                        if r.get("url"):
                            yt_url = r["url"]
                            break
                    if yt_url:
                        break
            except Exception:
                pass

            # Update LongFormVideo record
            lf.status      = "posted"
            lf.content_id  = item.id
            lf.posted_at   = datetime.utcnow()
            lf.youtube_url = yt_url

            run.status       = "completed"
            run.note         = f"longform: {lf.title[:80]}"
            run.completed_at = datetime.utcnow()
            db.session.commit()
            print(f"[longform] Posted: {lf.title}")

        except Exception as e:
            import traceback
            run.status = "failed"; run.note = str(e)[:500]
            db.session.commit()
            print(f"[longform] ERROR {niche}: {e}")
            traceback.print_exc()
