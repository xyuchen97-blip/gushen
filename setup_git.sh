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
    git remote add origin https://github.com/xyuchen97-blip/gushen
    echo "✅ Remote added: https://github.com/xyuchen97-blip/gushen"
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
git commit -m "v10.2: revert v10.3 cross-market/OpEx signals (regression)

Reverted v10.3 additions after backtest showed US Sharpe regression:
- Cross-market momentum (HK→US): statistically significant (p=0.0001)
  but caused US Sharpe 2.689→2.304 (-0.385). Momentum signal conflicts
  with contrarian engine — pushes marginal signals over BUY threshold.
- OpEx Friday gate: insufficient sample size (n=62, p=0.23).

Research preserved in research_hypotheses.py for future reference.
Engine restored to v10.2 baseline (analyst signals + adaptive exits)."

echo ""
echo "✅ Committed. Now pushing..."

# Push
git branch -M main
git push -u origin main

echo ""
echo "🎉 Pushed to https://github.com/xyuchen97-blip/gushen"
