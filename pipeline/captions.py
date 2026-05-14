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
    "anatomy": [
        ["Your body is doing THIS right now and you don't even know 🤯", "The organ doctors don't tell you enough about 🧬"],
        ["This is why you feel that way 🧠", "The human body fact that breaks people's brains 💥"],
        ["What happens inside your body when you do this 👀", "Scientists just discovered this about the human body 🔬"],
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
}

HASHTAGS = {
    "trading":  "#trading #crypto #stocks #investing #finance #stockmarket #daytrading #forex #wealth #money #fyp #foryou",
    "fitness":  "#fitness #gym #workout #health #gains #motivation #bodybuilding #nutrition #fitlife #foryou #fyp",
    "crime":    "#truecrime #crime #mystery #unsolved #coldcase #criminal #thriller #darkweb #detective #fyp",
    "sports":   "#sports #nba #soccer #football #athlete #highlights #champion #viral #fyp #foryoupage",
    "anatomy":  "#anatomy #biology #medicine #health #science #medstudent #humanbody #medicalfacts #fyp",
    "everything": "#viral #trending #funny #relatable #foryou #fyp #memes #entertainment #satisfying",
    "kids":     "#kids #storytime #children #cartoon #family #education #fun #imagination #fyp",
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

    prompt = f"""You create viral short-form video captions for social media (TikTok, Instagram Reels, YouTube Shorts).

Niche: {niche}
Video title/topic: {title}
{style_context}
Write exactly 2 different caption variants for this video. Each should:
- Be 1-2 punchy lines max (no more than 150 chars before hashtags)
- Create curiosity, emotion, or FOMO — never describe what's in the video
- Feel native to TikTok/Reels (casual, direct, sometimes use 1-2 emojis)
- NOT start with "I" or "We"
- End with these hashtags on a new line: {tags}

Reply in this exact format (nothing else):
VARIANT_A: [caption here]
VARIANT_B: [caption here]"""

    client = anthropic.Anthropic(api_key=api_key)
    msg    = client.messages.create(
        model      = "claude-haiku-4-5-20251001",  # fast + cheap for captions
        max_tokens = 300,
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
# Public entry point
# ---------------------------------------------------------------------------

def generate_captions(niche: str, title: str) -> tuple[str, str]:
    """
    Returns (caption_a, caption_b) — always two variants for A/B testing.
    Uses Claude API if ANTHROPIC_API_KEY is set, otherwise falls back to templates.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _claude_captions(niche, title, api_key)
        except Exception as e:
            print(f"[captions] Claude API failed, using template: {e}")
    return _template_captions(niche, title)
