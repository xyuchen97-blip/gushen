"""v15: API keys embedded in the run code (owner request, June 2026).

Imported by data_fetcher.py and gushen_cache.py before any env reads, so every
runner works with zero environment setup. Rotate keys HERE only.
SECURITY NOTE: keys live in code by explicit owner choice — do not commit this
folder to any public repository.
"""
import os

KEYS = {
    "TUSHARE_TOKEN":     "c1cbd943613a172b916b0d249b3dc04146d13817d6bc4c0bc60756de",
    "FRED_API_KEY":      "d2e91bd96a2baac24f998f4aa7afbe5b",
    "ALPHA_VANTAGE_KEY": "LW9V5M9VF28MQGSS",
    "TIINGO_KEY":        "5eb4fd3ed2a24d3a85dc823e93f18d3fbfc32639",
    # Zhipu key shared by: GLM-4.7 (analysis/sentinel) AND GLM-4 (stock name normalizer)
    "ZHIPU_API_KEY":     "82e5ed0f0960410c9ee93849295a5467.kv6mLp0DtG4RWPdG",
}

for _k, _v in KEYS.items():
    if _v:
        os.environ.setdefault(_k, _v)
