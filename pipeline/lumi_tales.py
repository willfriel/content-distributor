"""
Lumi Tales story generation pipeline.
Uses Claude to write complete storybook scripts with scenes, narration,
and visual descriptions — ready for Higgsfield video generation.
"""

import os
import random
from datetime import datetime

# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

CHARACTERS = [
    {
        "name":        "Benny",
        "animal":      "bear",
        "trait":       "chubby, curious little bear who loves exploring",
        "color":       "warm honey brown with a cream belly",
        "personality": "always asks 'why' and gets distracted by shiny things",
    },
    {
        "name":        "Pip",
        "animal":      "bunny",
        "trait":       "tiny bunny who is scared of everything but tries anyway",
        "color":       "soft white with a pink nose and floppy ears",
        "personality": "whispers a lot, holds their ears when nervous, always surprises themselves",
    },
    {
        "name":        "Rosie",
        "animal":      "fox",
        "trait":       "clever little fox who solves problems with kindness",
        "color":       "warm orange with a white fluffy tail tip",
        "personality": "always has an idea, loves making lists, gives the best hugs",
    },
    {
        "name":        "Slow Mo",
        "animal":      "turtle",
        "trait":       "turtle who always arrives last but never gives up",
        "color":       "soft green shell with a yellow-green face",
        "personality": "very calm, never rushes, secretly sees things everyone else misses",
    },
    {
        "name":        "Luna",
        "animal":      "butterfly",
        "trait":       "glowing butterfly who appears when someone feels lonely",
        "color":       "iridescent wings with soft purple and gold shimmer",
        "personality": "speaks in gentle rhymes, leaves a trail of tiny sparkles",
    },
    {
        "name":        "Finn",
        "animal":      "frog",
        "trait":       "frog who jumps into things without thinking and learns from it",
        "color":       "bright cheerful green with big round golden eyes",
        "personality": "super energetic, says 'let's do it!' before knowing the plan",
    },
    {
        "name":        "Ollie",
        "animal":      "owl",
        "trait":       "wise little owl who gives advice but is secretly afraid of the dark",
        "color":       "fluffy grey and white with big round glasses",
        "personality": "speaks wisely but flinches at sudden noises, secretly needs a nightlight",
    },
]

# ---------------------------------------------------------------------------
# Visual styles
# ---------------------------------------------------------------------------

STYLES = {
    "A": {
        "name":        "Soft Watercolor Storybook",
        "description": "soft watercolor illustration, pastel colors, warm golden hour lighting, "
                       "hand-painted texture, cozy bedtime picture book aesthetic, gentle brush strokes",
        "voice":       "Nicole",  # ElevenLabs — soft, intimate
        "music":       "gentle piano lullaby",
    },
    "B": {
        "name":        "Bright Flat Cartoon",
        "description": "bright flat cartoon illustration, bold clean outlines, vivid saturated colors, "
                       "simple shapes, modern kids animation aesthetic, Bluey-inspired warmth",
        "voice":       "Gigi",   # ElevenLabs — bright, friendly
        "music":       "upbeat ukulele",
    },
    "C": {
        "name":        "Magical Night Sky",
        "description": "magical night sky illustration, deep indigo and purple background, "
                       "glowing stars and moonlight, dreamlike sparkles, soft ethereal glow, "
                       "enchanted forest or cloud world setting",
        "voice":       "Thomas",  # ElevenLabs — calm, meditative
        "music":       "soft dreamy synth",
    },
}

INTRO_CARD   = "✨ Lumi Tales ✨"
OUTRO_LINE   = "Sweet dreams from Lumi Tales ✨"
TARGET_DURATION = 50  # seconds total
SCENES_COUNT    = 8


# ---------------------------------------------------------------------------
# Story generation
# ---------------------------------------------------------------------------

def generate_story(character: dict = None, style_key: str = None) -> dict | None:
    """
    Generate a complete Lumi Tales story using Claude.
    Returns a story dict or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[lumi] ANTHROPIC_API_KEY not set")
        return None

    character = character or random.choice(CHARACTERS)
    style_key = style_key or random.choice(list(STYLES.keys()))
    style     = STYLES[style_key]

    prompt = f"""You write scripts for Lumi Tales — a gentle kids storybook channel for children aged 2-6.
Each story is ~50 seconds long ({SCENES_COUNT} scenes, ~6 seconds each).

TODAY'S CHARACTER:
- Name: {character['name']}
- Animal: {character['animal']}
- Looks: {character['color']}
- Personality: {character['personality']}
- Core trait: {character['trait']}

VISUAL STYLE: {style['name']}
Style description: {style['description']}

RULES:
- Simple words only (a 3-year-old must understand)
- Warm, cozy, reassuring tone — never scary
- Always a happy ending
- One clear problem, one kind solution
- No cliffhangers
- Repetition is good (kids love it)
- The character should be round, soft, and extremely friendly-looking
- Every scene needs a visual description specific enough for an AI image generator

