"""WSGI entry point.

Run locally:
    flask run

Production (gunicorn):
    gunicorn wsgi:app
"""
from app import create_app

app = create_app()
