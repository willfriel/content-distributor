# Content Distributor

Multi-account content distribution system for managing video uploads across YouTube, Instagram, and TikTok — organised by niche.

## Niches (default)
- Trading
- Fitness
- Crime / Horror / Mystery
- Sports
- Anatomy & Physiology

## Stack
- Flask + SQLAlchemy + PostgreSQL
- Fernet encryption for stored API credentials
- OpusClip for clipping long videos into shorts
- YouTube Data API v3, Instagram Graph API, TikTok Content Posting API
- Optional Telegram notifications

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in env vars
cp .env.example .env

# 3. Generate a Fernet key (run once, save to .env)
python -c "from config import Config; print(Config.generate_fernet_key())"

# 4. Init the database (creates tables + seeds default niches)
flask --app server init-db

# 5. Run locally
python server.py
```

## API Reference

### Content
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/content/submit` | Queue a video for processing |
| POST | `/api/content/process/{id}` | Trigger OpusClip + upload |
| GET | `/api/content/status/{id}` | Poll job status |

### Accounts
| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/accounts/connect` | Add a social account to a niche |
| GET | `/api/accounts/list` | List accounts (filter by ?niche= or ?platform=) |
| DELETE | `/api/accounts/{id}` | Remove an account |

### Niches
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/niches` | List active niches |
| POST | `/api/niches` | Add a new niche |

### Dashboard
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/dashboard/stats` | Upload counts by status, niche summary |

## Submit a video (example)

```bash
curl -X POST http://localhost:5000/api/content/submit \
  -H "Content-Type: application/json" \
  -d '{
    "niche": "trading",
    "video_url": "https://example.com/video.mp4",
    "title": "Top 5 Trade Setups This Week",
    "description": "Weekly breakdown of the best trade setups.",
    "platforms": ["youtube", "instagram"]
  }'
```

## Connect an account (example)

```bash
curl -X POST http://localhost:5000/api/accounts/connect \
  -H "Content-Type: application/json" \
  -d '{
    "niche": "trading",
    "platform": "youtube",
    "account_name": "Trading Channel",
    "account_id": "UCxxxxxxxxxxxxxxxx",
    "credentials": {
      "access_token": "...",
      "refresh_token": "...",
      "client_id": "...",
      "client_secret": "..."
    }
  }'
```

## Render Deployment

1. Create a new Web Service pointing to this repo
2. Set build command: `pip install -r requirements.txt`
3. Set start command: `gunicorn server:app`
4. Add a PostgreSQL addon
5. Copy all env vars from `.env.example` into the Render environment panel
6. Run `flask --app server init-db` via Render shell after first deploy

## Notes

- **TikTok API**: The Content Posting API requires approval from TikTok. Until approved, uploads will return `manual_required: true`.
- **Credential encryption**: All stored API credentials are encrypted with Fernet. If you rotate `FERNET_KEY`, existing credentials will be unreadable — re-connect all accounts.
- **OpusClip polling**: The current process endpoint polls synchronously. For production, move to Celery or a Render background worker to avoid HTTP timeouts on long videos.