Write the story in this EXACT JSON format (no other text):
{{
  "title": "short catchy title",
  "moral": "one simple lesson in one sentence",
  "scenes": [
    {{
      "scene": 1,
      "narration": "exactly what the narrator says out loud (max 20 words)",
      "visual": "detailed image generation prompt for this scene — include character appearance, setting, {style['description'][:60]}, friendly and cute"
    }}
  ],
  "outro": "{OUTRO_LINE}"
}}

Write all {SCENES_COUNT} scenes. Make it magical."""

    try:
        import anthropic
        import json

        client = anthropic.Anthropic(api_key=api_key)
        msg    = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 1500,
            messages   = [{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        story_data = json.loads(text)

        return {
            "character":    character["name"],
            "animal":       character["animal"],
            "style":        style_key,
            "style_name":   style["name"],
            "voice":        style["voice"],
            "music":        style["music"],
            "title":        story_data["title"],
            "moral":        story_data.get("moral", ""),
            "script":       story_data["scenes"],
            "outro":        story_data.get("outro", OUTRO_LINE),
            "intro_card":   INTRO_CARD,
            "visual_style": style["description"],
        }

    except Exception as e:
        print(f"[lumi] Story generation failed: {e}")
        return None


def generate_and_store(app) -> int | None:
    """Generate a new story and store it in the DB. Returns story ID."""
    story = generate_story()
    if not story:
        return None

    with app.app_context():
        from models import db, LumiStory

        record = LumiStory(
            character = story["character"],
            style     = story["style"],
            title     = story["title"],
            moral     = story["moral"],
            script    = story,   # full dict including scenes, voice, visual_style
            status    = "ready",
        )
        db.session.add(record)
        db.session.commit()

        print(f"[lumi] Generated: '{story['title']}' — {story['character']} ({story['style_name']})")
        return record.id


# ---------------------------------------------------------------------------
# Niche video duration limits (seconds) — None = no limit
# ---------------------------------------------------------------------------

NICHE_MAX_DURATION = {
    "trading":    60,
    "fitness":    30,
    "crime":      90,
    "sports":     30,
    "anatomy":    60,
    "everything": None,
    "kids_short": 45,
    "kids_full":  300,   # 5 min YouTube video
}


# ---------------------------------------------------------------------------
# Teaser + full format helpers
# ---------------------------------------------------------------------------

def build_teaser(story: dict) -> dict:
    """
    Extract scenes 1-2 as a Short teaser.
    Ends right as the problem is introduced — kids want to see what happens next.
    """
    scenes        = story.get("script", {}).get("scenes", story.get("script", []))
    teaser_scenes = scenes[:2] if isinstance(scenes, list) else []
    narration     = " ".join(s.get("narration", "") for s in teaser_scenes)
    character     = story.get("character", "a little friend")

    return {
        **story,
        "format":    "short",
        "scenes":    teaser_scenes,
        "narration": narration,
        "caption":   (
            f"Does {character} find a way? 🌟 Watch the full story on Lumi Tales ✨\n\n"
            f"#LumiTales #KidsStories #Shorts #Storytime #AnimatedStories"
        ),
        "max_duration": NICHE_MAX_DURATION["kids_short"],
    }


def build_full_video(story: dict) -> dict:
    """
    Build the full YouTube video script from all 8 scenes.
    Includes intro card, full narration, and outro.
    """
    scenes     = story.get("script", {}).get("scenes", story.get("script", []))
    if not isinstance(scenes, list):
        scenes = []
    narration  = " ".join(s.get("narration", "") for s in scenes)
    character  = story.get("character", "")
    title      = story.get("title", "A Lumi Tales Story")
    moral      = story.get("moral", "")

    description = (
        f"✨ {title} ✨\n\n"
        f"Join {character} in today's Lumi Tales adventure!\n\n"
        f"{moral}\n\n"
        f"🌙 New stories every day — subscribe so you never miss one!\n\n"
        f"#LumiTales #KidsStories #BedtimeStories #Storytime #AnimatedStories "
        f"#KidsYouTube #ChildrensBooks #Cartoon"
    )

    return {
        **story,
        "format":       "full",
        "scenes":       scenes,
        "narration":    narration,
        "caption":      description,
        "max_duration": NICHE_MAX_DURATION["kids_full"],
        "made_for_kids": True,
    }


def get_next_story(app) -> dict | None:
    """Get the oldest ready story that hasn't been posted yet."""
    with app.app_context():
        from models import LumiStory
        story = LumiStory.query.filter_by(status="ready").order_by(LumiStory.generated_at).first()
        return story.to_dict() if story else None


def mark_posted(story_id: int, content_id: int, app):
    with app.app_context():
        from models import db, LumiStory
        story = LumiStory.query.get(story_id)
        if story:
            story.status     = "posted"
            story.content_id = content_id
            story.posted_at  = datetime.utcnow()
            db.session.commit()
