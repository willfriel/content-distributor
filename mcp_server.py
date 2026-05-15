"""
MCP server for the Content Distributor pipeline.
Exposes pipeline controls to Claude Desktop so it can:
- Update style guides from browsed content
- Add reference accounts
- Trigger pipeline runs
- Log observed reels/trends
Run locally: python mcp_server.py
"""

import json
import os
import sys
import requests
from datetime import datetime

BASE_URL = os.environ.get("CONTENT_DISTRIBUTOR_URL", "https://content-distributor.onrender.com")


def _api(method: str, path: str, data: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        if method == "GET":
            r = requests.get(url, timeout=30)
        else:
            r = requests.post(url, json=data or {}, timeout=30)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------

def _respond(result):
    msg = {"jsonrpc": "2.0", "id": 1, "result": result}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _error(msg):
    err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": msg}}
    sys.stdout.write(json.dumps(err) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "log_reel",
        "description": (
            "Log an observed Instagram Reel or YouTube Short. Call this for every video "
            "you analyze while scrolling. Stores hook style, format, engagement signals, "
            "and niche so the style learning system can use it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform":    {"type": "string", "enum": ["instagram", "youtube"], "description": "Where you saw it"},
                "account":     {"type": "string", "description": "Account/channel handle"},
                "niche":       {"type": "string", "description": "Which niche this fits: trading, fitness, crime, sports, gaming, everything, kids"},
                "hook":        {"type": "string", "description": "The first line or opening 3 seconds — what grabbed attention"},
                "format":      {"type": "string", "description": "e.g. talking head, clips montage, text overlay, POV, reaction"},
                "caption":     {"type": "string", "description": "The caption text if visible"},
                "hashtags":    {"type": "array", "items": {"type": "string"}, "description": "Hashtags used"},
                "engagement":  {"type": "string", "description": "Rough engagement signal: low / medium / high / viral"},
                "notes":       {"type": "string", "description": "Anything else notable about why this worked or didn't"},
            },
            "required": ["platform", "niche", "hook", "format", "engagement"],
        },
    },
    {
        "name": "update_style_guide",
        "description": (
            "After analyzing a batch of reels, call this to push updated style recommendations "
            "into the pipeline for a specific niche. This directly affects how captions and "
            "content are generated going forward."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "niche": {"type": "string", "description": "Niche to update"},
                "recommendations": {
                    "type": "object",
                    "description": "Style recommendations derived from observed content",
                    "properties": {
                        "best_hooks":       {"type": "array", "items": {"type": "string"}, "description": "Top performing hook styles observed"},
                        "best_formats":     {"type": "array", "items": {"type": "string"}, "description": "Video formats that performed best"},
                        "caption_length":   {"type": "string", "enum": ["short", "medium", "long"]},
                        "emoji_target":     {"type": "integer", "description": "How many emojis top posts used"},
                        "best_hashtags":    {"type": "array", "items": {"type": "string"}},
                        "avoid":            {"type": "array", "items": {"type": "string"}, "description": "What low performers had in common"},
                        "example_hooks":    {"type": "array", "items": {"type": "string"}, "description": "Verbatim hook examples to emulate"},
                    },
                },
            },
            "required": ["niche", "recommendations"],
        },
    },
    {
        "name": "add_reference_account",
        "description": "Add an Instagram or YouTube account to the scraper reference list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle":     {"type": "string", "description": "Account handle without @"},
                "platform":   {"type": "string", "enum": ["instagram", "youtube"]},
                "niche_hint": {"type": "string", "description": "Which niche this account belongs to"},
            },
            "required": ["handle", "platform"],
        },
    },
    {
        "name": "trigger_pipeline",
        "description": "Trigger an immediate pipeline run for a niche to post content right now.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "niche": {"type": "string", "description": "Niche to trigger: trading, fitness, crime, sports, gaming, everything, kids"},
            },
            "required": ["niche"],
        },
    },
    {
        "name": "get_pipeline_status",
        "description": "Get the current status of all pipeline jobs and recent runs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_style_guides",
        "description": "Get the current style guides for all niches.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# In-memory reel log (persists for the session)
_reel_log: list[dict] = []


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_log_reel(args: dict) -> dict:
    entry = {**args, "logged_at": datetime.utcnow().isoformat()}
    _reel_log.append(entry)
    return {
        "logged": True,
        "total_logged_this_session": len(_reel_log),
        "message": f"Logged {args.get('engagement', '?')} engagement {args.get('format', '?')} from {args.get('platform')} — niche: {args.get('niche')}",
    }


def handle_update_style_guide(args: dict) -> dict:
    niche = args["niche"]
    recs  = args["recommendations"]

    # Add session reel observations to the recommendations
    niche_reels = [r for r in _reel_log if r.get("niche") == niche]
    if niche_reels:
        viral = [r for r in niche_reels if r.get("engagement") in ("high", "viral")]
        if viral and "example_hooks" not in recs:
            recs["example_hooks"] = [r["hook"] for r in viral[:5]]

    r = _api("POST", f"/api/style-guides/{niche}", {
        "niche":           niche,
        "recommendations": recs,
        "source":          "mcp_scroll_session",
        "reels_analyzed":  len(niche_reels),
    })
    return r


def handle_add_reference_account(args: dict) -> dict:
    return _api("POST", "/api/scraper/accounts", {
        "handle":     args["handle"].lstrip("@"),
        "platform":   args.get("platform", "instagram"),
        "niche_hint": args.get("niche_hint"),
    })


def handle_trigger_pipeline(args: dict) -> dict:
    return _api("POST", f"/api/pipeline/run-now/{args['niche']}")


def handle_get_pipeline_status(_) -> dict:
    return _api("GET", "/api/pipeline/status")


def handle_get_style_guides(_) -> dict:
    return _api("GET", "/api/scraper/style-guides")


HANDLERS = {
    "log_reel":               handle_log_reel,
    "update_style_guide":     handle_update_style_guide,
    "add_reference_account":  handle_add_reference_account,
    "trigger_pipeline":       handle_trigger_pipeline,
    "get_pipeline_status":    handle_get_pipeline_status,
    "get_style_guides":       handle_get_style_guides,
}


# ---------------------------------------------------------------------------
# MCP main loop
# ---------------------------------------------------------------------------

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")

        if method == "initialize":
            _respond({
                "protocolVersion": "2024-11-05",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "content-distributor", "version": "1.0.0"},
            })

        elif method == "tools/list":
            _respond({"tools": TOOLS})

        elif method == "tools/call":
            tool_name = msg.get("params", {}).get("name")
            tool_args  = msg.get("params", {}).get("arguments", {})
            handler    = HANDLERS.get(tool_name)
            if not handler:
                _error(f"Unknown tool: {tool_name}")
                continue
            try:
                result = handler(tool_args)
                _respond({
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                })
            except Exception as e:
                _error(str(e))

        elif method == "notifications/initialized":
            pass  # no response needed

        else:
            _error(f"Unknown method: {method}")


if __name__ == "__main__":
    main()
