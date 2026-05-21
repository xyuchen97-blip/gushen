#!/bin/bash
# Gushen v10.2 — Git init and push to GitHub
# Run: cd ~/Desktop/gushen_handoff && bash setup_git.sh

set -e

cd ~/Desktop/gushen_handoff

# Clean up build artifacts
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Remove legacy files (now excluded by .gitignore, but clean them up)
rm -rf strategy/archive/ 2>/dev/null || true
rm -f data/run_us48.py data/run_us_backtest.py 2>/dev/null || true
rm -f data/*.log 2>/dev/null || true

# Init git repo
if [ ! -d .git ]; then
    git init
    echo "✅ Git initialized"
else
    echo "✅ Git repo already exists"
fi

# Add remote (if not already set)
if ! git remote get-url origin &>/dev/null; then
    git remote add origin https://github.com/xyuchen97/gushen.git
    echo "✅ Remote added: https://github.com/xyuchen97/gushen.git"
else
    echo "✅ Remote already set: $(git remote get-url origin)"
fi

# Stage all files
git add -A

# Show what we're committing
echo ""
echo "📊 Staged files:"
git diff --cached --stat | tail -10
echo ""

# Commit
git commit -m "v10.2: regime-adaptive scoring + analyst signals + adaptive exits

Engine:
- 5-stage scoring pipeline (score_bar_v5) with regime-adaptive dual-mode
- Stage 3.5: US earnings beat streak signals (Alpha Vantage EARNINGS)
- Adaptive exit: time decay + profit-take trailing + ATR stop (US/A only)
- Margin financing re-activated for A-stocks
- All legacy code removed; all API keys use env vars

Data:
- analyst_signals table in gushen.db (968 signals: 248 A + 720 US)
- A-stock Tushare forecast: cached but disabled (too coarse)
- HK akshare ET: cached for production (not backtestable)

Performance (21 stocks, 2021-2026):
- Overall S=1.476, US=2.767, HK=1.643, A=0.222"

echo ""
echo "✅ Committed. Now pushing..."

# Push
git branch -M main
git push -u origin main

echo ""
echo "🎉 Pushed to https://github.com/xyuchen97/gushen"
