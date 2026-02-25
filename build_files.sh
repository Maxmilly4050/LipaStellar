#!/usr/bin/env bash
# build_files.sh — run by Vercel at deploy time

set -e

echo "==> Installing Python dependencies..."
pip install -r requirements.txt --break-system-packages

echo "==> Collecting static files..."
python manage.py collectstatic --noinput

echo "==> Applying database migrations..."
python manage.py migrate --noinput

echo "==> Build complete."
