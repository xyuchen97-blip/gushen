"""GLM client (Zhipu) — the v15 LLM layer calls GLM-4.7, NOT Claude.

Uses ZHIPU_API_KEY from strategy/gushen_keys.py (same key as GLM-4 per owner).
Model selectable via GLM_MODEL env; defaults to glm-4.7.
"""
import os, json, requests

API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def glm_chat(messages, model=None, temperature=0.2, max_tokens=1500, timeout=60, retries=4,
             thinking=True):
    """messages = [{'role':'user','content':...}, ...] → assistant text (or raises)."""
    import time
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        raise RuntimeError("ZHIPU_API_KEY empty — fill it in strategy/gushen_keys.py")
    last = None
    for attempt in range(retries):
        body = {"model": model or os.environ.get("GLM_MODEL", "glm-4.7"),
                "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens}
        if not thinking:
            body["thinking"] = {"type": "disabled"}   # hybrid-reasoning models: fast path
        r = requests.post(API_URL, timeout=timeout,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body)
        if r.status_code == 429:        # rate limit (key shared with WorkBuddy normalizer)
            last = r
            time.sleep(3 * (attempt + 1))
            continue
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("empty content (reasoning consumed budget? pass thinking=False)")
        return content
    last.raise_for_status()


def normalize_ticker(user_input):
    """Stock name → (ticker, market) via GLM-4 (NOT 4.7 — owner's normalizer
    convention uses the GLM-4 model on the same key). E.g. '茅台' → 600519.SH/A.
    """
    prompt = (
        f"将用户输入的股票名称规范化。输入: '{user_input}'。"
        '返回严格JSON: {"ticker": "<代码>", "market": "A|HK|US"}。'
        "规则: A股格式如600519.SH/000858.SZ; 港股格式如0700.HK(4位数字); 美股直接用代码如NVDA。"
        "不确定时返回 {\"ticker\": \"UNKNOWN\", \"market\": \"\"}。")
    # Owner-specified chain (June 2026): glm-4.7-flash primary → glm-4.5-air fallback.
    # Note: glm-4.5-air is a REASONING model — needs max_tokens headroom (~400) or the
    # reasoning consumes the budget and content returns empty. Dead/failed models are
    # memoized per process so repeated calls skip straight to the working tier.
    global _DEAD_MODELS
    for m in ("glm-4.7-flash", "glm-4.5-air"):
        if m in _DEAD_MODELS:
            continue
        try:
            j = glm_json(prompt, model=m, temperature=0.0, max_tokens=200,
                         retries=2, timeout=40, thinking=False)
            return j.get("ticker", "UNKNOWN"), j.get("market", "")
        except requests.HTTPError:
            _DEAD_MODELS.add(m)   # quota/invalid model — permanent for this process
            continue
        except Exception:
            continue              # timeout/parse — transient, do NOT kill the model
    return "UNKNOWN", ""


_DEAD_MODELS = set()


def glm_json(prompt, system=None, **kw):
    """Ask for strict JSON; parse with fence stripping."""
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    txt = glm_chat(msgs, **kw).strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
    return json.loads(txt)
