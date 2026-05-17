"""
Caption generation via Claude API with template fallback.
Always returns two variants (A and B) for A/B testing.
"""

import os
import random

# ---------------------------------------------------------------------------
# Niche caption templates (fallback when no API key)
# ---------------------------------------------------------------------------

TEMPLATES = {
    "trading": [
        ["The market just did THIS 👀 would you have caught it?", "Most traders miss this setup every single day 📈"],
        ["POV: you finally understand how the market works 🧠", "This is why 90% of traders lose money 💸"],
        ["Would you have taken this trade? 👇", "The chart doesn't lie — here's what happened next 📊"],
    ],
    "fitness": [
        ["Stop doing this at the gym ❌ do THIS instead ✅", "The fitness advice nobody tells you 🏋️"],
        ["Why you're not seeing results yet 😤", "One change that doubled my gains 💪"],
        ["The workout secret trainers don't want you to know 👀", "Do this every morning and watch what happens 🌅"],
        ["Fitness isn't just body — it's mind too 🧠💪", "The mental side of getting in shape nobody talks about"],
        ["Your habits are building you or breaking you 🔨", "Discipline is just self-respect in disguise 👊"],
        ["The reason you keep quitting has nothing to do with willpower 🧠", "One mindset shift that changes everything 🌅"],
    ],
    "crime": [
        ["This case was never solved… until now 🔍", "The detail that broke this case wide open 😨"],
        ["Nobody believed her story… but she was right 👁", "The clue everyone missed for 20 years 🕵️"],
        ["He had the perfect alibi. He was lying. 😳", "This cold case just got solved and it's wild 🚨"],
    ],
    "sports": [
        ["Nobody expected this to happen 🤯", "The moment that changed the game forever 🏆"],
        ["Athletes don't talk about this side of the sport 😤", "This play made the whole stadium go silent 🤫"],
        ["The underdog story nobody saw coming 👊", "One play. Season over. 💔"],
    ],
    "gaming": [
        ["Nobody believed he could do this 🎮😤", "The clip that broke the internet overnight 💀"],
        ["POV: you finally hit that rank you've been grinding for 🏆", "This play should not have worked. It worked. 🤯"],
        ["Bro really did that on stream 💀", "The moment every gamer dreams about 🎮✨"],
    ],
    "everything": [
        ["You won't believe this actually happened 😭", "The internet needed to see this today 💀"],
        ["Bro said what 💀", "This has no right being this satisfying 😌"],
        ["POV: it hits different at 2am 🌙", "Main character behavior 🎬"],
    ],
    "kids": [
        ["The bedtime story that put every kid to sleep 😴✨", "Kids everywhere are obsessed with this 🌟"],
        ["Story time! 🎉 Can you guess what happens next?", "The most magical story you'll hear today 🧚"],
        ["Even adults love this one 😂❤️", "Imagination level: unlimited 🚀"],
    ],
    "twitch": [
        ["Bro really did this on stream 💀🔴", "The chat had NO idea what was about to happen 👀"],
        ["This clip broke the internet for a reason 😭🔴", "Nobody does it like him 💀 stream clip of the day"],
        ["Streamer goes viral for this 🤯 #twitch", "The moment that made everyone clip it 🔴💀"],
        ["POV: you're watching live when this happens 😭", "This is why Twitch is undefeated 🔴🏆"],
        ["Chat was NOT ready 💀💀💀", "The clip everyone's talking about today 🔴👀"],
        ["You can't make this stuff up 😭🔴", "Streamer of the year behavior 🏆💀"],
    ],
}

HASHTAGS = {
    "trading":  "#trading #crypto #stocks #investing #finance #stockmarket #daytrading #forex #wealth #money #fyp #foryou",
    "fitness":  "#fitness #gym #workout #health #gains #motivation #mindset #discipline #selfimprovement #grindset #foryou #fyp",
    "crime":    "#truecrime #crime #mystery #unsolved #coldcase #criminal #thriller #darkweb #detective #fyp",
    "sports":   "#sports #nba #soccer #football #athlete #highlights #champion #viral #fyp #foryoupage",
    "gaming":   "#gaming #gamer #twitch #fyp #foryou #viral #gameplay #esports #gamingmoments #streamer",
    "everything": "#viral #trending #funny #relatable #foryou #fyp #memes #entertainment #satisfying",
    "kids":     "#kids #storytime #children #cartoon #family #education #fun #imagination #fyp",
    "twitch":   "#twitch #twitchclips #streamer #viral #funny #clip #fyp #foryou #livestream #gaming",
}


