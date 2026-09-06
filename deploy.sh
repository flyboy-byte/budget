#!/usr/bin/env bash
# Deploy latest code to VPS and restart the budget service.
# Run from local machine: ./deploy.sh
set -euo pipefail

# VPS host lives in .env (VPS_HOST=user@host), which is gitignored and never committed.
VPS=$(grep -E '^VPS_HOST=' .env | cut -d= -f2-)
[ -n "$VPS" ] || { echo "VPS_HOST not set in .env"; exit 1; }

echo "=== Pre-deploy checks ==="

# 1. Warn on uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "WARNING: uncommitted local changes (not deployed — only what's pushed to GitHub)."
fi

# 2. Tests must pass before deploying
.venv/bin/python -m pytest tests/ -q || { echo "Tests failed — aborting deploy."; exit 1; }

# 3. Push to GitHub
echo "--- git push ---"
git push origin master

echo ""
echo "=== Deploying to $VPS ==="

ssh "$VPS" bash <<'REMOTE'
set -euo pipefail
cd ~/budget

echo "--- git pull ---"
git pull

echo "--- venv sync ---"
.venv/bin/pip install -q -r requirements.txt

echo "--- syntax check ---"
.venv/bin/python -m compileall -q app/ scripts/

echo "--- migrations ---"
.venv/bin/python -m migrations.runner

echo "--- restarting service ---"
systemctl --user restart budget.service

echo "--- status ---"
systemctl --user status budget.service --no-pager -l | head -6
REMOTE

echo ""
echo "Deploy complete."
