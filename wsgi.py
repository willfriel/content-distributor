"""Gunicorn entry point — handles all startup logic cleanly."""
from server import app, _migrate, _seed_links, _seed_niches, _seed_reference_accounts
from models import db
from pipeline.scheduler import init_scheduler

with app.app_context():
    db.create_all()
    _migrate()
    _seed_niches()
    _seed_links()
    _seed_reference_accounts()

init_scheduler(app)