def _template_captions(niche: str, title: str) -> tuple[str, str]:
    options = TEMPLATES.get(niche, TEMPLATES["everything"])
    pair    = random.choice(options)
    tags    = HASHTAGS.get(niche, HASHTAGS["everything"])
    return f"{pair[0]}\n\n{tags}", f"{pair[1]}\n\n{tags}"


# ---------------------------------------------------------------------------
# Claude API captions
# ---------------------------------------------------------------------------

def _claude_captions(niche: str, title: str, api_key: str) -> tuple[str, str]:
    import anthropic
    from pipeline.style_learner import get_style_guide
    from pipeline.persona import SYSTEM_PROMPT

    tags  = HASHTAGS.get(niche, "")
    guide = get_style_guide(niche)
    recs  = guide.get("recommendations", {}) if guide else {}

    style_context = ""
    if recs:
        hooks    = recs.get("example_hooks", [])
        length   = recs.get("caption_length", "short")
        emojis   = recs.get("emoji_target", 1)
        hashtags = recs.get("best_hashtags", [])
        if hashtags:
            tags = " ".join(hashtags)  # use learned hashtags over defaults
        style_context = f"""
Style guidelines learned from top-performing accounts in this niche:
- Caption length: {length}
- Average emojis per caption: {emojis}
- Winning hook examples: {"; ".join(hooks[:3]) if hooks else "n/a"}
- Use these hashtags: {tags}
"""

    prompt = f"""Niche: {niche}
Video title/topic: {title}
{style_context}
Write exactly 2 different caption variants for this video. Each should:
- Open with a hook that stops the scroll in the first 3 seconds (curiosity gap, emotional trigger, or pattern interrupt)
- Be 1-2 punchy lines max (no more than 150 chars before hashtags)
- Never describe what's in the video — tease, provoke, or create FOMO instead
- Feel native to Reels/Shorts (casual, direct, 1-2 emojis max)
- NOT start with "I" or "We"
- End with these hashtags on a new line: {tags}

Reply in this exact format (nothing else):
VARIANT_A: [caption here]
VARIANT_B: [caption here]"""

    client = anthropic.Anthropic(api_key=api_key)
    msg    = client.messages.create(
        model      = "claude-haiku-4-5-20251001",
        max_tokens = 300,
        system     = SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()

    variant_a, variant_b = "", ""
    for line in text.splitlines():
        if line.startswith("VARIANT_A:"):
            variant_a = line.replace("VARIANT_A:", "").strip()
        elif line.startswith("VARIANT_B:"):
            variant_b = line.replace("VARIANT_B:", "").strip()

    # Append hashtags if Claude didn't include them
    if tags and tags not in variant_a:
        variant_a += f"\n\n{tags}"
    if tags and tags not in variant_b:
        variant_b += f"\n\n{tags}"

    return variant_a or _template_captions(niche, title)[0], \
           variant_b or _template_captions(niche, title)[1]


# ---------------------------------------------------------------------------
# Long-form CTA helpers
# ---------------------------------------------------------------------------

def _get_longform_cta(niche: str) -> str:
    """Return a CTA line pointing to the latest long-form video for this niche, or ''."""
    try:
        from models import LongFormVideo
        lf = LongFormVideo.query.filter_by(niche=niche, status="posted")\
                                .order_by(LongFormVideo.posted_at.desc()).first()
        if lf and lf.youtube_url:
            return f"\n\nWatch the full video 👉 {lf.youtube_url}"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_captions(niche: str, title: str, add_longform_cta: bool = False) -> tuple[str, str]:
    """
    Returns (caption_a, caption_b) — always two variants for A/B testing.
    Uses Claude API if ANTHROPIC_API_KEY is set, otherwise falls back to templates.
    Pass add_longform_cta=True for Shorts to append a link to the full long-form video.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            cap_a, cap_b = _claude_captions(niche, title, api_key)
        except Exception as e:
            print(f"[captions] Claude API failed, using template: {e}")
            cap_a, cap_b = _template_captions(niche, title)
    else:
        cap_a, cap_b = _template_captions(niche, title)

    if add_longform_cta:
        cta   = _get_longform_cta(niche)
        cap_a += cta
        cap_b += cta

    return cap_a, cap_b
