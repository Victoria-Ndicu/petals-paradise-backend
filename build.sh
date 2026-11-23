#!/usr/bin/env bash
# Render build step: install deps, collect static files, apply migrations.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
if [ -n "${MIGRATION_DATABASE_URL:-}" ]; then
  export DATABASE_URL="$MIGRATION_DATABASE_URL"
fi
python manage.py migrate --no-input
python manage.py createcachetable
