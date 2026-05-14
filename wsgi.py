"""Gunicorn entry point — handles all startup logic cleanly."""
from server import app, _migrate, _seed_links
from models import db
from pipeline.scheduler import init_scheduler

with app.app_context():
    db.create_all()
    _migrate()
    _seed_links()

init_scheduler(app)
