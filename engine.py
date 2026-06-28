"""engine.py — provider-agnostic AI + Azure DevOps engine (no UI dependency).
Ported from the original QA tool scripts so the Flet UI can drive it directly.
Configure provider keys in AI_CONFIG below, or pass them at runtime via set_credentials().
"""
import os, re, json, base64, html as _html, requests, time

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
AZURE_ORG = "worldofsystemsmyportal"

AI_PROVIDER = "anthropic"   # overridden at runtime by the UI

# Output language for generated titles/steps: "ar" (Arabic) or "en" (English).
# Overridden at runtime by the UI via set_output_lang().
OUTPUT_LANG = "ar"

def set_output_lang(lang):
    """Set the language for generated titles/steps. 'ar' or 'en'."""
    global OUTPUT_LANG
    OUTPUT_LANG = "en" if str(lang).lower().startswith("en") else "ar"
    return OUTPUT_LANG

AI_CONFIG = {
    "anthropic":    {"api_key": "your-anthropic-key-here", "model": "claude-sonnet-4-6", "vision": True},
    "openai":       {"api_key": "your-openai-key-here", "model": "gpt-4o", "vision": True},
    "gemini":       {"api_key": "your-gemini-key-here", "model": "gemini-1.5-pro", "vision": True},
    "azure_openai": {"api_key": "your-azure-openai-key-here", "endpoint": "https://YOUR-RESOURCE.openai.azure.com",
                     "deployment": "gpt-4o", "api_version": "2024-06-01", "vision": True},
    "ollama":       {"api_key": "", "base_url": "http://localhost:11434", "model": "llama3.1", "vision": False},
    "nvidia":       {"api_key": "nvapi-your-nvidia-key-here", "base_url": "https://integrate.api.nvidia.com/v1",
                     "model": "qwen/qwen3.5-397b-a17b", "vision": True},
    # DeepSeek — OpenAI-compatible API (base_url https://api.deepseek.com).
    # New accounts get a one-time free token grant, then cheap pay-as-you-go.
    # "deepseek-chat" is the current alias for V4-Flash (non-thinking). NOTE: the
    # deepseek-chat / deepseek-reasoner aliases are scheduled for deprecation on
    # 2026-07-24 — after that switch model to "deepseek-v4-flash" (fast/cheap) or
    # "deepseek-v4-pro" (stronger reasoning). deepseek-chat is text-only.
    "deepseek":     {"api_key": "your-deepseek-key-here", "base_url": "https://api.deepseek.com",
                     "model": "deepseek-chat", "vision": False},
    # Qwen (Alibaba DashScope / Model Studio) — OpenAI-compatible. Default base_url
    # is the INTERNATIONAL (Singapore) endpoint, correct for accounts outside
    # mainland China (e.g. Egypt). For a Beijing-region key use
    # https://dashscope.aliyuncs.com/compatible-mode/v1 ; US: dashscope-us...
    # "qwen-plus" is a solid text default; for image input switch to "qwen-vl-max"
    # and set vision: True. New Model Studio accounts get limited trial credits.
    "qwen":         {"api_key": "your-qwen-key-here",
                     "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                     "model": "qwen-plus", "vision": False},
    # Manus — an AGENT API (not chat-completions). It speaks the OpenAI *Responses*
    # API at https://api.manus.im, authenticated by an "API_KEY" HEADER (the
    # openai client's api_key arg is just a placeholder). Tasks run ASYNCHRONOUSLY
    # and are billed in credits, so each call creates+polls a task. "models" are
    # agent profiles: manus-1.6 (general), -lite (cheap/fast), -max (deep). We use
    # task_mode "chat" (lightest) by default. See engine's `manus` call branch.
    "manus":        {"api_key": "your-manus-key-here", "base_url": "https://api.manus.im",
                     "model": "manus-1.6", "task_mode": "chat", "vision": True},
}

FEATURE_DESCRIPTION = ""   # optional global feature context for step generation

# Email
GMAIL_SENDER   = "wsstestteam2@gmail.com"
GMAIL_APP_PASS = ""

# Runtime credentials (set by the UI)
AZURE_PAT = ""

def set_credentials(provider=None, api_key=None, pat=None, gmail=None,
                    org=None, gmail_sender=None, model=None):
    global AI_PROVIDER, AZURE_PAT, GMAIL_APP_PASS, AZURE_ORG, GMAIL_SENDER
    if provider:
        AI_PROVIDER = provider
    if api_key and AI_PROVIDER in AI_CONFIG:
        AI_CONFIG[AI_PROVIDER]["api_key"] = api_key
    if model and AI_PROVIDER in AI_CONFIG:
        # Azure routes by "deployment"; every other provider uses "model".
        key = "deployment" if AI_PROVIDER == "azure_openai" else "model"
        AI_CONFIG[AI_PROVIDER][key] = model.strip()
    if pat is not None:
        AZURE_PAT = pat
    if gmail is not None:
        GMAIL_APP_PASS = gmail
    if org:
        AZURE_ORG = org.strip()
    if gmail_sender:
        GMAIL_SENDER = gmail_sender.strip()

def current_model(provider=None):
    """Return the configured model id for a provider (or the active one)."""
    p = provider or AI_PROVIDER
    cfg = AI_CONFIG.get(p, {})
    return cfg.get("deployment") if p == "azure_openai" else cfg.get("model")


# ═══════════════════════════════════════════════════════════════════════════════
#  AI PROVIDER LAYER
# ═══════════════════════════════════════════════════════════════════════════════
class CreditBalanceError(Exception):
    pass

# ── Error classification ──────────────────────────────────────────────────────
# Categories returned by classify_ai_error(). The UI/log uses (category, message);
# the orchestrators use TRANSIENT_CATEGORIES to decide whether to retry.
TRANSIENT_CATEGORIES = {"rate_limit", "server", "overloaded", "timeout", "network"}

def _status_of(exc):
    """Best-effort HTTP status code from any provider SDK exception."""
    for attr in ("status_code", "status", "http_status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(m.group(1)) if m else None

def classify_ai_error(exc):
    """Map any provider exception to (category, friendly_message).

    Categories: auth, credit, rate_limit, bad_model, not_found, context_length,
    bad_request, content_filter, server, overloaded, network, timeout, unknown.
    Reads typed SDK exception names first, then HTTP status, then message text,
    so it works across the anthropic / openai / google SDKs.
    """
    prov = T_disp(AI_PROVIDER)
    raw = str(exc or "")
    low = raw.lower()
    etype = type(exc).__name__.lower()
    status = _status_of(exc)

    # 0) credit / quota first (a 429 can mean either rate-limit OR out-of-quota)
    if _is_credit_error(raw):
        return ("credit", f"{prov}: account is out of credit/quota. Top up with the "
                          f"provider, or switch the AI Provider in Setup and Resume.")

    # 0.5) expired / invalid key — some providers (e.g. Gemini) return this as a
    # 400, so catch it by message BEFORE the generic bad_request branch below.
    if (("api key expired" in low) or ("api_key_invalid" in low)
            or ("api key not valid" in low) or ("api key is invalid" in low)
            or ("expired" in low and "key" in low) or ("renew" in low and "key" in low)):
        return ("auth", f"{prov}: API key expired/invalid. Renew or re-check the key "
                        f"in Setup and Save it, then Resume — or switch provider.")

    # 1) typed-exception names from the SDKs (most reliable)
    if "authentication" in etype or "permissiondenied" in etype:
        return ("auth", f"{prov}: API key rejected. Re-check the key in Setup, Save it, then Resume.")
    if "ratelimit" in etype:
        return ("rate_limit", f"{prov}: rate limited (429). Waiting before retry…")
    if "notfound" in etype:
        return ("bad_model", f"{prov}: model not found. Pick a valid model for this provider in Setup.")
    if "badrequest" in etype or "unprocessable" in etype or "invalidargument" in etype:
        if "context length" in low or "maximum context" in low or "too long" in low:
            return ("context_length", f"{prov}: input too long for this model's context window.")
        if "model" in low and ("not" in low or "invalid" in low or "does not exist" in low):
            return ("bad_model", f"{prov}: invalid model. Pick a valid model for this provider in Setup.")
        return ("bad_request", f"{prov}: request rejected — {re.sub(r'\s+', ' ', raw).strip()[:160]}")
    if "timeout" in etype or "timed out" in low:
        return ("timeout", f"{prov}: request timed out. Retrying…")
    if ("apiconnection" in etype or "connectionerror" in etype or "getaddrinfo" in low
            or "name or service not known" in low or "ssl" in low
            or "max retries" in low or "failed to establish" in low):
        return ("network", f"{prov}: cannot reach the provider — check your network/firewall.")
    if "overloaded" in etype or "overloaded" in low or status == 529:
        return ("overloaded", f"{prov}: provider overloaded. Retrying…")
    if "internalserver" in etype or "serviceunavailable" in etype:
        return ("server", f"{prov}: provider error ({status or '5xx'}). Retrying…")
    if "contentfilter" in etype or "content_filter" in low or ("blocked" in low and "safety" in low):
        return ("content_filter", f"{prov}: the response was blocked by a safety filter.")

    # 2) fall back to HTTP status code
    if status == 401:
        return ("auth", f"{prov}: API key rejected (401). Re-check the key in Setup, Save it, then Resume.")
    if status == 403:
        return ("auth", f"{prov}: access denied (403). Check the key's permissions/region for this model.")
    if status == 404:
        return ("bad_model", f"{prov}: model/endpoint not found (404). Pick a valid model in Setup.")
    if status == 422:
        return ("bad_request", f"{prov}: request rejected (422) — check the model and parameters.")
    if status == 429:
        return ("rate_limit", f"{prov}: rate limited (429). Waiting before retry…")
    if status == 529:
        return ("overloaded", f"{prov}: provider overloaded (529). Retrying…")
    if status in (500, 502, 503, 504):
        return ("server", f"{prov}: provider error ({status}). Retrying…")

    # 3) message-text fallbacks
    if "invalid api key" in low or "incorrect api key" in low or "unauthorized" in low or "x-api-key" in low:
        return ("auth", f"{prov}: API key rejected. Re-check the key in Setup, Save it, then Resume.")
    if "model" in low and ("not found" in low or "does not exist" in low or "unknown model" in low):
        return ("bad_model", f"{prov}: model not found. Pick a valid model for this provider in Setup.")
    if "rate limit" in low or "429" in low:
        return ("rate_limit", f"{prov}: rate limited (429). Waiting before retry…")
    if "context length" in low or "maximum context" in low:
        return ("context_length", f"{prov}: input too long for this model's context window.")

    return ("unknown", (f"{prov}: {re.sub(r'\s+', ' ', raw).strip()[:180]}"
                        if raw else f"{prov}: unknown error."))

# Provider errors a user can fix by renewing a key, waiting, or switching provider
# — these PAUSE the run (so the user can act + Resume). Everything else (a bad
# JSON response, content filter, oversized context) falls back to raw steps.
# Errors worth PAUSING the run for so the user can switch provider / fix the key.
# Transient categories (rate_limit, server, overloaded, network, timeout) are
# deliberately EXCLUDED — ai_complete already retries those patiently, so pausing
# for them would just nag the user about something that clears on its own.
_RECOVERABLE_AI_CATS = {"auth", "credit", "bad_model", "not_found"}

def _is_recoverable_ai_error(exc):
    try:
        cat, _ = classify_ai_error(exc)
    except Exception:
        return False
    return cat in _RECOVERABLE_AI_CATS

def _ai_cfg():
    cfg = AI_CONFIG.get(AI_PROVIDER)
    if not cfg:
        raise RuntimeError(f"Unknown AI_PROVIDER '{AI_PROVIDER}'.")
    return cfg
def _is_credit_error(msg):
    m = msg.lower()
    return ("credit balance is too low" in m or "insufficient_quota" in m
            or ("quota" in m and "exceeded" in m) or ("billing" in m and "hard limit" in m))

def friendly_ai_error(msg):
    """Turn a raw provider error (often a long JSON 400/401) into one readable
    line for the activity log. Accepts an exception or a string."""
    try:
        exc = msg if isinstance(msg, BaseException) else Exception(str(msg or ""))
        _cat, friendly = classify_ai_error(exc)
        return friendly
    except Exception:
        return re.sub(r"\s+", " ", str(msg or "")).strip()[:200]

def T_disp(name):
    """Pretty provider name without importing the UI theme."""
    return {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Gemini",
            "azure_openai": "Azure OpenAI", "ollama": "Ollama", "nvidia": "NVIDIA",
            "deepseek": "DeepSeek", "qwen": "Qwen", "manus": "Manus"}.get(name, str(name).title())

def active_providers():
    """Provider names that have a usable key."""
    out = []
    for name, cfg in AI_CONFIG.items():
        k = (cfg.get("api_key") or "").strip()
        if k and not k.startswith("your-") and "-here" not in k:
            out.append(name)
    return out

class EmptyAIResponse(Exception):
    """Raised when a provider returns no usable text (empty choices, None content,
    content-filter block, or truncated/blocked output)."""
    pass

def _extract_openai_text(resp):
    """Defensively pull text from an OpenAI-compatible chat completion."""
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise EmptyAIResponse("provider returned no choices")
    ch = choices[0]
    fr = getattr(ch, "finish_reason", None)
    msg = getattr(ch, "message", None)
    text = getattr(msg, "content", None) if msg is not None else None
    if text is None:
        refusal = getattr(msg, "refusal", None) if msg is not None else None
        if refusal:
            raise EmptyAIResponse(f"model refused: {refusal}")
        if fr == "content_filter":
            raise EmptyAIResponse("response blocked by content filter")
        raise EmptyAIResponse("empty content from provider")
    if fr == "length" and not str(text).strip():
        raise EmptyAIResponse("response truncated (max_tokens) with no content")
    return text

def _ai_call_once(provider, cfg, prompt_text, images, max_tokens, timeout, want_json=False):
    """One provider call. Returns text or raises (EmptyAIResponse / SDK error).
    want_json=True asks the provider for strict JSON output where supported (used
    for the intent-compiler calls), so models like Gemini don't wrap the JSON in
    reasoning prose ('Wait, let's…') which then fails to parse."""
    if provider == "anthropic":
        import anthropic
        content = []
        for im in images:
            content.append({"type": "image", "source": {"type": "base64",
                "media_type": im["media_type"], "data": im["data"]}})
        content.append({"type": "text", "text": prompt_text})
        resp = anthropic.Anthropic(api_key=cfg["api_key"]).messages.create(
            model=cfg["model"], max_tokens=max_tokens, timeout=timeout,
            messages=[{"role": "user", "content": content}])
        blocks = getattr(resp, "content", None) or []
        texts = [getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text"]
        out = "".join(texts).strip()
        if not out:
            sr = getattr(resp, "stop_reason", None)
            raise EmptyAIResponse(f"empty response (stop_reason={sr})")
        return out

    if provider in ("openai", "nvidia", "deepseek", "qwen"):
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url")) if cfg.get("base_url") \
                 else OpenAI(api_key=cfg["api_key"])
        if images:
            content = [{"type": "text", "text": prompt_text}]
            for im in images:
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{im['media_type']};base64,{im['data']}"}})
        else:
            # text-only: plain string. Some providers reject the typed-array form
            # for non-vision models.
            content = prompt_text
        kwargs = {"model": cfg["model"], "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": content}]}
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = client.chat.completions.create(**kwargs)
        return _extract_openai_text(resp)

    if provider == "azure_openai":
        from openai import AzureOpenAI
        client = AzureOpenAI(api_key=cfg["api_key"], azure_endpoint=cfg["endpoint"],
                             api_version=cfg["api_version"])
        if images:
            content = [{"type": "text", "text": prompt_text}]
            for im in images:
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{im['media_type']};base64,{im['data']}"}})
        else:
            content = prompt_text
        kwargs = {"model": cfg["deployment"], "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": content}]}
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = client.chat.completions.create(**kwargs)
        return _extract_openai_text(resp)

    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=cfg["api_key"])
        model = genai.GenerativeModel(cfg["model"])
        parts = [prompt_text]
        for im in images:
            parts.append({"mime_type": im["media_type"], "data": base64.b64decode(im["data"])})
        gen_cfg = {"max_output_tokens": max_tokens}
        if want_json:
            # Force strict JSON so Gemini 2.x doesn't emit reasoning prose around
            # the JSON (the cause of "Cannot parse JSON … Wait, let's…" failures).
            gen_cfg["response_mime_type"] = "application/json"
        resp = model.generate_content(parts, generation_config=gen_cfg)
        # Gemini raises on .text if the candidate was blocked; surface that cleanly
        try:
            txt = resp.text
        except Exception:
            fb = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            cands = getattr(resp, "candidates", None) or []
            fr = getattr(cands[0], "finish_reason", None) if cands else None
            raise EmptyAIResponse(f"blocked by Gemini (block_reason={fb}, finish_reason={fr})")
        if not (txt or "").strip():
            raise EmptyAIResponse("empty response from Gemini")
        return txt

    if provider == "ollama":
        payload = {"model": cfg["model"],
                   "messages": [{"role": "user", "content": prompt_text}], "stream": False}
        r = requests.post(f"{cfg['base_url']}/api/chat", json=payload, timeout=timeout or 180)
        r.raise_for_status()
        data = r.json()
        txt = (data.get("message") or {}).get("content")
        if not (txt or "").strip():
            raise EmptyAIResponse("empty response from Ollama")
        return txt

    if provider == "manus":
        # Manus speaks the OpenAI *Responses* API and runs tasks ASYNCHRONOUSLY:
        # create → poll until status leaves "running" → read the assistant text.
        # Auth is via the API_KEY header (the api_key arg is just a placeholder).
        from openai import OpenAI
        import time as _t
        base = cfg.get("base_url") or "https://api.manus.im"
        client = OpenAI(base_url=base, api_key="placeholder",
                        default_headers={"API_KEY": cfg["api_key"]})
        content = [{"type": "input_text", "text": prompt_text}]
        for im in images:
            content.append({"type": "input_image",
                            "image_url": f"data:{im['media_type']};base64,{im['data']}"})
        resp = client.responses.create(
            model=cfg["model"],
            input=[{"role": "user", "content": content}],
            extra_body={"task_mode": cfg.get("task_mode") or "chat",
                        "agent_profile": cfg["model"]})
        rid = getattr(resp, "id", None)
        status = getattr(resp, "status", None)
        # poll (Manus tasks are slow); honor Stop via the interruptible sleep
        deadline = _t.time() + (timeout if timeout else 600)
        while status == "running" and _t.time() < deadline:
            _interruptible_sleep(5)
            if _STOP_EVENT.is_set():
                break
            try:
                resp = client.responses.retrieve(response_id=rid)
            except Exception:
                break
            status = getattr(resp, "status", None)
        if status == "error":
            raise RuntimeError(f"Manus task failed (id {rid})")
        # assistant text lives in output[].content[].text (skip files/empties)
        texts = []
        for msg in (getattr(resp, "output", None) or []):
            if getattr(msg, "role", None) != "assistant":
                continue
            for part in (getattr(msg, "content", None) or []):
                t = getattr(part, "text", None)
                if t:
                    texts.append(t)
        out = "\n".join(texts).strip()
        if not out:
            raise EmptyAIResponse(f"Manus returned no assistant text (status={status})")
        return out

    raise RuntimeError(f"Unhandled provider '{provider}'")

# Module-level cooperative stop: run loops set this so long backoff sleeps inside
# ai_complete can bail out promptly when the user clicks Stop.
import threading as _threading
_STOP_EVENT = _threading.Event()

def request_stop():
    _STOP_EVENT.set()

def clear_stop():
    _STOP_EVENT.clear()

def _interruptible_sleep(seconds):
    """Sleep in small slices so a Stop request ends the wait quickly."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if _STOP_EVENT.is_set():
            return
        time.sleep(min(0.25, end - time.time()))

def _retry_after_seconds(exc):
    """Pull a Retry-After hint (seconds) from a provider exception, if any."""
    # OpenAI/Anthropic SDKs attach .response with headers; also check the message.
    try:
        resp = getattr(exc, "response", None)
        hdrs = getattr(resp, "headers", None) if resp is not None else None
        if hdrs:
            for k in ("retry-after", "Retry-After", "x-ratelimit-reset-requests",
                      "x-ratelimit-reset-tokens"):
                v = hdrs.get(k) if hasattr(hdrs, "get") else None
                if v:
                    m = re.search(r"[\d.]+", str(v))
                    if m:
                        return float(m.group(0))
    except Exception:
        pass
    # message text: "try again in 12s" / "retry after 5 seconds"
    try:
        m = re.search(r"(?:retry[- ]after|try again in)\D*([\d.]+)\s*(m|min|s|sec)?",
                      str(exc), re.I)
        if m:
            val = float(m.group(1))
            unit = (m.group(2) or "s").lower()
            return val * 60 if unit.startswith("m") else val
    except Exception:
        pass
    return None

def ai_complete(prompt_text, images=None, max_tokens=4096, timeout=None,
                retries=3, on_retry=None, want_json=False):
    """Call the active AI provider with defensive extraction + retry on transient
    errors (rate limit / 5xx / overloaded / timeout / network).

    Transient errors are retried patiently (rate-limit/overload get a larger
    budget and honor any Retry-After hint), so they almost never bubble up to the
    user. Raises CreditBalanceError for out-of-credit, or a RuntimeError carrying
    the friendly classified message for non-transient failures. `on_retry(msg)`
    (if given) is called before each retry so the UI can log "retrying…".
    """
    cfg = _ai_cfg(); provider = AI_PROVIDER; images = images or []
    # Per-category retry budgets. Rate-limit / overloaded clear on their own, so
    # we wait them out generously instead of surfacing an error to the user.
    _BUDGET = {"rate_limit": 8, "overloaded": 8, "server": 5,
               "timeout": 4, "network": 4}
    attempt = 0
    last_friendly = None
    while True:
        attempt += 1
        try:
            return _ai_call_once(provider, cfg, prompt_text, images, max_tokens, timeout, want_json)
        except CreditBalanceError:
            raise
        except EmptyAIResponse as e:
            # empty/blocked: retry a couple of times (often transient), then give up
            cat, friendly = "empty", f"{T_disp(provider)}: {e}"
            last_friendly = friendly
            if attempt <= retries:
                _delay = min(2 * attempt, 8)
                if on_retry: on_retry(f"{friendly} — retrying ({attempt}/{retries})…")
                _interruptible_sleep(_delay); continue
            raise RuntimeError(friendly)
        except Exception as e:
            if _is_credit_error(str(e)):
                raise CreditBalanceError(str(e))
            cat, friendly = classify_ai_error(e)
            last_friendly = friendly
            budget = max(retries, _BUDGET.get(cat, 0)) if cat in TRANSIENT_CATEGORIES else 0
            if budget and attempt <= budget:
                # Honor a server-provided Retry-After when present; otherwise back
                # off progressively. Rate-limit/overload wait longer (they clear).
                ra = _retry_after_seconds(e)
                if ra is not None:
                    _delay = min(max(ra, 1), 60)
                elif cat in ("rate_limit", "overloaded"):
                    _delay = min(5 + 5 * attempt, 45)      # 10,15,…cap 45s
                else:
                    _delay = min(2 * attempt, 20)
                if on_retry:
                    on_retry(f"{friendly} — waiting {int(_delay)}s then retry "
                             f"({attempt}/{budget})…")
                _interruptible_sleep(_delay); continue
            raise RuntimeError(friendly)


# ═══════════════════════════════════════════════════════════════════════════════
#  AZURE REST HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
from requests.adapters import HTTPAdapter

_AZ_SESSION = None


def _az_session():
    """Shared, connection-pooled requests.Session for Azure DevOps calls.

    A cold plan generation fires hundreds of small GETs — one per test suite, 16
    in parallel. A bare requests.get() opens a brand-new TCP+TLS connection every
    time; those handshakes are slow AND CPU-heavy, and the CPU work holds Python's
    GIL, which is what made the nav/UI stutter during a cold generate. Reusing
    pooled keep-alive connections removes the per-call handshake entirely: the same
    requests return the same results, but far faster and much lighter on the GIL —
    so we keep the worker count high without starving the UI thread. urllib3's
    connection pool is thread-safe, and we never mutate shared session state (auth
    is passed per request), so sharing it across the worker pool is safe."""
    global _AZ_SESSION
    if _AZ_SESSION is None:
        s = requests.Session()
        # Headroom so 16 concurrent workers (plus other fetches) never block on a
        # free connection. max_retries=0 keeps the existing raise-on-error behavior.
        ad = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        _AZ_SESSION = s
    return _AZ_SESSION


def _azure_get(url, pat=None, timeout=12):
    pat = pat or AZURE_PAT
    try:
        r = _az_session().get(url, auth=("", pat), timeout=timeout)
    except requests.exceptions.SSLError:
        raise RuntimeError("SSL error reaching Azure DevOps. Your network may block dev.azure.com.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot reach Azure DevOps (dev.azure.com). Check your network/firewall.")
    except requests.exceptions.Timeout:
        raise RuntimeError("Azure DevOps request timed out (12s). Network may be blocking it.")
    if r.status_code == 401:
        raise RuntimeError("Authentication failed (401). Check your PAT and its scopes.")
    if r.status_code == 403:
        raise RuntimeError("Access denied (403). Your PAT may lack Test Management permission.")
    if r.status_code == 404:
        raise RuntimeError("Not found (404). Check the project name spelling.")
    r.raise_for_status()
    return r.json()

def validate_pat(pat):
    """Returns (ok, message). Lightweight check that the PAT can reach the org."""
    try:
        _azure_get(f"https://dev.azure.com/{AZURE_ORG}/_apis/projects?api-version=7.0", pat)
        return True, "Valid"
    except Exception as e:
        return False, str(e)

def validate_api_key():
    """Cheap check that the configured AI provider key works.
    Returns (ok, category). category is one of: ok, credit, ratelimited, auth,
    bad_model, network, timeout, content_filter, server, overloaded,
    missing-package:<pkg>, or error:<message>. The UI maps these to friendly text.
    Uses a single direct call (no retry) so Connect is fast.
    """
    cfg = _ai_cfg(); provider = AI_PROVIDER
    if provider == "manus":
        # A normal "ping" would create a real (billed, slow) Manus task. Instead
        # verify the key cheaply by listing tasks (no task creation, no credits).
        try:
            from openai import OpenAI
            client = OpenAI(base_url=cfg.get("base_url") or "https://api.manus.im",
                            api_key="placeholder", default_headers={"API_KEY": cfg["api_key"]})
            client.get("/v1/tasks?limit=1", cast_to=object)
            return True, "ok"
        except ModuleNotFoundError as e:
            missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
            return False, f"missing-package:{missing}"
        except Exception as e:
            cat, friendly = classify_ai_error(e)
            if cat == "auth":
                return False, "auth"
            if cat == "credit":
                return True, "credit"
            if cat == "rate_limit":
                return True, "ratelimited"
            if cat in ("network", "timeout", "server", "overloaded"):
                return False, cat
            return False, "error:" + friendly
    try:
        _ai_call_once(provider, cfg, "ping", [], 8, None)
        return True, "ok"
    except CreditBalanceError:
        return True, "credit"          # key valid, just out of credit
    except ModuleNotFoundError as e:
        missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
        return False, f"missing-package:{missing}"
    except EmptyAIResponse:
        # got a (blocked/empty) response — that still means the key authenticated
        return True, "ok"
    except Exception as e:
        cat, friendly = classify_ai_error(e)
        if cat == "credit":
            return True, "credit"
        if cat == "rate_limit":
            return True, "ratelimited"  # key valid, just throttled
        if cat in ("auth",):
            return False, "auth"
        if cat in ("network", "timeout", "server", "overloaded", "content_filter"):
            return False, cat
        if cat == "bad_model":
            return False, "error:" + friendly
        return False, "error:" + friendly

# ── Model discovery ───────────────────────────────────────────────────────────
# Curated fallbacks shown when a live /models fetch fails or returns nothing.
# Chat/vision-capable text models only (no embeddings / audio / image-gen).
STATIC_MODELS = {
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
                  "claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest"],
    "openai":    ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini"],
    "gemini":    ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    "nvidia":    ["meta/llama-3.1-70b-instruct", "meta/llama-3.1-405b-instruct",
                  "qwen/qwen2.5-72b-instruct", "deepseek-ai/deepseek-r1",
                  "nvidia/llama-3.1-nemotron-70b-instruct"],
    "deepseek":  ["deepseek-chat", "deepseek-reasoner"],
    "qwen":      ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-vl-max", "qwen-vl-plus"],
    "azure_openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
    "ollama":    ["llama3.1", "llama3.2", "qwen2.5", "mistral", "gemma2"],
    # Manus "models" are agent profiles, not chat models (no live /models list).
    "manus":     ["manus-1.6", "manus-1.6-lite", "manus-1.6-max"],
}

def _is_chat_model_id(provider, mid):
    """Filter out non-chat model ids (embeddings, tts, image, moderation, …)."""
    s = mid.lower()
    bad = ("embedding", "embed", "whisper", "tts", "audio", "moderation",
           "dall-e", "image", "vision-instruct-embed", "rerank", "guard",
           "search", "similarity", "code-search", "davinci", "babbage", "ada",
           "realtime", "transcribe")
    if any(b in s for b in bad):
        return False
    if provider == "openai":
        # keep gpt-*/o*-family chat models
        return s.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))
    return True

def list_models(provider=None, api_key=None, base_url=None, timeout=15):
    """Return (models, source) where source is 'live' or 'static'.
    Fetches the provider's model catalogue with the SELECTED provider's key.
    Falls back to STATIC_MODELS on any error so the dropdown is never empty.
    """
    p = provider or AI_PROVIDER
    cfg = AI_CONFIG.get(p, {})
    key = (api_key or cfg.get("api_key") or "").strip()
    burl = base_url or cfg.get("base_url")
    static = STATIC_MODELS.get(p, [])

    # Manus has no live model list — the "models" are fixed agent profiles.
    if p == "manus":
        return (static, "static")

    def _ok(lst):
        # de-dupe, keep order, drop obvious non-chat ids, cap length
        seen, out = set(), []
        for m in lst:
            m = (m or "").strip()
            if not m or m in seen:
                continue
            if not _is_chat_model_id(p, m):
                continue
            seen.add(m); out.append(m)
        return out

    try:
        if p == "anthropic":
            import anthropic
            res = anthropic.Anthropic(api_key=key).models.list(limit=100)
            ids = [getattr(m, "id", None) for m in getattr(res, "data", []) or []]
            ids = _ok([i for i in ids if i])
            return (ids or static), ("live" if ids else "static")

        if p in ("openai", "nvidia", "deepseek", "qwen"):
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=burl) if burl else OpenAI(api_key=key)
            res = client.models.list()
            ids = [getattr(m, "id", None) for m in getattr(res, "data", []) or []]
            ids = _ok([i for i in ids if i])
            ids.sort()
            return (ids or static), ("live" if ids else "static")

        if p == "azure_openai":
            from openai import AzureOpenAI
            client = AzureOpenAI(api_key=key, azure_endpoint=cfg.get("endpoint"),
                                 api_version=cfg.get("api_version", "2024-06-01"))
            res = client.models.list()
            ids = _ok([getattr(m, "id", None) for m in getattr(res, "data", []) or [] if getattr(m, "id", None)])
            return (ids or static), ("live" if ids else "static")

        if p == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=key)
            ids = []
            for m in genai.list_models():
                methods = getattr(m, "supported_generation_methods", []) or []
                if "generateContent" in methods:
                    nm = getattr(m, "name", "") or ""
                    ids.append(nm.split("/", 1)[1] if nm.startswith("models/") else nm)
            ids = _ok(ids)
            return (ids or static), ("live" if ids else "static")

        if p == "ollama":
            r = requests.get(f"{(burl or 'http://localhost:11434')}/api/tags", timeout=timeout)
            r.raise_for_status()
            ids = _ok([m.get("name") for m in (r.json().get("models") or [])])
            return (ids or static), ("live" if ids else "static")

    except Exception:
        return static, "static"
    return static, "static"


def fetch_projects(pat=None):
    data = _azure_get(f"https://dev.azure.com/{AZURE_ORG}/_apis/projects?api-version=7.0", pat)
    return sorted([p["name"] for p in data.get("value", [])])

def fetch_iterations(project, pat=None):
    url = (f"https://dev.azure.com/{AZURE_ORG}/{project}"
           f"/_apis/wit/classificationnodes/iterations?$depth=10&api-version=7.0")
    data = _azure_get(url, pat)
    out = []
    def _walk(node, prefix):
        name = node.get("name", "")
        path = (prefix + "\\" + name) if prefix else name
        out.append({"name": name, "path": path, "id": node.get("identifier", "")})
        for ch in node.get("children", []) or []:
            _walk(ch, path)
    for child in data.get("children", []) or []:
        _walk(child, project)
    if not out:
        out.append({"name": project, "path": project, "id": data.get("identifier", "")})
    return out

def fetch_stories_in_iteration(project, iteration_path, pat=None):
    pat = pat or AZURE_PAT
    safe = iteration_path.replace("'", "''")
    wiql = {"query": ("SELECT [System.Id], [System.Title] FROM WorkItems "
                      "WHERE [System.WorkItemType] = 'User Story' "
                      f"AND [System.IterationPath] = '{safe}' ORDER BY [System.Id]")}
    url = f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/wiql?api-version=7.0"
    r = requests.post(url, json=wiql, auth=("", pat), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"WIQL query failed (HTTP {r.status_code})")
    ids = [w["id"] for w in r.json().get("workItems", [])]
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 200):
        batch = ids[i:i+200]
        burl = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workitems"
                f"?ids={','.join(map(str,batch))}&fields=System.Id,System.Title&api-version=7.0")
        br = requests.get(burl, auth=("", pat), timeout=30)
        if br.status_code == 200:
            for w in br.json().get("value", []):
                out.append({"id": w["id"], "title": w["fields"].get("System.Title", "")})
    return out

def create_plan_with_sprint_suites(project, name, iteration_path, cb=None, pat=None):
    """Create a test plan, then add a requirement-based suite for every User Story
    in the chosen sprint (iteration_path). PAT-only — no AI calls.
    cb(event, payload) events:
        "plan"     -> {"plan_id": id}
        "stories"  -> {"total": N}
        "suite"    -> {"done": i, "total": N, "story_id": sid, "title": t, "ok": bool}
        "done"     -> {"plan_id": id, "story_ids": [...], "created": k, "skipped": s, "failed": f}
    Returns (plan_id, story_ids).
    """
    pat = pat or AZURE_PAT
    cb = cb or (lambda *a, **k: None)

    # 1) create the plan
    plan_id = create_test_plan(project, name, iteration_path, pat)
    cb("plan", {"plan_id": plan_id})

    # 2) all User Stories in the sprint
    stories = fetch_stories_in_iteration(project, iteration_path, pat)
    total = len(stories)
    cb("stories", {"total": total})

    story_ids = [s["id"] for s in stories]
    if total == 0:
        cb("done", {"plan_id": plan_id, "story_ids": [], "created": 0, "skipped": 0, "failed": 0})
        return plan_id, []

    # 3) requirement suite per story
    root = _get_root_suite_id(project, plan_id, pat)
    created = skipped = failed = 0
    for i, s in enumerate(stories, 1):
        sid = s["id"]; title = s.get("title", "")
        ok = True
        try:
            res = create_requirement_suite(project, plan_id, sid, root, pat)
            if res is None:
                skipped += 1   # already existed
            else:
                created += 1
        except Exception:
            failed += 1; ok = False
        cb("suite", {"done": i, "total": total, "story_id": sid, "title": title, "ok": ok})

    cb("done", {"plan_id": plan_id, "story_ids": story_ids,
                "created": created, "skipped": skipped, "failed": failed})
    return plan_id, story_ids


def fetch_test_plans(project, pat=None):
    data = _azure_get(f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/testplan/plans?api-version=7.0", pat)
    return [{"id": p["id"], "name": p["name"]} for p in data.get("value", [])]

def create_test_plan(project, name, iteration_path, pat=None):
    pat = pat or AZURE_PAT
    url = f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/testplan/plans?api-version=7.0"
    body = {"name": name, "iteration": iteration_path}
    try:
        r = requests.post(url, json=body, auth=("", pat), timeout=30)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("No internet connection or Azure DevOps is unreachable.")
    if r.status_code == 401:
        raise RuntimeError("Authentication failed (401). Check your PAT.")
    if r.status_code == 403:
        raise RuntimeError("Access denied (403). PAT needs Test Management (read & write).")
    if r.status_code == 400:
        raise RuntimeError(f"Invalid request (400). The iteration path may be wrong.\n{r.text[:160]}")
    r.raise_for_status()
    return r.json()["id"]

def _get_root_suite_id(project, plan_id, pat=None):
    data = _azure_get(f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/testplan/plans/{plan_id}?api-version=7.0", pat)
    return data.get("rootSuite", {}).get("id")


def sprint_summary(project, plan_id, pat=None):
    """Build a status summary for the sprint behind a test plan.

    Reads the plan's iteration, finds every User Story in that iteration, tallies
    their states, and counts the test cases mapped to each story's suite.
    Returns a dict:
        {
          "plan_name": str, "iteration": str,
          "total_stories": int,
          "by_state": {state: count, ...},
          "stories": [{"id","title","state","test_cases"}, ...],
          "total_test_cases": int,
        }
    """
    pat = pat or AZURE_PAT
    # 1) plan → iteration path + name
    plan = _azure_get(f"https://dev.azure.com/{AZURE_ORG}/{project}"
                      f"/_apis/testplan/plans/{plan_id}?api-version=7.0", pat)
    plan_name = plan.get("name", str(plan_id))
    iteration = plan.get("iteration") or ""

    # 2) all User Stories in that iteration (id + title + state)
    stories = []
    if iteration:
        safe = iteration.replace("'", "''")
        wiql = {"query": ("SELECT [System.Id] FROM WorkItems "
                          "WHERE [System.WorkItemType] = 'User Story' "
                          f"AND [System.IterationPath] = '{safe}' ORDER BY [System.Id]")}
        url = f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/wiql?api-version=7.0"
        r = requests.post(url, json=wiql, auth=("", pat), timeout=30)
        ids = [w["id"] for w in r.json().get("workItems", [])] if r.status_code == 200 else []
        for i in range(0, len(ids), 200):
            batch = ids[i:i+200]
            burl = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workitems"
                    f"?ids={','.join(map(str,batch))}"
                    f"&fields=System.Id,System.Title,System.State&api-version=7.0")
            br = requests.get(burl, auth=("", pat), timeout=30)
            if br.status_code == 200:
                for w in br.json().get("value", []):
                    f = w.get("fields", {})
                    stories.append({"id": w["id"],
                                    "title": f.get("System.Title", ""),
                                    "state": f.get("System.State", "Unknown")})

    # 3) test-case counts per story (via that story's suite in the plan)
    story_ids = [s["id"] for s in stories]
    smap = {}
    if story_ids:
        try:
            smap = discover_suites_for_stories(project, plan_id, set(story_ids),
                                               create_missing=False)
        except Exception:
            smap = {}
    # Count test cases per story CONCURRENTLY — one suite fetch per story serially
    # made the sprint summary crawl on big sprints.
    import concurrent.futures as _cf
    def _count_story(s):
        suite_id = smap.get(s["id"])
        if not suite_id:
            return s["id"], 0
        try:
            return s["id"], len(fetch_test_cases_for_suite(project, plan_id, suite_id))
        except Exception:
            return s["id"], 0
    total_tc = 0
    if stories:
        with _cf.ThreadPoolExecutor(max_workers=min(16, len(stories))) as _ex:
            _counts = dict(_ex.map(_count_story, stories))
        for s in stories:
            s["test_cases"] = _counts.get(s["id"], 0)
            total_tc += s["test_cases"]

    # 4) tally states
    by_state = {}
    for s in stories:
        by_state[s["state"]] = by_state.get(s["state"], 0) + 1

    return {
        "plan_name": plan_name,
        "iteration": iteration,
        "total_stories": len(stories),
        "by_state": by_state,
        "stories": stories,
        "total_test_cases": total_tc,
        "project": project,
        "org": AZURE_ORG,
    }

def create_requirement_suite(project, plan_id, story_id, root_suite_id=None, pat=None):
    pat = pat or AZURE_PAT
    if root_suite_id is None:
        root_suite_id = _get_root_suite_id(project, plan_id, pat)
    if not root_suite_id:
        raise RuntimeError("Could not find the plan's root suite.")
    url = f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/testplan/Plans/{plan_id}/suites?api-version=7.1"
    body = {"suiteType": "requirementTestSuite", "name": str(story_id),
            "requirementId": int(story_id), "parentSuite": {"id": int(root_suite_id)}}
    r = requests.post(url, json=body, auth=("", pat), timeout=30)
    if r.status_code in (200, 201):
        data = r.json()
        if isinstance(data.get("value"), list) and data["value"]:
            return data["value"][0].get("id")
        return data.get("id")
    if r.status_code == 400 and "already" in r.text.lower():
        return None
    raise RuntimeError(f"Create suite failed (HTTP {r.status_code})")


# ═══════════════════════════════════════════════════════════════════════════════
#  JSON parsing (robust for Qwen/DeepSeek quirks)
# ═══════════════════════════════════════════════════════════════════════════════
def parse_json_robust(raw):
    if raw is None:
        raise ValueError("AI returned an empty response (None)")
    raw = str(raw)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    if not raw:
        raise ValueError("AI returned an empty response")
    try: return json.loads(raw)
    except json.JSONDecodeError: pass
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    candidate = m.group(0) if m else raw
    def _repair(s):
        s = re.sub(r"'([^'\n]*?)'(\s*[:,\]}])", r'"\1"\2', s)
        s = re.sub(r"([:,\[{]\s*)'([^'\n]*?)'", r'\1"\2"', s)
        out, in_str, esc = [], False, False
        for ch in s:
            if esc: out.append(ch); esc = False; continue
            if ch == "\\": out.append(ch); esc = True; continue
            if ch == '"': in_str = not in_str; out.append(ch); continue
            if in_str and ch == "\n": out.append("\\n"); continue
            if in_str and ch == "\t": out.append("\\t"); continue
            if in_str and ch == "\r": continue
            out.append(ch)
        return "".join(out)
    try: return json.loads(_repair(candidate))
    except Exception: pass
    objs = re.findall(r"\{[^{}]+\}", candidate, re.DOTALL)
    out = []
    for o in objs:
        for variant in (o, _repair(o)):
            try: out.append(json.loads(variant)); break
            except Exception: continue
    if out: return out
    raise ValueError(f"Cannot parse JSON:\n{raw[:300]}")


# ═══════════════════════════════════════════════════════════════════════════════
#  AZURE SDK CONNECTION (work items, test cases)
# ═══════════════════════════════════════════════════════════════════════════════
_wit_client = None
_test_client = None

def connect_azure_sdk(project):
    """Initialize the azure-devops SDK clients. Returns (wit_client, test_client)."""
    global _wit_client, _test_client
    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication
    org_url = f"https://dev.azure.com/{AZURE_ORG}"
    creds = BasicAuthentication("", AZURE_PAT)
    conn = Connection(base_url=org_url, creds=creds)
    _wit_client  = conn.clients.get_work_item_tracking_client()
    _test_client = conn.clients.get_test_client()
    return _wit_client, _test_client


# ── Assign-to-tester (identity / picklist field on the sprint's user stories) ──
_FIELD_REF_CACHE = {}

def resolve_field_ref(project, label, pat=None):
    """Find a field's reference name by its display label (e.g. 'Assigned To
    Tester'), so callers don't need to know the internal Custom.* name."""
    key = (project, (label or "").strip().lower())
    if key in _FIELD_REF_CACHE:
        return _FIELD_REF_CACHE[key]
    url = f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/fields?api-version=7.0"
    ref = None
    try:
        data = _azure_get(url, pat)
        for f in data.get("value", []) or []:
            if (f.get("name", "") or "").strip().lower() == key[1]:
                ref = f.get("referenceName")
                break
    except Exception:
        ref = None
    _FIELD_REF_CACHE[key] = ref
    return ref

def tester_allowed_values(project, field_ref, wit_type="User Story", pat=None):
    """The field's allowed values (the Azure 'list') for a work item type, or []."""
    try:
        url = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workitemtypes/"
               f"{wit_type}/fields/{field_ref}?api-version=7.0")
        data = _azure_get(url, pat)
        return list(data.get("allowedValues", []) or [])
    except Exception:
        return []

def _match_identity(name, allowed):
    """Map a resource name to one allowed value. Returns (value, error_or_None).
    Exact (case-insensitive) > startswith > contains; ambiguous → error."""
    n = (name or "").strip().lower()
    if not n:
        return None, "no name"
    for pred in (lambda a: a.strip().lower() == n,
                 lambda a: a.strip().lower().startswith(n),
                 lambda a: n in a.strip().lower()):
        hits = [a for a in allowed if pred(a)]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f"'{name}' matches several testers ({', '.join(hits[:4])})"
    return None, f"no match for '{name}' in the Assigned To Tester list"

def assign_tester(project, work_item_id, value, field_ref):
    """Write the identity/picklist field on one work item. Raises on failure."""
    from azure.devops.v7_0.work_item_tracking.models import JsonPatchOperation
    if _wit_client is None:
        connect_azure_sdk(project)
    patch = [JsonPatchOperation(op="add", path=f"/fields/{field_ref}", value=value)]
    _wit_client.update_work_item(patch, id=int(work_item_id))

def assign_testers(project, assignments, field_label="Assigned To Tester", cb=None):
    """assignments = [{'id': story_id, 'name': resource_name}, …].
    Resolves the field by its label, matches each name to the field's allowed list
    (readable error when it doesn't match), and writes it.
    Returns {'ok': n, 'field': ref, 'errors': [str, …]}."""
    cb = cb or (lambda *a, **k: None)
    field_ref = resolve_field_ref(project, field_label)
    if not field_ref:
        return {"ok": 0, "field": None,
                "errors": [f"No field named '{field_label}' exists in this project."]}
    allowed = tester_allowed_values(project, field_ref)
    ok, errors = 0, []
    for a in assignments:
        sid, name = a.get("id"), (a.get("name") or "").strip()
        if not name:
            errors.append(f"Story {sid}: no assignee."); continue
        if allowed:
            value, err = _match_identity(name, allowed)
            if err:
                errors.append(f"Story {sid}: {err}."); continue
        else:
            value = name  # no enumerable list — let Azure resolve / reject it
        try:
            assign_tester(project, sid, value, field_ref)
            ok += 1
            cb(f"Assigned {value} → story {sid}", "ok")
        except Exception as e:
            msg = str(e)
            if any(t in msg for t in ("resolve", "TF401", "TF51", "not a valid")) \
               or "does not" in msg.lower():
                errors.append(f"Story {sid}: '{name}' isn't a valid Assigned To Tester.")
            else:
                errors.append(f"Story {sid}: {msg[:120]}")
    return {"ok": ok, "field": field_ref, "errors": errors}


def fetch_stories_in_plan(project, plan_id, pat=None):
    """User stories referenced by a test plan's requirement-based suites —
    independent of any sprint/iteration (so it works for plans that have no
    iteration set). Returns [{"id": int, "title": str}] sorted by id."""
    pat = pat or AZURE_PAT
    url = (f"https://dev.azure.com/{AZURE_ORG}/{project}"
           f"/_apis/testplan/Plans/{plan_id}/Suites?api-version=7.0&$expand=true")
    try:
        resp = requests.get(url, auth=("", pat), timeout=30)
    except requests.exceptions.ConnectionError:
        raise RuntimeError("No internet connection or Azure DevOps is unreachable.")
    if resp.status_code != 200:
        raise RuntimeError(f"Could not fetch suites (HTTP {resp.status_code})")
    ids = set()
    for suite in resp.json().get("value", []):
        rid = suite.get("requirementId")
        if rid:
            try:
                ids.add(int(rid))
            except Exception:
                pass
        else:  # QA-Studio-created suites may be named "<id>" or "<id>: title"
            try:
                ids.add(int(str(suite.get("name", "")).split(":")[0].strip()))
            except (ValueError, IndexError):
                pass
    ids = sorted(ids)
    titles, sprints = {}, {}
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        burl = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workitems"
                f"?ids={','.join(map(str, batch))}"
                f"&fields=System.Id,System.Title,System.IterationPath&api-version=7.0")
        try:
            br = requests.get(burl, auth=("", pat), timeout=30)
            if br.status_code == 200:
                for w in br.json().get("value", []):
                    f = w.get("fields", {})
                    titles[int(w["id"])] = f.get("System.Title", "")
                    sprints[int(w["id"])] = (f.get("System.IterationPath", "") or "").split("\\")[-1]
        except Exception:
            pass
    return [{"id": sid, "title": titles.get(sid, ""), "sprint": sprints.get(sid, "")}
            for sid in ids]


def discover_suites_for_stories(project, plan_id, story_ids, create_missing=True):
    """Match each story to a suite in the plan; auto-create requirement suites for
    any story without one (unless create_missing=False). Returns {story_id: suite_id}."""
    url = (f"https://dev.azure.com/{AZURE_ORG}/{project}"
           f"/_apis/testplan/Plans/{plan_id}/Suites?api-version=7.0&$expand=true")
    resp = requests.get(url, auth=("", AZURE_PAT), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not fetch suites (HTTP {resp.status_code})")
    suites = resp.json().get("value", [])
    story_map = {}
    for suite in suites:
        suite_id = suite.get("id")
        req_id = suite.get("requirementId")
        if req_id and int(req_id) in story_ids:
            story_map[int(req_id)] = suite_id; continue
        name = suite.get("name", "")
        try:
            cand = int(name.split(":")[0].strip())
            if cand in story_ids:
                story_map[cand] = suite_id
        except (ValueError, IndexError):
            pass
    missing = [sid for sid in story_ids if sid not in story_map]
    if missing and create_missing:
        try: root_id = _get_root_suite_id(project, plan_id)
        except Exception: root_id = None
        for sid in missing:
            try:
                new_suite = create_requirement_suite(project, plan_id, sid, root_id)
                rr = requests.get(url, auth=("", AZURE_PAT), timeout=30)
                if rr.status_code == 200:
                    found = False
                    for s in rr.json().get("value", []):
                        if s.get("requirementId") and int(s["requirementId"]) == sid:
                            story_map[sid] = s.get("id"); found = True; break
                    if not found and new_suite:
                        story_map[sid] = new_suite
            except Exception:
                pass
    return story_map


def fetch_stories(story_ids):
    """Fetch work items (user stories) with title + acceptance criteria."""
    stories = []
    for sid in story_ids:
        try:
            wi = _wit_client.get_work_item(sid, expand="Relations")
            stories.append(wi)
        except Exception:
            pass
    return stories


def _downscale_image(raw_bytes, max_dim=1024):
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return raw_bytes, None


def fetch_story_screenshots(story):
    shots = []
    for rel in (story.relations or []):
        if rel.rel != "AttachedFile":
            continue
        fname = rel.attributes.get("name", "").lower()
        ext = os.path.splitext(fname)[1]
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            continue
        try:
            r = requests.get(rel.url, auth=("", AZURE_PAT), timeout=30)
            if r.status_code == 200:
                raw, mt = _downscale_image(r.content)
                media = mt or ("image/png" if ext == ".png" else "image/jpeg")
                shots.append({"b64": base64.b64encode(raw).decode(), "media_type": media, "name": fname})
        except Exception:
            pass
    return shots


def describe_story_ui(screenshots, story_title=""):
    if not screenshots:
        return ""
    prompt = f"""
        أنت مهندس ضمان جودة خبير. لديك صورة/صور لواجهة المستخدم لميزة بعنوان: {story_title}
        صف الواجهة بدقة ووضوح باللغة العربية: العناصر الظاهرة (حقول، أزرار، قوائم، جداول،
        رسائل)، أسماؤها، وأي نصوص مهمة، وكيفية ترتيبها وتفاعلها.
        هذا الوصف سيُستخدم لكتابة خطوات اختبار، لذا ركّز على التفاصيل العملية القابلة للاختبار.
        أعد وصفاً نصياً فقط بدون أي صيغة JSON.
    """
    images = [{"media_type": sc["media_type"], "data": sc["b64"]} for sc in screenshots]
    try:
        return (ai_complete(prompt, images=images, max_tokens=1500) or "").strip()
    except CreditBalanceError:
        raise
    except Exception:
        return ""


def fetch_test_cases_for_suite(project, plan_id, suite_id):
    # witFields=System.Id keeps the response to the bare work-item id per case
    # instead of the full test-case payload. The number of entries (what callers
    # count) is unchanged, but each response is a fraction of the size, so the
    # bulk per-suite counting during plan generation is dramatically faster.
    url = (f"https://dev.azure.com/{AZURE_ORG}/{project}/"
           f"_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase"
           f"?witFields=System.Id&api-version=7.0")
    # Retry on throttling / transient server errors. Azure DevOps rate-limits bulk
    # counting (hundreds of suites) and returns HTTP 429 with a Retry-After header;
    # honoring it (instead of failing -> a silent count of 0, or hammering) keeps
    # the counts correct and avoids wasted requests.
    # Bounded retry: enough to ride out brief throttling, but capped so a heavily
    # rate-limited cold count (e.g. Regenerate over hundreds of suites) can't stall
    # for minutes. Worst case ≈ 3 attempts × ~6 s ≈ 18 s for a single stubborn suite.
    last = 0
    for _attempt in range(3):
        resp = _az_session().get(url, auth=("", AZURE_PAT), timeout=30)
        last = resp.status_code
        if last == 200:
            return resp.json().get("value", [])
        if last == 429:
            try:
                wait = float(resp.headers.get("Retry-After", "1"))
            except Exception:
                wait = 1.0
            time.sleep(min(max(wait, 0.5), 6))     # honor Retry-After, capped at 6 s
            continue
        if 500 <= last < 600:
            time.sleep(0.4 * (_attempt + 1))       # transient server error -> brief backoff
            continue
        break                                       # other 4xx -> not retryable
    raise RuntimeError(f"HTTP {last}")


def _strip_html(s):
    """Remove HTML tags/entities from an Azure step fragment → plain text."""
    if not s:
        return ""
    import html as _h
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _h.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_steps_xml(steps_xml):
    """Parse Azure's Microsoft.VSTS.TCM.Steps XML into a list of:
        {"index": int, "action": str, "expected": str}
    The format is <steps><step><parameterizedString>action</parameterizedString>
    <parameterizedString>expected</parameterizedString></step>...</steps>.
    """
    if not steps_xml or "<step " not in steps_xml:
        return []
    out = []
    try:
        import xml.etree.ElementTree as ET
        # wrap in case of stray entities; Azure XML is generally well-formed
        root = ET.fromstring(steps_xml)
        steps = root.findall(".//step")
        for i, st in enumerate(steps, 1):
            ps = st.findall("parameterizedString")
            action = _strip_html(ps[0].text if len(ps) >= 1 and ps[0].text else "")
            expected = _strip_html(ps[1].text if len(ps) >= 2 and ps[1].text else "")
            if action or expected:
                out.append({"index": i, "action": action, "expected": expected})
    except Exception:
        # Fallback: regex the parameterizedString pairs
        try:
            chunks = re.findall(r"<step\b.*?</step>", steps_xml, flags=re.S)
            for i, ch in enumerate(chunks, 1):
                ps = re.findall(r"<parameterizedString[^>]*>(.*?)</parameterizedString>", ch, flags=re.S)
                action = _strip_html(ps[0]) if len(ps) >= 1 else ""
                expected = _strip_html(ps[1]) if len(ps) >= 2 else ""
                if action or expected:
                    out.append({"index": i, "action": action, "expected": expected})
        except Exception:
            pass
    return out


def fetch_test_case_steps(tc_id):
    """Return parsed steps [{index,action,expected}] for a single test case id."""
    try:
        wi = _wit_client.get_work_item(tc_id, fields=["Microsoft.VSTS.TCM.Steps"])
        xml = (wi.fields or {}).get("Microsoft.VSTS.TCM.Steps", "") or ""
        return parse_steps_xml(xml)
    except Exception:
        return []



import time

def _is_arabic_out():
    return OUTPUT_LANG != "en"

def generate_steps(tc_title, acceptance_criteria, ui_description="", log=None):
    if _is_arabic_out():
        ui_block = f"\n        وصف واجهة المستخدم (مستخلص من الصور):\n        {ui_description}\n" if ui_description else ""
        text = f"""
        أنت مهندس ضمان جودة (QA) خبير. أنشئ خطوات اختبار تفصيلية لحالة الاختبار التالية.
        اكتب جميع الخطوات باللغة العربية فقط.
        أعد فقط مصفوفة JSON — بدون أي نص إضافي أو markdown.
        مهم: لا تستخدم علامات الاقتباس المزدوجة داخل قيم النصوص.

        قواعد صارمة لمنع الخطوات المكررة أو الزائدة:
        - كل خطوة = إجراء واحد فقط يقوم به المستخدم، مع نتيجته المتوقعة في حقل expected.
        - لا تنشئ خطوة منفصلة لمجرد إعادة وصف إجراء سابق أو نتيجته. مثال خاطئ يجب تجنّبه:
          أربع خطوات كلها تقول «تم النقر على أيقونة تغيير اللغة وظهرت القائمة» — هذا تكرار،
          اجعلها خطوة واحدة (النقر) ونتيجتها في expected (ظهور القائمة).
        - الشروط البيئية أو شروط الحالة (يوجد اتصال إنترنت، فتح المتصفح، المستخدم على صفحة
          تسجيل الدخول) تُكتب فقط في حقل precondition لأول خطوة مرتبطة، وليست خطوة إجراء مستقلة.
        - التحقق من نتيجة يوضع في حقل expected، لا كخطوة إجراء جديدة.
        - استخدم أقل عدد من الخطوات يغطي السيناريو بالكامل (عادة 2 إلى 6 خطوات).
        - لا تكرر نفس الإجراء عبر خطوات متعددة.

        عنوان حالة الاختبار: {tc_title}
        معايير القبول: {acceptance_criteria}
        وصف الميزة: {FEATURE_DESCRIPTION}{ui_block}

        الصيغة:
        [{{"precondition":"...","action":"...","expected":"..."}}]
    """
    else:
        ui_block = f"\n        UI description (extracted from screenshots):\n        {ui_description}\n" if ui_description else ""
        text = f"""
        You are an expert QA engineer. Generate detailed test steps for the following test case.
        Write ALL steps in English only.
        Return ONLY a JSON array — no extra text or markdown.
        Important: do not use double quotes inside the string values.

        Strict rules to prevent repeated / redundant steps:
        - Each step = exactly ONE concrete user action, with its expected result in 'expected'.
        - Do NOT create a separate step that merely restates a previous action or its outcome.
          Bad example to avoid: four steps that all say "clicked the language icon and the menu
          appeared" — that is duplication; make it ONE step (the click) with the menu appearing
          in 'expected'.
        - Environmental / state preconditions (internet is available, browser opened, user is on
          the login page) go ONLY in the 'precondition' field of the first related step — never
          as their own action step.
        - Verifying an outcome goes in 'expected', not as a new action step.
        - Use the FEWEST steps that fully cover the scenario (usually 2-6).
        - Never repeat the same action across multiple steps.

        Test case title: {tc_title}
        Acceptance criteria: {acceptance_criteria}
        Feature description: {FEATURE_DESCRIPTION}{ui_block}

        Format:
        [{{"precondition":"...","action":"...","expected":"..."}}]
    """
    time.sleep(1)
    last_err = None
    for attempt in range(5):
        try:
            return parse_json_robust(ai_complete(text, max_tokens=4096, want_json=True))
        except CreditBalanceError:
            raise
        except Exception as e:
            last_err = e; es = str(e).lower()
            if "429" in es or "rate_limit" in es:
                w = 30*(attempt+1)
                if log: log(f"Rate limited — waiting {w}s (attempt {attempt+1}/5)…", "warn")
                time.sleep(w)
            elif any(k in es for k in ("500","502","503","out of memory","cuda","internal server","overloaded")):
                w = 10*(attempt+1)
                if log: log(f"Provider busy/GPU error — retrying in {w}s (attempt {attempt+1}/5)…", "warn")
                time.sleep(w)
            elif "empty response" in es or "cannot parse json" in es:
                if log: log(f"Bad/empty AI response — retrying (attempt {attempt+1}/5)…", "warn")
                time.sleep(3)
            else:
                raise
    raise RuntimeError(f"Failed after 5 attempts: {last_err}")


def evaluate_existing_steps(tc_title, criteria, existing_steps_xml):
    plain = re.sub(r"<[^>]+>", " ", existing_steps_xml or "")
    plain = _html.unescape(re.sub(r"\s+", " ", plain)).strip()[:4000]
    if _is_arabic_out():
        prompt = f"""
        أنت مهندس ضمان جودة خبير. لديك حالة اختبار بخطواتها الحالية، ومعايير القبول الخاصة بها.
        مهمتك: قرر هل الخطوات الحالية كافية وتغطي معايير القبول بشكل صحيح أم لا.
        اعتبر الخطوات غير كافية (adequate=false) إذا وُجد أي مما يلي:
        - خطوات مكررة تعيد وصف نفس الإجراء أو نتيجته أكثر من مرة.
        - شروط بيئية مكتوبة كخطوات إجراء مستقلة (اتصال إنترنت، فتح المتصفح، المستخدم على الصفحة).
        - نتيجة متوقعة مكتوبة كخطوات إجراء متعددة بدلاً من حقل النتيجة.
        عنوان حالة الاختبار: {tc_title}
        معايير القبول: {criteria}
        الخطوات الحالية: {plain}
        أعد فقط كائن JSON: {{"adequate": true/false, "reason": "سبب مختصر بالعربية"}}
    """
        fallback_reason = "تعذر تحليل نتيجة التقييم"
    else:
        prompt = f"""
        You are an expert QA engineer. You have a test case with its current steps and its
        acceptance criteria. Your task: decide whether the current steps are adequate and
        correctly cover the acceptance criteria or not.
        Consider the steps INADEQUATE (adequate=false) if any of these are present:
        - repeated steps that restate the same action or its outcome more than once;
        - environmental preconditions written as their own action steps (internet available,
          browser opened, user is on the page);
        - an expected outcome written as several action steps instead of an 'expected' result.
        Test case title: {tc_title}
        Acceptance criteria: {criteria}
        Current steps: {plain}
        Return ONLY a JSON object: {{"adequate": true/false, "reason": "short reason in English"}}
    """
        fallback_reason = "Could not parse the evaluation result"
    raw = (ai_complete(prompt, max_tokens=1024) or "").strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = parse_json_robust(raw)
        if isinstance(data, list) and data: data = data[0]
        return {"adequate": bool(data.get("adequate", False)), "reason": str(data.get("reason", "")).strip()}
    except Exception:
        return {"adequate": False, "reason": fallback_reason}


def build_steps_xml(steps):
    n = len(steps)
    xml = f'<steps id="0" last="{n + 1}">'
    for i, s in enumerate(steps, 2):
        pre = s.get("precondition", "").strip()
        action = s.get("action", "").strip()
        exp = s.get("expected", "").strip()
        action_text = (f"الشرط المسبق: {pre}\nالإجراء: {action}" if pre else action) if _is_arabic_out() \
            else (f"Precondition: {pre}\nAction: {action}" if pre else action)
        action_html = "<DIV><P>" + _html.escape(action_text).replace("\n", "</P><P>") + "</P></DIV>" if action_text else "<DIV><P></P></DIV>"
        exp_html = "<DIV><P>" + _html.escape(exp).replace("\n", "</P><P>") + "</P></DIV>" if exp else "<DIV><P></P></DIV>"
        xml += (f'<step id="{i}" type="ValidateStep">'
                f'<parameterizedString isformatted="true">{_html.escape(action_html)}</parameterizedString>'
                f'<parameterizedString isformatted="true">{_html.escape(exp_html)}</parameterizedString>'
                f'<description/></step>')
    return xml + '</steps>'


def update_test_case_with_steps(tc_id, steps_xml, project, story_id=None):
    from azure.devops.v7_0.work_item_tracking.models import JsonPatchOperation
    patch = [JsonPatchOperation(op="add", path="/fields/Microsoft.VSTS.TCM.Steps", value=steps_xml)]
    if story_id:
        try:
            wi = _wit_client.get_work_item(tc_id, expand="Relations")
            rels = wi.relations or []
            linked = any(getattr(r, "rel", "") == "Microsoft.VSTS.Common.TestedBy-Reverse"
                         and str(getattr(r, "url", "")).rstrip("/").endswith(f"/{story_id}") for r in rels)
            if not linked:
                patch.append(JsonPatchOperation(op="add", path="/relations/-", value={
                    "rel": "Microsoft.VSTS.Common.TestedBy-Reverse",
                    "url": f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workItems/{story_id}"}))
        except Exception:
            pass
    _wit_client.update_work_item(patch, id=tc_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  TITLES GENERATION
# ═══════════════════════════════════════════════════════════════════════════════
def generate_titles(story_title, criteria, existing_titles=None, log=None):
    if _is_arabic_out():
        ct = criteria or "لا توجد معايير قبول. أنشئ عناوين عامة بناءً على العنوان."
        existing_block = ""
        if existing_titles:
            listed = "\n".join(f"- {t}" for t in existing_titles[:150])
            existing_block = f"""
            حالات الاختبار التالية موجودة بالفعل لهذه القصة — لا تكررها:
{listed}
            أنشئ فقط عناوين لسيناريوهات جديدة. إذا كانت جميعها مغطاة، أعد مصفوفة فارغة [].
        """
        prompt = f"""
        أنت مهندس QA خبير. أنشئ عناوين حالات اختبار لقصة المستخدم التالية.
        اكتب العناوين باللغة العربية فقط. لا تستخدم علامات اقتباس مزدوجة داخل النصوص.
        أعد فقط مصفوفة JSON.

        قواعد صارمة لمنع التكرار:
        - لا تنشئ عنوانين يختبران نفس السلوك بصياغة مختلفة. كل عنوان يجب أن يغطي
          سيناريو فريداً غير مغطى بأي عنوان آخر.
        - اعتبر العنوانين مكررين إذا كانا يتحققان من نفس القاعدة أو الشرط حتى لو
          اختلفت الكلمات. مثال على تكرار يجب تجنبه:
            • "التحقق من أن الحد الأدنى لحقل الإجابة بالعربي هو حرفان"
            • "التحقق من أن حقل الإجابة بالعربي لا يقبل أقل من 2 حرف"
          هذان عنوانان مكرران — اختر واحداً فقط.
        - لكل حقل أو قاعدة، أنشئ حالة اختبار واحدة فقط لكل سيناريو (صحيح / خاطئ /
          حدّي)، وليس عدة صياغات لنفس السيناريو.
        - ادمج الحالات المتشابهة في عنوان واحد واضح بدلاً من تكرارها.
        - قبل الإخراج، راجع القائمة واحذف أي عنوان يكرر معنى عنوان آخر.

        العنوان: {story_title}
        معايير القبول: {ct}
        {existing_block}
        الصيغة: ["عنوان 1","عنوان 2"]
    """
    else:
        ct = criteria or "No acceptance criteria. Generate general titles based on the title."
        existing_block = ""
        if existing_titles:
            listed = "\n".join(f"- {t}" for t in existing_titles[:150])
            existing_block = f"""
            The following test cases already exist for this story — do NOT duplicate them:
{listed}
            Only generate titles for NEW scenarios. If all are covered, return an empty array [].
        """
        prompt = f"""
        You are an expert QA engineer. Generate test case titles for the following user story.
        Write the titles in English only. Do not use double quotes inside the text.
        Return ONLY a JSON array.

        Strict rules to prevent duplication:
        - Do not create two titles that test the same behavior with different wording.
          Each title must cover a unique scenario not covered by any other title.
        - Treat two titles as duplicates if they verify the same rule or condition even if
          the words differ. Example of a duplicate to avoid:
            • "Verify the minimum length of the Arabic answer field is 2 characters"
            • "Verify the Arabic answer field does not accept fewer than 2 characters"
          These two are duplicates — pick only one.
        - For each field or rule, create only one test case per scenario (valid / invalid /
          boundary), not several phrasings of the same scenario.
        - Merge similar cases into one clear title instead of repeating them.
        - Before output, review the list and remove any title that repeats another's meaning.

        Title: {story_title}
        Acceptance criteria: {ct}
        {existing_block}
        Format: ["title 1","title 2"]
    """
    time.sleep(1)
    last_err = None
    for attempt in range(5):
        try:
            return parse_json_robust(ai_complete(prompt, max_tokens=4096, want_json=True))
        except CreditBalanceError:
            raise
        except Exception as e:
            last_err = e; es = str(e).lower()
            if "429" in es or "rate_limit" in es:
                time.sleep(30*(attempt+1))
            elif any(k in es for k in ("500","502","503","cuda","out of memory","overloaded")):
                time.sleep(10*(attempt+1))
            elif "empty response" in es or "cannot parse json" in es:
                time.sleep(3)
            else:
                raise
    raise RuntimeError(f"Failed after 5 attempts: {last_err}")


def create_test_case(project, plan_id, suite_id, title, story_id):
    """Create a test case work item and add it to the suite. Returns tc_id."""
    from azure.devops.v7_0.work_item_tracking.models import JsonPatchOperation
    patch = [JsonPatchOperation(op="add", path="/fields/System.Title", value=title)]
    if story_id:
        patch.append(JsonPatchOperation(op="add", path="/relations/-", value={
            "rel": "Microsoft.VSTS.Common.TestedBy-Reverse",
            "url": f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/workItems/{story_id}"}))
    wi = _wit_client.create_work_item(patch, project=project, type="Test Case")
    tc_id = wi.id
    try:
        _test_client.add_test_cases_to_suite(project=project, plan_id=plan_id,
                                             suite_id=suite_id, test_case_ids=str(tc_id))
    except Exception:
        pass
    return tc_id


def _norm_title(t):
    t = re.sub(r"[^\w\s\u0600-\u06FF]", "", str(t))
    return re.sub(r"\s+", " ", t).strip().lower()


def delete_test_case(project, plan_id, suite_id, tc_id):
    """Remove a test case from the suite and delete the work item."""
    # 1) remove from suite (best-effort)
    try:
        _test_client.remove_test_cases_from_suite_url(
            project=project, plan_id=plan_id, suite_id=suite_id, test_case_ids=str(tc_id))
    except Exception:
        try:
            url = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/test/Plans/"
                   f"{plan_id}/Suites/{suite_id}/testcases/{tc_id}?api-version=7.0")
            requests.delete(url, auth=("", AZURE_PAT), timeout=30)
        except Exception:
            pass
    # 2) delete the work item itself
    try:
        _wit_client.delete_work_item(id=tc_id, project=project, destroy=False)
        return True
    except Exception:
        try:
            url = (f"https://dev.azure.com/{AZURE_ORG}/{project}/_apis/wit/"
                   f"workitems/{tc_id}?api-version=7.0")
            r = requests.delete(url, auth=("", AZURE_PAT), timeout=30)
            return r.status_code in (200, 204)
        except Exception:
            return False


def dedupe_existing_suite(project, plan_id, suite_id, cb=None, do_delete=True):
    """Find duplicate test cases ALREADY in a suite and remove the less complete
    one of each duplicate group, keeping the most accurate (most steps, then
    oldest id). Duplicates are matched by semantic key (meaning), so
    'لا يقبل أقل من 2 حرف' and 'لا يقبل أقل من حرفين' are treated as the same.

    Returns {"removed": [ {id,title,kept_id} ], "groups": n}.
    """
    cb = cb or (lambda *a, **k: None)
    try:
        cases = fetch_test_cases_for_suite(project, plan_id, suite_id)
    except Exception as e:
        cb("log", {"msg": f"Could not read suite for dedup: {str(e)[:80]}", "tone": "warn"})
        return {"removed": [], "groups": 0}

    # Build records: {id, title, step_count}. The per-case step fetch is done
    # CONCURRENTLY (was serial — slow on big suites).
    import concurrent.futures as _cf

    def _rec(c):
        wi = c.get("workItem", {})
        tc_id = wi.get("id")
        title = wi.get("name", "")
        if not tc_id:
            return None
        try:
            sc = len(fetch_test_case_steps(tc_id))
        except Exception:
            sc = 0
        return {"id": int(tc_id), "title": title, "steps": sc,
                "key": _semantic_key(title), "norm": _norm_title(title)}

    if cases:
        with _cf.ThreadPoolExecutor(max_workers=min(16, len(cases))) as _ex:
            recs = [r for r in _ex.map(_rec, cases) if r]
    else:
        recs = []

    # Group by semantic key (and exact-norm), then within each group decide keeper
    groups = {}
    for r in recs:
        # find an existing group whose key is a near-duplicate
        placed = False
        for gk in list(groups.keys()):
            if r["norm"] and any(r["norm"] == x["norm"] for x in groups[gk]):
                groups[gk].append(r); placed = True; break
            if _is_near_duplicate(r["key"], {gk}):
                groups[gk].append(r); placed = True; break
        if not placed:
            groups[r["key"]] = [r]

    removed = []       # successfully deleted duplicates
    kept_dupes = []    # duplicates we could NOT delete (left in the suite)
    dup_groups = 0
    for gk, members in groups.items():
        if len(members) < 2:
            continue
        dup_groups += 1
        # keeper = most steps (more complete/accurate), tie-break = smallest id (oldest)
        members.sort(key=lambda m: (-m["steps"], m["id"]))
        keeper = members[0]
        cb("log", {"msg": f"Duplicate group ({len(members)}) — keeping #{keeper['id']} "
                          f"({keeper['steps']} steps)", "tone": "info", "ar": True,
                   "id": keeper["id"], "detail": keeper["title"]})
        for victim in members[1:]:
            if not do_delete:
                cb("log", {"msg": f"{victim['title']}", "tone": "warn", "ar": True,
                           "id": victim["id"],
                           "detail": f"duplicate (not deleted) · {victim['steps']} steps · dup of #{keeper['id']}"})
                kept_dupes.append({"id": victim["id"], "title": victim["title"], "kept_id": keeper["id"]})
                continue
            ok = delete_test_case(project, plan_id, suite_id, victim["id"])
            if ok:
                # log the OLD (removed) test — its id + title — and the id we kept
                cb("log", {"msg": f"{victim['title']}", "tone": "skip", "ar": True,
                           "id": victim["id"],
                           "detail": f"removed old #{victim['id']} · {victim['steps']} steps · kept #{keeper['id']}"})
                removed.append({"id": victim["id"], "title": victim["title"], "kept_id": keeper["id"]})
            else:
                # delete failed → the duplicate is STILL there; never count it as removed
                cb("log", {"msg": f"{victim['title']}", "tone": "err", "ar": True,
                           "id": victim["id"],
                           "detail": f"delete FAILED — kept as duplicate · dup of #{keeper['id']}"})
                kept_dupes.append({"id": victim["id"], "title": victim["title"], "kept_id": keeper["id"]})
    if dup_groups:
        if removed:
            tail = (f"; {len(kept_dupes)} could not be deleted (kept)" if kept_dupes else "")
            cb("log", {"msg": f"Removed {len(removed)} duplicate test case"
                              + ("s" if len(removed) != 1 else "")
                              + f" across {dup_groups} group" + ("s" if dup_groups != 1 else "")
                              + tail,
                       "tone": "warn" if kept_dupes else "ok"})
        else:
            cb("log", {"msg": f"Found {dup_groups} duplicate group"
                              + ("s" if dup_groups != 1 else "")
                              + f", but none could be deleted ({len(kept_dupes)} kept).",
                       "tone": "err"})
    else:
        cb("log", {"msg": "No duplicate test cases found in the suite.", "tone": "dim"})
    return {"removed": removed, "kept": kept_dupes, "groups": dup_groups}


# Arabic filler/stop words that don't change a test's meaning — removed before
# comparing two titles for semantic equivalence.
_AR_STOP = {
    "التحقق", "من", "أن", "ان", "هو", "هي", "عند", "في", "على", "إلى", "الى",
    "مع", "عن", "لا", "هذا", "هذه", "يتم", "يجب", "كان", "تكون", "حقل", "الحقل",
    "لحقل", "قيمة", "القيمة", "رسالة", "الرسالة", "زر", "الزر", "صفحة", "الصفحة",
    "إمكانية", "امكانية", "ظهور", "وجود", "بشكل", "صحيح", "الحد",
}
# Synonym groups → canonical token, so "الحد الأدنى ... حرفان" and
# "لا يقبل أقل من 2 حرف" map onto shared concept tokens.
_AR_SYN = {
    # length-amount tokens all collapse to "minlen" concept
    "حرفان": "minlen", "حرفين": "minlen", "حرف": "minlen", "أحرف": "minlen",
    "احرف": "minlen", "2": "minlen", "٢": "minlen",
    # "minimum" and "does not accept less than" express the SAME rule → one token
    "الأدنى": "minrule", "الادنى": "minrule", "أدنى": "minrule", "ادنى": "minrule",
    "أقل": "minrule", "اقل": "minrule",
    "الأقصى": "maxrule", "الاقصى": "maxrule", "أقصى": "maxrule", "اقصى": "maxrule",
    "أكثر": "maxrule", "اكثر": "maxrule",
    "يقبل": "accept", "قبول": "accept", "تقبل": "accept",
    "الإجابة": "answer", "الاجابة": "answer", "إجابة": "answer", "اجابة": "answer",
    "السؤال": "question", "سؤال": "question",
    "العربي": "ar", "بالعربي": "ar", "العربية": "ar", "بالعربية": "ar", "عربي": "ar",
    "الإنجليزي": "en", "الانجليزي": "en", "بالإنجليزي": "en",
    "بالانجليزي": "en", "الإنجليزية": "en", "الانجليزية": "en", "بالإنجليزية": "en",
    "إجباري": "required", "اجباري": "required", "الزامي": "required",
    "إلزامي": "required", "مطلوب": "required",
    "فارغ": "empty", "فارغاً": "empty", "فارغا": "empty", "تركه": "empty",
    # field-name tokens
    "الاسم": "name", "الإسم": "name", "اسم": "name", "إسم": "name",
    "البريد": "email", "الايميل": "email", "الإيميل": "email", "الالكتروني": "email",
    "الإلكتروني": "email", "الهاتف": "phone", "الجوال": "phone", "الموبايل": "phone",
    "رقم": "number", "الرقم": "number",
    # number words → digits so "حرفين" ~ "2 حرف", "ثلاثة" ~ "3"
    "حرفين": "minlen", "حرفان": "minlen",
    "واحد": "1", "واحده": "1", "واحدة": "1", "اثنين": "2", "اثنان": "2",
    "ثلاثة": "3", "ثلاثه": "3", "اربعة": "4", "أربعة": "4", "اربعه": "4",
    "خمسة": "5", "خمسه": "5", "ستة": "6", "سته": "6",
    "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9", "١": "1",
}

def _semantic_key(title):
    """Reduce an Arabic title to a frozenset of meaning tokens, so two titles
    that test the same rule with different wording collapse to the same key."""
    norm = _norm_title(title)
    toks = []
    for w in norm.split():
        w = _AR_SYN.get(w, w)
        if w in _AR_STOP:
            continue
        toks.append(w)
    return frozenset(toks)

def _is_near_duplicate(key, seen_keys, threshold=0.8):
    """True if `key` overlaps an already-seen key by >= threshold (Jaccard)."""
    if not key:
        return False
    for k in seen_keys:
        if not k:
            continue
        inter = len(key & k)
        union = len(key | k)
        if union and inter / union >= threshold:
            return True
        # also treat full subset of a short key as duplicate
        if key <= k or k <= key:
            if min(len(key), len(k)) >= 2:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATORS — driven by the UI via a callback
#  cb(event_type, payload) where event_type in:
#    'log'    payload={'msg','tone','id','indent'}
#    'stat'   payload={'total','stories_done','total_stories','done','skipped','errors'}
#    'progress' payload={'pct','label'}
#    'story'  payload={'id','title'}
#    'done'   payload={'summary', 'action_items', ...}
# ═══════════════════════════════════════════════════════════════════════════════
class StopRequested(Exception):
    pass

def run_titles(project, plan_id, story_ids, cb, should_stop=lambda: False):
    wit, test = connect_azure_sdk(project)
    cb("log", {"msg": "Discovering suites for stories…", "tone": "dim"})
    story_suite_map = discover_suites_for_stories(project, plan_id, set(story_ids))
    stories = fetch_stories(story_ids)

    total_created = 0; errors = 0; stories_done = 0
    total_stories = len(stories)
    _titles_start = time.time()
    per_story_stats = {}   # {sid: {"id","title","total","ok","skipped","err","suite"}}
    cb("stat", {"total": 0, "stories_done": 0, "total_stories": total_stories,
                "done": 0, "skipped": 0, "errors": 0})

    for story in stories:
        if should_stop(): break
        sid = story.id
        title = story.fields.get("System.Title", "No Title")
        criteria = story.fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
        cb("story", {"id": sid, "title": title})
        suite_id = story_suite_map.get(sid)
        if not suite_id:
            cb("log", {"msg": f"No suite for story {sid} — skipped", "tone": "warn"})
            continue
        ps = per_story_stats.setdefault(sid, {"id": sid, "title": title, "total": 0,
                                              "ok": 0, "skipped": 0, "err": 0, "suite": suite_id})
        # Remove pre-existing duplicate test cases in this suite first, keeping the
        # most complete one of each group (catches dupes from prior runs / manual entry).
        try:
            dedupe_existing_suite(project, plan_id, suite_id, cb=cb, do_delete=True)
        except Exception as de:
            cb("log", {"msg": f"Dedup skipped: {str(de)[:80]}", "tone": "warn"})
        # existing titles
        existing_titles = []
        try:
            for it in fetch_test_cases_for_suite(project, plan_id, suite_id):
                nm = (it.get("workItem", {}) or {}).get("name", "").strip()
                if nm: existing_titles.append(nm)
        except Exception:
            pass
        if existing_titles:
            cb("log", {"msg": f"Suite already has {len(existing_titles)} test case(s) — only new added", "tone": "warn"})
        try:
            titles = generate_titles(title, criteria, existing_titles, log=lambda m,t="warn": cb("log",{"msg":m,"tone":t}))
        except CreditBalanceError:
            cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit"}); return
        except Exception as e:
            cb("log", {"msg": f"AI error: {e}", "tone": "err"}); errors += 1; ps["err"] += 1; continue

        existing_norm = {_norm_title(t) for t in existing_titles}
        seen_keys = {_semantic_key(t) for t in existing_titles}
        unique = []
        dropped_dupes = 0
        for t in titles:
            nk = _norm_title(t)
            sk = _semantic_key(t)
            if nk in existing_norm or _is_near_duplicate(sk, seen_keys):
                dropped_dupes += 1
                continue
            unique.append(t)
            existing_norm.add(nk)
            seen_keys.add(sk)
        if dropped_dupes:
            ps["skipped"] += dropped_dupes
            cb("log", {"msg": f"Skipped {dropped_dupes} duplicate/near-duplicate title"
                              + ("s" if dropped_dupes > 1 else ""), "tone": "dim", "ico": "⏭"})
        for tc_title in unique:
            if should_stop(): break
            ps["total"] += 1
            _tc_start = time.time()
            try:
                tc_id = create_test_case(project, plan_id, suite_id, tc_title, sid)
                total_created += 1
                ps["ok"] += 1
                _elapsed = time.time() - _tc_start
                ps["secs"] = ps.get("secs", 0.0) + _elapsed
                cb("log", {"msg": tc_title, "tone": "ok", "id": tc_id, "ar": True,
                           "secs": round(_elapsed, 1), "detail": f"⏱ {_fmt_mmss(_elapsed)}"})
            except Exception as e:
                errors += 1
                ps["err"] += 1
                ps["secs"] = ps.get("secs", 0.0) + (time.time() - _tc_start)
                cb("log", {"msg": f"{tc_title} — {e}", "tone": "err", "ar": True})
            cb("stat", {"total": total_created, "stories_done": stories_done,
                        "total_stories": total_stories, "done": total_created,
                        "skipped": 0, "errors": errors})
        if not should_stop():
            stories_done += 1
            cb("log", {"msg": f"Story {sid} completed", "tone": "ok", "ico": "└"})

    # round per-story seconds
    for v in per_story_stats.values():
        v["secs"] = round(v.get("secs", 0.0), 1)
    _total_secs = round(time.time() - _titles_start, 1)
    cb("done", {"summary": f"{total_created} created · {errors} failed",
                "created": total_created, "errors": errors,
                "stories_done": stories_done, "total_stories": total_stories,
                "per_story": list(per_story_stats.values()),
                "total_secs": _total_secs})


def run_steps(project, plan_id, story_ids, cb, should_stop=lambda: False,
              existing_mode="skip", dedupe_existing=True):
    """existing_mode: 'skip' or 'evaluate'. dedupe_existing: remove pre-existing
    duplicate test cases in each suite before processing."""
    wit, test = connect_azure_sdk(project)
    cb("log", {"msg": "Discovering suites for stories…", "tone": "dim"})
    story_suite_map = discover_suites_for_stories(project, plan_id, set(story_ids))
    stories = fetch_stories(story_ids)
    story_ctx = {}
    for s in stories:
        story_ctx[s.id] = {
            "title": s.fields.get("System.Title", "No Title"),
            "criteria": s.fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""),
            "screenshots": fetch_story_screenshots(s),
        }
    # Log each story → suite mapping up front
    for sid in story_ids:
        suite_id = story_suite_map.get(sid)
        title = story_ctx.get(sid, {}).get("title", "")
        if suite_id:
            cb("log", {"msg": f"Story {sid} → suite {suite_id} · {title}",
                       "tone": "story", "ico": "▸", "ar": bool(title)})
        else:
            cb("log", {"msg": f"Story {sid} — no suite found/created, skipped",
                       "tone": "warn", "ico": "⚠"})
    # ── Remove pre-existing duplicate test cases in each suite ──
    # Catches duplicates already sitting in Azure (from prior runs or manual
    # entry), keeping the most complete one of each group and deleting the rest.
    if dedupe_existing:
        cb("log", {"msg": "Checking suites for duplicate test cases…", "tone": "dim"})
        for sid, suite_id in story_suite_map.items():
            if should_stop(): break
            if suite_id:
                try:
                    dedupe_existing_suite(project, plan_id, suite_id, cb=cb, do_delete=True)
                except Exception as de:
                    cb("log", {"msg": f"Dedup skipped for suite {suite_id}: {str(de)[:80]}",
                               "tone": "warn"})
    # ── Seed titles for empty suites ──
    # If a story's suite has NO test cases yet, generate titles first (same dedup
    # as the titles tool) and create the test cases, so the steps run can proceed
    # on a fresh plan. Suites that already have test cases are left untouched —
    # those go through the normal Skip/Evaluate path below.
    seeded_total = 0
    for sid, suite_id in story_suite_map.items():
        if should_stop(): break
        if not suite_id:
            continue
        # current test cases in this suite
        existing_titles = []
        try:
            for it in fetch_test_cases_for_suite(project, plan_id, suite_id):
                nm = (it.get("workItem", {}) or {}).get("name", "").strip()
                if nm: existing_titles.append(nm)
        except Exception:
            pass
        if existing_titles:
            continue  # suite already populated → handled by Skip/Evaluate later

        ctx = story_ctx.get(sid, {})
        s_title = ctx.get("title", "")
        s_criteria = ctx.get("criteria", "")
        cb("log", {"msg": f"Suite {suite_id} is empty — generating test case titles…",
                   "tone": "dim", "ico": "▸"})
        try:
            titles = generate_titles(s_title, s_criteria, [],
                                     log=lambda m, t="warn": cb("log", {"msg": m, "tone": t}))
        except CreditBalanceError:
            cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit"}); return
        except Exception as e:
            cb("log", {"msg": f"Title generation failed for story {sid}: {e}", "tone": "err"})
            continue

        # de-duplicate (exact + semantic), same as the titles tool
        seen_norm = set(); seen_keys = set(); unique = []; dropped = 0
        for t in titles:
            nk = _norm_title(t); sk = _semantic_key(t)
            if nk in seen_norm or _is_near_duplicate(sk, seen_keys):
                dropped += 1; continue
            unique.append(t); seen_norm.add(nk); seen_keys.add(sk)
        if dropped:
            cb("log", {"msg": f"Skipped {dropped} duplicate/near-duplicate title"
                              + ("s" if dropped > 1 else ""), "tone": "dim", "ico": "⏭"})
        for tc_title in unique:
            if should_stop(): break
            try:
                new_id = create_test_case(project, plan_id, suite_id, tc_title, sid)
                seeded_total += 1
                cb("log", {"msg": tc_title + " — test case created", "tone": "ok",
                           "id": new_id, "ar": True})
            except Exception as e:
                cb("log", {"msg": f"{tc_title} — {e}", "tone": "err", "ar": True})
    if seeded_total:
        cb("log", {"msg": f"Created {seeded_total} new test case"
                          + ("s" if seeded_total > 1 else "")
                          + " — now generating steps…", "tone": "ok", "ico": "✓"})

    # Build flat test-case list (now includes any freshly-seeded cases)
    suite_test_cases = []
    for sid, suite_id in story_suite_map.items():
        try:
            for tc in fetch_test_cases_for_suite(project, plan_id, suite_id):
                suite_test_cases.append((tc, sid, suite_id))
        except Exception:
            pass

    total = len(suite_test_cases)
    # Count stories that actually have test cases to process
    _stories_with_tc = set(sid for _, sid, _ in suite_test_cases)
    total_stories = len(_stories_with_tc) if _stories_with_tc else len(story_suite_map)
    done = ok = err = skipped = stories_done = 0
    action_items = []
    skipped_items = []
    from collections import Counter as _C
    ok_by_story = _C(); skip_by_story = _C(); err_by_story = _C()
    time_by_story = {}        # {sid: cumulative seconds}
    _run_start = time.time()
    from collections import Counter
    remaining = Counter(sid for _, sid, _ in suite_test_cases)
    story_total = Counter(sid for _, sid, _ in suite_test_cases)
    # Per-story progress: {sid: {"total","done","ok","skipped","err","title","suite"}}
    story_prog = {}
    for sid in story_total:
        title = story_ctx.get(sid, {}).get("title", "")
        suite = story_suite_map.get(sid)
        story_prog[sid] = {"total": story_total[sid], "done": 0, "ok": 0,
                           "skipped": 0, "err": 0, "title": title, "suite": suite}
        cb("story_progress", {"id": sid, **story_prog[sid]})

    cb("stat", {"total": total, "stories_done": 0, "total_stories": total_stories,
                "done": 0, "skipped": 0, "errors": 0, "created": seeded_total})

    for tc, story_id, suite_id in suite_test_cases:
        if should_stop(): break
        _tc_start = time.time()
        wi = tc.get("workItem", {})
        tc_id = wi.get("id"); tc_title = wi.get("name", "No Title")
        ctx = story_ctx.get(story_id, {})
        criteria = ctx.get("criteria", "")

        # Live progress the instant this case starts (so the bar leaves "Starting…"
        # and the active story flips to "Running" immediately, not only when it ends).
        _start_pct = int(done / total * 100) if total else 0
        cb("progress", {"pct": _start_pct, "label": f"{_start_pct}% · {done} of {total}"})
        _sp = story_prog.get(story_id)
        if _sp is not None and _sp.get("done", 0) == 0:
            # mark the story active (done stays 0 but emit so the card shows Running)
            cb("story_progress", {"id": story_id, **_sp, "_active": True})

        # existing steps?
        existing_xml = ""
        try:
            ex = wit.get_work_item(tc_id, fields=["Microsoft.VSTS.TCM.Steps"])
            existing_xml = (ex.fields or {}).get("Microsoft.VSTS.TCM.Steps", "") or ""
        except Exception:
            pass
        has_existing = bool(existing_xml and "<step " in existing_xml)

        if has_existing and existing_mode == "skip":
            skipped += 1; done += 1; skip_by_story[story_id] += 1
            _el = round(time.time() - _tc_start, 1)
            skipped_items.append({"id": tc_id, "title": tc_title,
                                  "reason": "Already had steps", "secs": _el})
            cb("log", {"msg": tc_title + " — already has steps, skipped", "tone": "skip",
                       "id": tc_id, "ico": "⏭", "ar": True, "secs": _el,
                       "detail": f"skipped · ⏱ {_fmt_mmss(_el)}"})
        else:
            inadequate_reason = ""
            proceed = True
            if has_existing and existing_mode == "evaluate":
                try:
                    verdict = evaluate_existing_steps(tc_title, criteria, existing_xml)
                except CreditBalanceError:
                    cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit"}); return
                except Exception:
                    verdict = {"adequate": False, "reason": "تعذر التقييم"}
                if verdict.get("adequate"):
                    skipped += 1; done += 1; proceed = False; skip_by_story[story_id] += 1
                    _el = round(time.time() - _tc_start, 1)
                    skipped_items.append({"id": tc_id, "title": tc_title,
                                          "reason": verdict.get("reason","Existing steps adequate"),
                                          "secs": _el})
                    cb("log", {"msg": tc_title + " — existing steps adequate", "tone": "ok",
                               "id": tc_id, "ar": True, "secs": _el,
                               "detail": f"existing steps adequate · ⏱ {_fmt_mmss(_el)}"})
                else:
                    inadequate_reason = verdict.get("reason", "")
            if proceed:
                # UI description cached once per story
                if "ui_desc" not in ctx:
                    if ctx.get("screenshots"):
                        cb("log", {"msg": f"read {len(ctx['screenshots'])} screenshot(s) once — UI described", "tone": "dim", "ico": "👁", "indent": True})
                    try:
                        ctx["ui_desc"] = describe_story_ui(ctx.get("screenshots"), ctx.get("title",""))
                    except CreditBalanceError:
                        cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit"}); return
                    story_ctx[story_id] = ctx
                cb("log", {"msg": tc_title + " — generating…", "tone": "info",
                           "id": tc_id, "ar": True, "wip": True, "wip_id": tc_id})
                try:
                    steps = generate_steps(tc_title, criteria, ctx.get("ui_desc",""),
                                           log=lambda m,t="warn": cb("log",{"msg":m,"tone":t}))
                    update_test_case_with_steps(tc_id, build_steps_xml(steps), project, story_id)
                    ok += 1; done += 1; ok_by_story[story_id] += 1
                    npre = sum(1 for s in steps if s.get("precondition","").strip())
                    _elapsed = time.time() - _tc_start
                    cb("log", {"msg": tc_title, "tone": "ok", "id": tc_id, "ar": True,
                               "replace_wip": tc_id,
                               "secs": round(_elapsed, 1),
                               "detail": f"{len(steps)} steps · pre {npre} · action {len(steps)} · "
                                         f"expected {len(steps)} · ⏱ {_fmt_mmss(_elapsed)}"})
                    if inadequate_reason:
                        action_items.append({"id": tc_id, "title": tc_title,
                                             "reason": inadequate_reason,
                                             "secs": round(_elapsed, 1)})
                except CreditBalanceError:
                    cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit",
                                "action_items": action_items}); return
                except Exception as e:
                    cat, friendly = classify_ai_error(e)
                    # A provider/config error (bad key, wrong/unknown model) hits
                    # EVERY case identically — stop now with one clear message
                    # instead of failing the whole suite one case at a time.
                    if cat in ("auth", "bad_model", "not_found"):
                        cb("log", {"msg": friendly, "tone": "err"})
                        cb("done", {"summary": f"Stopped — {friendly}", "reason": cat,
                                    "action_items": action_items}); return
                    err += 1; done += 1; err_by_story[story_id] += 1
                    cb("log", {"msg": tc_title + f" — {friendly}", "tone": "err", "id": tc_id,
                               "ar": True, "replace_wip": tc_id})

        # update per-story progress snapshot
        time_by_story[story_id] = time_by_story.get(story_id, 0.0) + (time.time() - _tc_start)
        sp = story_prog.get(story_id)
        if sp is not None:
            sp["done"] = sp["total"] - (remaining[story_id] - 1)
            sp["ok"] = ok_by_story.get(story_id, 0)
            sp["skipped"] = skip_by_story.get(story_id, 0)
            sp["err"] = err_by_story.get(story_id, 0)
            cb("story_progress", {"id": story_id, **sp})

        remaining[story_id] -= 1
        if remaining[story_id] == 0:
            stories_done += 1
            cb("log", {"msg": f"Story {story_id} completed · all test cases processed", "tone": "ok", "ico": "└"})
        pct = int(done/total*100) if total else 0
        cb("stat", {"total": total, "stories_done": stories_done, "total_stories": total_stories,
                    "done": ok, "skipped": skipped, "errors": err, "created": seeded_total})
        cb("progress", {"pct": pct, "label": f"{pct}% · {done} of {total}"})

    per_story = []
    for sid, sp in story_prog.items():
        per_story.append({"id": sid, "title": sp["title"], "suite": sp["suite"],
                          "total": sp["total"], "ok": ok_by_story.get(sid, 0),
                          "skipped": skip_by_story.get(sid, 0), "err": err_by_story.get(sid, 0),
                          "secs": round(time_by_story.get(sid, 0.0), 1)})
    _total_secs = round(time.time() - _run_start, 1)
    cb("done", {"summary": f"{ok} updated · {skipped} skipped · {err} failed",
                "updated": ok, "skipped": skipped, "errors": err,
                "created": seeded_total,
                "stories_done": stories_done, "total_stories": total_stories,
                "action_items": action_items, "skipped_items": skipped_items,
                "per_story": per_story, "total_secs": _total_secs})


def validate_stories_in_plan(project, plan_id, story_ids):
    """Read-only check: returns (found, missing) story-id lists for the plan.
    A story is 'found' if it maps to a requirement suite already in the plan."""
    smap = discover_suites_for_stories(project, plan_id, set(story_ids), create_missing=False)
    found = [sid for sid in story_ids if sid in smap]
    missing = [sid for sid in story_ids if sid not in smap]
    return found, missing


def count_test_cases(project, plan_id, story_ids):
    """Real number of existing test cases across the given stories (parallel).
    Used for the live estimate so it shows a true count, not a guess."""
    import concurrent.futures as _cf
    try:
        smap = discover_suites_for_stories(project, plan_id, set(story_ids),
                                           create_missing=False)
    except Exception:
        return 0
    suites = [sid for sid in smap.values() if sid]
    if not suites:
        return 0
    def _one(suite_id):
        try:
            return len(fetch_test_cases_for_suite(project, plan_id, suite_id))
        except Exception:
            return 0
    with _cf.ThreadPoolExecutor(max_workers=min(16, len(suites))) as _ex:
        return sum(_ex.map(_one, suites))


def count_existing_steps(project, plan_id, story_ids):
    """Count test cases that already have steps (for the existing-steps modal)."""
    import concurrent.futures as _cf
    wit, test = connect_azure_sdk(project)
    smap = discover_suites_for_stories(project, plan_id, set(story_ids), create_missing=False)
    # Fetch each suite's test cases CONCURRENTLY (was a serial loop per suite).
    suites = [s for s in smap.values() if s]
    ids = []
    if suites:
        def _fetch_ids(suite_id):
            out = []
            try:
                for tc in fetch_test_cases_for_suite(project, plan_id, suite_id):
                    wid = tc.get("workItem", {}).get("id")
                    if wid:
                        out.append(wid)
            except Exception:
                pass
            return out
        with _cf.ThreadPoolExecutor(max_workers=min(16, len(suites))) as _ex:
            for _lst in _ex.map(_fetch_ids, suites):
                ids.extend(_lst)
    have = 0
    for i in range(0, len(ids), 200):
        try:
            for w in wit.get_work_items(ids[i:i+200], fields=["Microsoft.VSTS.TCM.Steps"]):
                sf = (w.fields or {}).get("Microsoft.VSTS.TCM.Steps", "") or ""
                if sf and "<step " in sf: have += 1
        except Exception:
            pass
    return have, len(ids)


# ═══════════════════════════════════════════════════════════════════════════════
#  EMAIL REPORT
# ═══════════════════════════════════════════════════════════════════════════════
def send_report(to_addrs, subject, html_body, attachments=None):
    """Send an HTML email via Gmail SMTP, with optional file attachments.
    Returns (ok, error_msg)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage
    from email.mime.application import MIMEApplication
    if not GMAIL_APP_PASS or not to_addrs:
        return False, "No Gmail password or recipients configured."
    # multipart/related so the HTML can reference the logo as cid:qastudio-logo
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = ", ".join(to_addrs) if isinstance(to_addrs, list) else to_addrs
    _alt = MIMEMultipart("alternative")
    _alt.attach(MIMEText(html_body, "html"))
    msg.attach(_alt)
    # file attachments (e.g. the regression plan as Word/Excel/PDF)
    for _path in (attachments or []):
        try:
            with open(_path, "rb") as _af:
                _part = MIMEApplication(_af.read())
            _part.add_header("Content-Disposition", "attachment",
                             filename=os.path.basename(_path))
            msg.attach(_part)
        except Exception:
            pass
    # inline brand logo (safe no-op if the file is missing)
    try:
        _lp = _logo_path()
        if _lp:
            with open(_lp, "rb") as _f:
                _img = MIMEImage(_f.read())
            _img.add_header("Content-ID", f"<{LOGO_CID}>")
            _img.add_header("Content-Disposition", "inline", filename="qa-logo.png")
            msg.attach(_img)
    except Exception:
        pass
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(GMAIL_SENDER, GMAIL_APP_PASS)
            s.send_message(msg)
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rejected the App Password. Generate a new 16-character "
                       "App Password (2-Step Verification must be on) and save it.")
    except smtplib.SMTPRecipientsRefused:
        return False, "Recipient address was refused — check the report email addresses."
    except smtplib.SMTPSenderRefused:
        return False, f"Sender {GMAIL_SENDER} was refused by Gmail."
    except smtplib.SMTPException as e:
        m = str(e).lower()
        if "username and password not accepted" in m or "badcredentials" in m or "535" in m:
            return False, ("Gmail rejected the App Password. Generate a new 16-character "
                           "App Password (2-Step Verification must be on) and save it.")
        return False, f"Email failed to send ({type(e).__name__})."
    except Exception as e:
        m = str(e).lower()
        if "timed out" in m or "timeout" in m or "connection" in m or "ssl" in m:
            return False, "Could not reach Gmail SMTP — check your network/firewall (port 465)."
        return False, "Email failed to send."


def _fmt_secs(s):
    """Human-friendly duration: 45s, 1m 20s, 2m."""
    try:
        s = float(s)
    except Exception:
        return ""
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60); sec = int(round(s - m * 60))
    return f"{m}m {sec}s" if sec else f"{m}m"


def _fmt_mmss(s):
    """Duration as mm:ss (e.g. 0:45, 1:57, 12:03)."""
    try:
        s = float(s)
    except Exception:
        return ""
    m = int(s // 60); sec = int(round(s - m * 60))
    if sec == 60:
        m += 1; sec = 0
    return f"{m}:{sec:02d}"


# ---- brand logo, embedded inline in emails via Content-ID ----
LOGO_CID = "qastudio-logo"

def _logo_path():
    """Path to the inline email logo (transparent Q mark), next to this file."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return ""
    for name in ("qa-logo.png", "app.png"):
        p = os.path.join(here, name)
        if os.path.exists(p):
            return p
    return ""

def _logo_tag(size=34):
    """<img> referencing the CID-embedded logo; degrades to alt text if blocked."""
    return (f"<img src='cid:{LOGO_CID}' width='{size}' height='{size}' alt='QA Studio' "
            f"style='display:block;border:0;outline:none;text-decoration:none' />")


def build_report_email(tool, summary, stats, action_items=None, skipped_items=None,
                       per_story=None, plan_url=None, total_secs=None, log_lines=None,
                       org=None, project=None):
    """Restrained, email-safe (table + inline-style) HTML run report.
    Renders consistently across Outlook / Gmail / Apple Mail; web fonts fall
    back to system fonts. Drives off the same data the in-app report uses."""
    import datetime as _dt

    # ---- palette ----
    PAPER="#E9E8EE"; CARD="#FFFFFF"; TINT="#FAFAFC"
    INK="#1B1A22"; INK2="#6B6975"; INK3="#9C9AA6"
    LINE="#E8E7EE"; LINE2="#F1F0F5"
    VIOLET="#3A57D6"; VIOLET_INK="#2940C2"; VIOLET_SOFT="#E7ECFF"
    GREEN="#1F8A52"; GREEN_SOFT="#E7F4ED"
    RED="#D6414A"; RED_SOFT="#FBEAEC"
    AMBER="#AB780C"; AMBER_SOFT="#F7EFD8"
    UI='"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
    MONO='"SFMono-Regular",Consolas,Menlo,monospace'
    AR='"Segoe UI","Tahoma",Arial,sans-serif'

    def _wi_url(item_id):
        if not (org and project and item_id):
            return ""
        return f"https://dev.azure.com/{org}/{project}/_workitems/edit/{item_id}"

    def _intval(v):
        try:
            return int(str(v).split('/')[0].strip())
        except Exception:
            return 0

    def _is_ar(s):
        return any('\u0600' <= c <= '\u06ff' for c in str(s))

    stats = stats or {}
    is_steps = "step" in str(tool).lower()
    review_n = len(action_items or [])
    failed_n = _intval(stats.get("Failed", 0))
    stopped = "stop" in str(summary).lower()

    # ---- status + accent ----
    if failed_n > 0:
        pill_fg, pill_bg, pill_txt = RED, RED_SOFT, "Completed with errors"; accent = RED
    elif stopped:
        pill_fg, pill_bg, pill_txt = AMBER, AMBER_SOFT, "Stopped early"; accent = AMBER
    else:
        pill_fg, pill_bg, pill_txt = GREEN, GREEN_SOFT, "Completed"; accent = VIOLET
    check_ic = "&#10003;" if pill_fg != AMBER else "&#9632;"
    status_pill = (f"<span style='display:inline-block;background:{pill_bg};color:{pill_fg};"
                   f"font-size:11px;font-weight:700;letter-spacing:.4px;padding:5px 12px;"
                   f"border-radius:20px'>{check_ic}&nbsp; {pill_txt.upper()}</span>")

    # ---- headline ----
    if is_steps:
        n = _intval(stats.get("Updated", 0))
        headline = (f"<b style='color:{VIOLET_INK}'>{n} test case" + ("s" if n != 1 else "") + "</b> updated") if n else "No test cases updated"
    else:
        n = _intval(stats.get("Created", 0))
        headline = (f"<b style='color:{VIOLET_INK}'>{n} title" + ("s" if n != 1 else "") + "</b> created") if n else "No new titles created"
    sub = _html.escape(str(summary or ""))

    hero = (f"{status_pill}"
            f"<div style='font-size:25px;font-weight:700;letter-spacing:-.5px;color:{INK};"
            f"line-height:1.15;margin:14px 0 0'>{headline}</div>"
            f"<div style='font-size:13px;color:{INK2};font-weight:600;margin-top:8px'>{sub}</div>")

    # ---- masthead ----
    today = _dt.date.today().strftime("%d %b %Y")
    kind = "Test Case Steps" if is_steps else "Test Case Titles"
    masthead = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        f"<td width='34' valign='middle' style='padding-right:13px'>{_logo_tag(34)}</td>"
        f"<td valign='middle'>"
        f"<div style='font-size:15px;font-weight:700;color:{INK};letter-spacing:-.2px'>QA Studio</div>"
        f"<div style='font-size:12px;font-weight:700;color:{VIOLET_INK};margin-top:2px'>{kind} &middot; Run report</div>"
        f"</td>"
        f"<td valign='middle' align='right' style='font-family:{MONO};font-size:11px;"
        f"color:{INK3};font-weight:700'>{today}</td>"
        f"</tr></table>")

    # ---- metric strip ----
    items = list(stats.items())
    if is_steps:
        merged = []
        for k, v in items:
            merged.append((k, v))
            if k == "Updated":
                merged.append(("Review", review_n))
        items = merged

    def _mcolor(k, v):
        iv = _intval(v)
        if k in ("Updated", "Created") and iv > 0: return GREEN
        if k == "Review" and iv > 0: return AMBER
        if k == "Failed" and iv > 0: return RED
        if k in ("Time", "Stories"): return INK
        return INK3
    mcells = ""
    for i, (k, v) in enumerate(items):
        col = _mcolor(k, v)
        bl = "" if i == 0 else f"border-left:1px solid {LINE2};"
        vsize = "18px" if k == "Time" else "24px"
        mcells += (f"<td width='1' style='{bl}padding:13px 6px 14px;text-align:center;vertical-align:top'>"
                   f"<div style='font-size:9.5px;font-weight:700;letter-spacing:1px;color:{INK3};"
                   f"text-transform:uppercase'>{_html.escape(str(k))}</div>"
                   f"<div style='font-family:{MONO};font-size:{vsize};font-weight:700;color:{col};"
                   f"margin-top:6px;line-height:1'>{_html.escape(str(v))}</div></td>")
    metrics = (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
               f"style='border:1px solid {LINE};border-radius:12px;table-layout:fixed'>"
               f"<tr>{mcells}</tr></table>")

    # ---- cta ----
    cta_row = ""
    if plan_url:
        safe_url = _html.escape(str(plan_url), quote=True)
        cta_row = (f"<tr><td style='padding:20px 32px 0'>"
                   f"<a href='{safe_url}' style='display:inline-block;background:{VIOLET};color:#fff;"
                   f"text-decoration:none;font-size:13px;font-weight:700;padding:12px 22px;"
                   f"border-radius:11px'>Open test plan in Azure DevOps &rarr;</a></td></tr>")

    # ---- section heading helper ----
    def _sec_head(dot, title, count, desc=""):
        d = (f"<div style='font-size:12.5px;color:{INK2};font-weight:600;margin-top:7px;"
             f"line-height:1.5'>{desc}</div>") if desc else ""
        return (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                f"<td valign='middle' style='padding-right:10px'>"
                f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
                f"background:{dot}'></span></td>"
                f"<td valign='middle' style='font-size:14.5px;font-weight:700;color:{INK};"
                f"letter-spacing:-.2px'>{title}</td>"
                f"<td valign='middle' style='padding-left:9px'><span style='font-family:{MONO};"
                f"font-size:11px;font-weight:700;color:{INK2};background:{LINE2};border-radius:20px;"
                f"padding:3px 9px'>{count}</span></td>"
                f"</tr></table>{d}")

    # ---- case card (review / skipped) ----
    def _case_card(a, rail, tag_fg, tag_bg, label, bg=CARD):
        title = _html.escape(str(a.get("title", "")))
        reason = _html.escape(str(a.get("reason", "")))
        item_id = _html.escape(str(a.get("id", "")))
        rtl = "direction:rtl;text-align:right;" if _is_ar(a.get("title", "")) else ""
        url = _wi_url(a.get("id"))
        open_link = (f"<a href='{_html.escape(url, quote=True)}' style='color:{VIOLET_INK};"
                     f"font-size:11.5px;font-weight:700;text-decoration:none'>Open &rarr;</a>") if url else ""
        return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                f"style='margin-top:11px;background:{bg};border:1px solid {LINE};"
                f"border-left:3px solid {rail};border-radius:11px'><tr>"
                f"<td style='padding:13px 16px'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td valign='middle'><span style='display:inline-block;background:{tag_bg};color:{tag_fg};"
                f"font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;"
                f"padding:3px 9px;border-radius:6px'>{label}</span> "
                f"<span style='font-family:{MONO};font-size:11.5px;font-weight:700;color:{INK3}'>#{item_id}</span></td>"
                f"<td valign='middle' align='right'>{open_link}</td>"
                f"</tr></table>"
                f"<div style='font-family:{AR};font-size:14px;font-weight:600;color:{INK};"
                f"margin-top:11px;line-height:1.5;{rtl}'>{title}</div>"
                + (f"<div style='font-family:{AR};font-size:12.5px;color:{INK2};margin-top:6px;"
                   f"line-height:1.7;{rtl}'>{reason}</div>" if reason else "")
                + f"</td></tr></table>")

    sections = ""

    # per-story (top of the report)
    if per_story:
        rows = ""
        for sp in per_story:
            sid = _html.escape(str(sp.get("id", "")))
            title = _html.escape(str(sp.get("title", "")))
            total = int(sp.get("total", 0) or 0)
            ok = int(sp.get("ok", 0) or 0); sk = int(sp.get("skipped", 0) or 0); er = int(sp.get("err", 0) or 0)
            secs = sp.get("secs", None)
            rtl = "direction:rtl;text-align:right;" if _is_ar(sp.get("title", "")) else ""
            chips = ""
            if ok: chips += (f"<span style='display:inline-block;background:{GREEN_SOFT};color:{GREEN};"
                             f"font-family:{MONO};font-size:11px;font-weight:700;padding:2px 9px;"
                             f"border-radius:7px;margin-left:5px'>&#10003; {ok}</span>")
            if sk: chips += (f"<span style='display:inline-block;background:{LINE2};color:{INK2};"
                             f"font-family:{MONO};font-size:11px;font-weight:700;padding:2px 9px;"
                             f"border-radius:7px;margin-left:5px'>{sk} skip</span>")
            if er: chips += (f"<span style='display:inline-block;background:{RED_SOFT};color:{RED};"
                             f"font-family:{MONO};font-size:11px;font-weight:700;padding:2px 9px;"
                             f"border-radius:7px;margin-left:5px'>&#10005; {er}</span>")
            tsub = f" &middot; {_fmt_mmss(secs)}" if secs not in (None, "", 0) else ""
            su = _wi_url(sp.get("id"))
            tlink = (f"<a href='{_html.escape(su, quote=True)}' style='color:{INK};"
                     f"text-decoration:none'>{title}</a>" if su else title)
            rows += (f"<tr><td style='padding:13px 0;border-top:1px solid {LINE2};vertical-align:top'>"
                     f"<div style='font-size:14px;font-weight:700;color:{INK};{rtl}'>{tlink}</div>"
                     f"<div style='font-family:{MONO};font-size:11px;font-weight:600;color:{INK3};"
                     f"margin-top:3px'>#{sid} &middot; {total} test case" + ("s" if total != 1 else "") + tsub + "</div></td>"
                     f"<td align='right' style='padding:13px 0;border-top:1px solid {LINE2};"
                     f"vertical-align:top;white-space:nowrap'>{chips}</td></tr>")
        sections += (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                     f"{_sec_head(VIOLET, 'Per-story breakdown', len(per_story))}"
                     f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                     f"style='margin-top:6px'>{rows}</table></td></tr>")

    # needs review
    if action_items:
        cards = "".join(_case_card(a, AMBER, AMBER, AMBER_SOFT, "Review") for a in action_items)
        sections += (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                     f"{_sec_head(AMBER, 'Needs your review', len(action_items), 'Steps that no longer match the story&rsquo;s acceptance criteria were regenerated &mdash; confirm them before the next run.')}"
                     f"{cards}</td></tr>")

    # skipped
    if skipped_items:
        shown = skipped_items[:40]
        cards = "".join(_case_card(a, "#CBC9D4", INK2, LINE2, "Skipped", bg=TINT) for a in shown)
        more = (f"<div style='font-size:12px;color:{INK3};margin-top:10px'>&hellip; and {len(skipped_items)-40} more</div>") if len(skipped_items) > 40 else ""
        sections += (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                     f"{_sec_head('#CBC9D4', 'Skipped', len(skipped_items), 'Existing steps were judged adequate and left untouched. No action needed.')}"
                     f"{cards}{more}</td></tr>")

    # activity log (full, scrollable where supported)
    if log_lines:
        tone_color = {"ok": GREEN, "err": RED, "warn": AMBER, "skip": INK3,
                      "review": AMBER, "story": VIOLET, "dim": INK3, "info": VIOLET_INK}
        default_ico = {"ok": "&#10003;", "err": "&#10005;", "warn": "&#9888;", "skip": "&#9197;",
                       "review": "&#9888;", "story": "&#9656;", "dim": "&middot;", "info": "&bull;"}
        rows = ""
        shown = log_lines[:120]
        for ln in shown:
            tone = ln.get("tone", "dim")
            col = tone_color.get(tone, INK)
            raw_ico = ln.get("ico")
            ico = _html.escape(str(raw_ico)) if raw_ico else default_ico.get(tone, "&middot;")
            msg = _html.escape(str(ln.get("msg", "")))
            item_id = _html.escape(str(ln.get("id", "")))
            detail = _html.escape(str(ln.get("detail", "")))
            is_ar = bool(ln.get("ar")) or _is_ar(ln.get("msg", ""))
            is_story = (tone == "story")
            indent = "padding-left:30px;" if ln.get("indent") else ""
            u = _wi_url(ln.get("id"))
            tdir = "rtl" if is_ar else "ltr"; talign = "right" if is_ar else "left"
            fam = AR if is_ar else UI
            id_chip = (f"<span style='font-family:{MONO};font-size:10.5px;font-weight:700;"
                       f"color:{INK3};background:#EEEDF3;border-radius:5px;padding:1px 6px'>{item_id}</span> ") if item_id else ""
            title_color = col if is_story else INK
            mlink = (f"<a href='{_html.escape(u, quote=True)}' style='color:{title_color};"
                     f"text-decoration:none'>{msg}</a>" if u else msg)
            detail_html = (f"<div style='font-family:{MONO};font-size:10.5px;font-weight:600;"
                           f"color:{INK3};margin-top:3px'>{detail}</div>") if detail else ""
            bg = "#F4F3F8" if tone in ("info", "story") and not is_ar else CARD
            rows += (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                     f"style='border-top:1px solid {LINE2};{indent}background:{bg}'><tr>"
                     f"<td width='18' valign='top' style='padding:8px 0 8px 0;color:{col};"
                     f"font-family:{MONO};font-size:13px;font-weight:700;text-align:center'>{ico}</td>"
                     f"<td valign='top' style='padding:8px 0 8px 9px;direction:{tdir};text-align:{talign}'>"
                     f"<div style='font-family:{fam};font-size:12.5px;font-weight:600;color:{title_color};"
                     f"line-height:1.5'>{id_chip}{mlink}</div>{detail_html}</td></tr></table>")
        more = (f"<div style='padding:11px 15px;border-top:1px solid {LINE};background:#F4F3F8;"
                f"text-align:center;font-size:11.5px;font-weight:600;color:{INK2}'>"
                f"&hellip; and {len(log_lines)-120} more lines &middot; open the full trace in QA Studio</div>") if len(log_lines) > 120 else ""
        if is_steps:
            legend = (f"<span style='color:{GREEN};font-weight:700'>&#9632;</span> updated &nbsp; "
                      f"<span style='color:{AMBER};font-weight:700'>&#9632;</span> review &nbsp; "
                      f"<span style='color:{INK3};font-weight:700'>&#9632;</span> kept")
        else:
            legend = (f"<span style='color:{GREEN};font-weight:700'>&#9632;</span> created &nbsp; "
                      f"<span style='color:{AMBER};font-weight:700'>&#9632;</span> removed &nbsp; "
                      f"<span style='color:{INK3};font-weight:700'>&#9632;</span> skipped")
        toolbar = (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                   f"style='background:#F4F3F8;border-bottom:1px solid {LINE}'><tr>"
                   f"<td style='padding:9px 15px;font-family:{MONO};font-size:10.5px;font-weight:700;color:{INK2}'>"
                   f"{len(log_lines)} lines &middot; full trace</td>"
                   f"<td align='right' style='padding:9px 15px;font-family:{UI};font-size:10px;"
                   f"font-weight:700;color:{INK3}'>{legend}</td></tr></table>")
        # No inner-scroll container: mobile/desktop Outlook can't scroll a nested
        # div, which would clip the log. Render the lines expanded (capped above)
        # so every client shows the full trace.
        log_box = (f"<div style='border:1px solid {LINE};border-radius:12px;overflow:hidden;background:{TINT}'>"
                   f"{toolbar}"
                   f"{rows}"
                   f"{more}</div>")
        sections += (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                     f"{_sec_head(INK, 'Run activity log', str(len(log_lines)) + ' lines')}"
                     f"<div style='margin-top:14px'>{log_box}</div></td></tr>")

    footer = (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
              f"<td valign='middle' style='padding-right:9px'>{_logo_tag(20)}</td>"
              f"<td valign='middle' style='font-size:11.5px;font-weight:600;color:{INK3}'>"
              f"Generated by QA Studio &middot; Azure DevOps + AI</td></tr></table>"
              + (f"<div style='font-family:{MONO};font-size:11px;color:{INK3};margin-top:8px;line-height:1.6'>"
                 f"Org: {_html.escape(str(org))} &middot; Project: {_html.escape(str(project))}</div>" if (org and project) else ""))

    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'></head>
<body style='margin:0;padding:0;background:{PAPER};-webkit-text-size-adjust:100%'>
<center style='width:100%;background:{PAPER}'>
<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:{PAPER}'><tr>
<td align='center' style='padding:26px 12px 48px'>
<table role='presentation' width='640' cellpadding='0' cellspacing='0' style='width:640px;max-width:640px;background:{CARD};border:1px solid #DEDDE6;border-radius:16px;overflow:hidden;font-family:{UI};color:{INK}'>
  <tr><td style='height:3px;line-height:3px;font-size:0;background:{accent}'>&nbsp;</td></tr>
  <tr><td style='padding:24px 32px 0'>{masthead}</td></tr>
  <tr><td style='padding:18px 32px 4px'>{hero}</td></tr>
  <tr><td style='padding:18px 32px 0'>{metrics}</td></tr>
  {cta_row}
  {sections}
  <tr><td style='padding:20px 32px 26px;border-top:1px solid {LINE};background:{TINT}'>{footer}</td></tr>
</table>
</td></tr></table></center></body></html>"""

def build_sprint_summary_email(data):
    """Restrained, email-safe (table + inline-style) Sprint Summary email.
    Matches the run-report design; logo is embedded inline via Content-ID."""
    import datetime as _dt

    PAPER="#E9E8EE"; CARD="#FFFFFF"; TINT="#FAFAFC"
    INK="#1B1A22"; INK2="#6B6975"; INK3="#9C9AA6"
    LINE="#E8E7EE"; LINE2="#F1F0F5"
    VIOLET="#3A57D6"; VIOLET_INK="#2940C2"; VIOLET_SOFT="#E7ECFF"
    GREEN="#1F8A52"; GREEN_SOFT="#E7F4ED"
    RED="#D6414A"; AMBER="#AB780C"; AMBER_SOFT="#F7EFD8"
    UI='"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
    MONO='"SFMono-Regular",Consolas,Menlo,monospace'
    AR='"Segoe UI","Tahoma",Arial,sans-serif'

    plan_name = _html.escape(str(data.get("plan_name", "")))
    iteration = _html.escape(str(data.get("iteration", "") or "—"))
    total_stories = data.get("total_stories", 0)
    total_tc = data.get("total_test_cases", 0)
    by_state = data.get("by_state", {})
    stories = data.get("stories", [])
    _proj = data.get("project", "")
    _org = data.get("org", AZURE_ORG)

    def _is_ar(s):
        return any('\u0600' <= c <= '\u06ff' for c in str(s))

    def _state_colors(state):
        s = (state or "").lower()
        if s in ("done", "closed", "completed", "resolved"): return (GREEN, GREEN_SOFT)
        if s in ("active", "in progress", "committed", "doing"): return (VIOLET_INK, VIOLET_SOFT)
        if s in ("new", "to do", "proposed", "open"): return (AMBER, AMBER_SOFT)
        return (INK2, LINE2)

    # masthead
    today = _dt.date.today().strftime("%d %b %Y")
    masthead = (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        f"<td width='34' valign='middle' style='padding-right:13px'>{_logo_tag(34)}</td>"
        f"<td valign='middle'>"
        f"<div style='font-size:15px;font-weight:700;color:{INK};letter-spacing:-.2px'>QA Studio</div>"
        f"<div style='font-size:12px;font-weight:700;color:{VIOLET_INK};margin-top:2px'>Sprint Summary &middot; Report</div>"
        f"</td>"
        f"<td valign='middle' align='right' style='font-family:{MONO};font-size:11px;color:{INK3};font-weight:700'>{today}</td>"
        f"</tr></table>")

    # hero
    hero = (f"<span style='display:inline-block;background:{VIOLET_SOFT};color:{VIOLET_INK};"
            f"font-size:11px;font-weight:700;letter-spacing:.4px;padding:5px 12px;border-radius:20px'>SPRINT SNAPSHOT</span>"
            f"<div style='font-size:23px;font-weight:700;letter-spacing:-.5px;color:{INK};line-height:1.2;margin:14px 0 0'>{plan_name}</div>"
            f"<div style='font-family:{MONO};font-size:12px;color:{INK2};font-weight:600;margin-top:6px'>{iteration}</div>")

    # metric strip
    metrics_data = [("Stories", total_stories, VIOLET_INK), ("Test Cases", total_tc, GREEN), ("Statuses", len(by_state), INK)]
    mcells = ""
    for i,(k,v,col) in enumerate(metrics_data):
        bl = "" if i == 0 else f"border-left:1px solid {LINE2};"
        mcells += (f"<td width='1' style='{bl}padding:14px 8px 15px;text-align:center'>"
                   f"<div style='font-size:9.5px;font-weight:800;letter-spacing:1px;color:{INK3};text-transform:uppercase'>{_html.escape(str(k))}</div>"
                   f"<div style='font-family:{MONO};font-size:25px;font-weight:700;color:{col};margin-top:6px;line-height:1'>{v}</div></td>")
    metrics = (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
               f"style='border:1px solid {LINE};border-radius:12px;table-layout:fixed'><tr>{mcells}</tr></table>")

    def _sec_head(dot, title, count):
        return (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                f"<td valign='middle' style='padding-right:10px'><span style='display:inline-block;width:9px;height:9px;border-radius:50%;background:{dot}'></span></td>"
                f"<td valign='middle' style='font-size:14.5px;font-weight:800;color:{INK};letter-spacing:-.2px'>{title}</td>"
                f"<td valign='middle' style='padding-left:9px'><span style='font-family:{MONO};font-size:11px;font-weight:800;color:{INK2};background:{LINE2};border-radius:20px;padding:3px 9px'>{count}</span></td>"
                f"</tr></table>")

    # status breakdown — wrapping chips
    chips = ""
    for st, cnt in sorted(by_state.items(), key=lambda x: -x[1]):
        fg, bg = _state_colors(st)
        chips += (f"<div style='display:inline-block;vertical-align:top;background:{bg};border-radius:11px;"
                  f"padding:13px 10px;text-align:center;min-width:92px;margin:0 8px 8px 0;box-sizing:border-box'>"
                  f"<div style='font-family:{MONO};font-size:22px;font-weight:700;color:{fg}'>{cnt}</div>"
                  f"<div style='font-size:11px;color:{INK2};font-weight:700;margin-top:3px;line-height:1.3'>{_html.escape(str(st))}</div></div>")
    status_block = (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                    f"{_sec_head(VIOLET, 'Status breakdown', len(by_state))}"
                    f"<div style='font-size:0;margin-top:14px'>{chips}</div></td></tr>") if by_state else ""

    # stories
    rows = ""
    for s in stories:
        title = _html.escape(str(s.get("title", "")))
        sid = _html.escape(str(s.get("id", "")))
        state = str(s.get("state", ""))
        tc = int(s.get("test_cases", 0) or 0)
        fg, bg = _state_colors(state)
        rtl = "direction:rtl;text-align:right;" if _is_ar(title) else ""
        wi = (f"https://dev.azure.com/{_org}/{_proj}/_workitems/edit/{s.get('id','')}" if _proj and s.get("id") else "")
        tlink = (f"<a href='{_html.escape(wi, quote=True)}' style='color:{INK};text-decoration:none'>{title}</a>" if wi else title)
        idlink = (f"<a href='{_html.escape(wi, quote=True)}' style='color:{VIOLET_INK};text-decoration:none'>#{sid} &rarr;</a>" if wi else f"#{sid}")
        rows += (f"<tr><td style='padding:13px 0;border-top:1px solid {LINE2};vertical-align:middle'>"
                 f"<div style='font-size:13.5px;font-weight:700;color:{INK};{rtl}'>{tlink}</div>"
                 f"<div style='font-family:{MONO};font-size:11px;font-weight:600;color:{INK3};margin-top:3px'>{idlink}</div></td>"
                 f"<td align='right' style='padding:13px 0;border-top:1px solid {LINE2};vertical-align:middle;white-space:nowrap'>"
                 f"<span style='font-family:{MONO};font-size:11px;font-weight:700;color:{INK2};margin-right:8px'>{tc} TC</span>"
                 f"<span style='display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:800;padding:3px 10px;border-radius:20px'>{_html.escape(state)}</span></td>"
                 f"</tr>")
    story_block = (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                   f"{_sec_head(INK, 'Stories', len(stories))}"
                   f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='margin-top:6px'>{rows}</table></td></tr>") if stories else ""

    footer = (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
              f"<td valign='middle' style='padding-right:9px'>{_logo_tag(20)}</td>"
              f"<td valign='middle' style='font-size:11.5px;font-weight:600;color:{INK3}'>Generated by QA Studio &middot; Azure DevOps + AI</td></tr></table>"
              + (f"<div style='font-family:{MONO};font-size:11px;color:{INK3};margin-top:8px'>Org: {_html.escape(str(_org))} &middot; Project: {_html.escape(str(_proj))}</div>" if _proj else ""))

    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'></head>
<body style='margin:0;padding:0;background:{PAPER};-webkit-text-size-adjust:100%'>
<center style='width:100%;background:{PAPER}'>
<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:{PAPER}'><tr>
<td align='center' style='padding:26px 12px 48px'>
<table role='presentation' width='640' cellpadding='0' cellspacing='0' style='width:640px;max-width:640px;background:{CARD};border:1px solid #DEDDE6;border-radius:16px;overflow:hidden;font-family:{UI};color:{INK}'>
  <tr><td style='height:3px;line-height:3px;font-size:0;background:{VIOLET}'>&nbsp;</td></tr>
  <tr><td style='padding:24px 32px 0'>{masthead}</td></tr>
  <tr><td style='padding:18px 32px 4px'>{hero}</td></tr>
  <tr><td style='padding:18px 32px 0'>{metrics}</td></tr>
  {status_block}
  {story_block}
  <tr><td style='padding:20px 32px 26px;border-top:1px solid {LINE};background:{TINT}'>{footer}</td></tr>
</table>
</td></tr></table></center></body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
#  SELENIUM AUTOMATION GENERATION (Selenium + Java + TestNG + POM)
# ═══════════════════════════════════════════════════════════════════════════════
#  Flow:
#   1. scrape_dom()           — Selenium opens the site (logs in first), harvests
#                               every interactive element with robust locators.
#   2. generate_test_class()  — AI writes one TestNG page-object-backed test class
#                               per story (a @Test per test case) using REAL locators.
#   3. build_automation_project() — assembles the full Maven project tree.
#   4. push_to_git()          — commits + pushes the project to a Git repo.
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_class_name(text, fallback="Story"):
    """Turn an arbitrary string into a valid Java class identifier."""
    # transliterate-ish: keep ascii letters/digits, capitalize words
    t = re.sub(r"[^0-9A-Za-z]+", " ", str(text)).strip()
    if not t:
        return fallback
    parts = [p for p in t.split(" ") if p]
    name = "".join(p[:1].upper() + p[1:] for p in parts)
    if name and name[0].isdigit():
        name = fallback + name
    return name or fallback


_HARVEST_JS = r"""
function robustCss(el){
  if(el.id) return '#'+CSS.escape(el.id);
  if(el.name) return el.tagName.toLowerCase()+'[name="'+el.name+'"]';
  let path=[], e=el;
  while(e && e.nodeType===1 && path.length<5){
    let sel=e.tagName.toLowerCase();
    if(e.className && typeof e.className==='string'){
      let c=e.className.trim().split(/\s+/).filter(Boolean).slice(0,2);
      if(c.length) sel+='.'+c.map(x=>CSS.escape(x)).join('.');
    }
    let p=e.parentNode, idx=1, sib=e;
    while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
    sel+=':nth-of-type('+idx+')';
    path.unshift(sel);
    e=e.parentNode;
    if(e && e.id){ path.unshift('#'+CSS.escape(e.id)); break; }
  }
  return path.join(' > ');
}
function xpathOf(el){
  if(el.id) return '//*[@id="'+el.id+'"]';
  let parts=[], e=el;
  while(e && e.nodeType===1){
    let idx=1, sib=e;
    while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
    parts.unshift(e.tagName.toLowerCase()+'['+idx+']');
    e=e.parentNode;
  }
  return '/'+parts.join('/');
}
const sel='input,button,a,select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=switch],[contenteditable=true]';
function anameOf(el){
  // best-effort accessible name: aria-label, associated <label>, title, alt, text
  let n = el.getAttribute('aria-label') || '';
  if(!n && el.id){
    try{ var lab=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
         if(lab) n=(lab.innerText||'').trim(); }catch(e){}
  }
  if(!n){ var pl=el.closest('label'); if(pl) n=(pl.innerText||'').trim(); }
  if(!n) n = el.getAttribute('title') || el.getAttribute('alt') || '';
  return (n||'').trim().slice(0,80);
}
const els=[...document.querySelectorAll(sel)];
return els.slice(0,250).map((el,i)=>({
  idx: i,
  tag: el.tagName.toLowerCase(),
  type: el.getAttribute('type')||'',
  role: el.getAttribute('role')||'',
  id: el.id||'',
  name: el.getAttribute('name')||'',
  testid: el.getAttribute('data-testid')||el.getAttribute('data-test')||el.getAttribute('data-cy')||'',
  text: (el.innerText||el.value||'').trim().slice(0,60),
  placeholder: el.getAttribute('placeholder')||'',
  aria: el.getAttribute('aria-label')||'',
  aname: anameOf(el),
  cls: ((typeof el.className==='string'?el.className:'')+' '+
        (el.querySelector('i,svg,[class*=icon i]')?
          (el.querySelector('i,svg,[class*=icon i]').getAttribute('class')||''):'')).trim().slice(0,120),
  svgicon: (function(){
    var s=el.getAttribute('data-svgicon')||el.getAttribute('data-svg-icon')||
          el.getAttribute('ng-reflect-svg-icon')||el.getAttribute('data-icon')||'';
    if(!s){var d=el.querySelector('[data-svgicon],[data-svg-icon],[ng-reflect-svg-icon],[data-icon]');
           if(d) s=d.getAttribute('data-svgicon')||d.getAttribute('data-svg-icon')||
                   d.getAttribute('ng-reflect-svg-icon')||d.getAttribute('data-icon')||'';}
    return (s||'').trim().slice(0,40);
  })(),
  disabled: !!(el.disabled||el.getAttribute('aria-disabled')==='true'),
  visible: !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
  css: robustCss(el),
  xpath: xpathOf(el)
}));
"""


# Error / validation message nodes — these are usually spans/divs/[role=alert]
# and are invisible to the interactive harvest above. Captured separately so the
# DOM-diff assertion binder (and negative-login error capture) can find them.
_ERROR_HARVEST_JS = r"""
function robustCss(el){
  if(el.id) return '#'+CSS.escape(el.id);
  if(el.name) return el.tagName.toLowerCase()+'[name="'+el.name+'"]';
  let path=[], e=el;
  while(e && e.nodeType===1 && path.length<5){
    let sel=e.tagName.toLowerCase();
    if(e.className && typeof e.className==='string'){
      let c=e.className.trim().split(/\s+/).filter(Boolean).slice(0,2);
      if(c.length) sel+='.'+c.map(x=>CSS.escape(x)).join('.');
    }
    let p=e.parentNode, idx=1, sib=e;
    while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
    sel+=':nth-of-type('+idx+')';
    path.unshift(sel);
    e=e.parentNode;
    if(e && e.id){ path.unshift('#'+CSS.escape(e.id)); break; }
  }
  return path.join(' > ');
}
function xpathOf(el){
  if(el.id) return '//*[@id="'+el.id+'"]';
  let parts=[], e=el;
  while(e && e.nodeType===1){
    let idx=1, sib=e;
    while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
    parts.unshift(e.tagName.toLowerCase()+'['+idx+']');
    e=e.parentNode;
  }
  return '/'+parts.join('/');
}
const sel="[role=alert],[role=status],[aria-live],.alert,.alert-error,.alert-danger,"+
  ".error,.has-error,.invalid-feedback,.help-block,.field-error,.form-error,.toast,"+
  ".kc-feedback-text,.pf-c-form__helper-text,#input-error,.message,.notification,"+
  "[id*=error i],[class*=error i],[class*=invalid i],[class*=feedback i],[class*=danger i],[class*=toast i]";
const out=[]; const seen=new Set();
[...document.querySelectorAll(sel)].forEach(el=>{
  const txt=(el.innerText||el.textContent||'').trim();
  if(!txt) return;
  if(txt.length>220) return;
  const key=robustCss(el);
  if(seen.has(key)) return; seen.add(key);
  out.push({
    tag: el.tagName.toLowerCase(), type:'', role: el.getAttribute('role')||'',
    id: el.id||'', name: el.getAttribute('name')||'', testid:'',
    text: txt.slice(0,120), placeholder:'',
    aria: el.getAttribute('aria-label')||'', aname:'',
    disabled:false,
    visible: !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
    css: key, xpath: xpathOf(el), is_error: true
  });
});
return out.slice(0,80);
"""


def _harvest_dom(driver):
    """Return the list of interactive elements on the current page."""
    try:
        return driver.execute_script("return (function(){" + _HARVEST_JS + "})();") or []
    except Exception:
        return []


def _harvest_errors(driver):
    """Return visible error/validation/notification message nodes."""
    try:
        return driver.execute_script(
            "return (function(){" + _ERROR_HARVEST_JS + "})();") or []
    except Exception:
        return []


def _verify_logged_in(driver, login_url, cb):
    """Best-effort check that login actually succeeded. Returns (ok, reason)."""
    import time as _t
    _t.sleep(1.0)
    cur = (driver.current_url or "").rstrip("/")
    base_login = (login_url or "").rstrip("/")
    moved = cur != base_login
    still_pw = False
    try:
        from selenium.webdriver.common.by import By
        pw = driver.find_elements(By.CSS_SELECTOR, "input[type=password]")
        still_pw = any(e.is_displayed() for e in pw)
    except Exception:
        pass
    err = False
    try:
        body = (driver.find_element("tag name", "body").text or "").lower()
        for kw in ("invalid", "incorrect", "failed", "غير صحيح", "خطأ",
                   "wrong password", "try again", "بيانات غير"):
            if kw in body:
                err = True; break
    except Exception:
        pass
    if still_pw and not moved:
        return False, "still on the login form (login likely failed)"
    if err and not moved:
        return False, "an error message is shown on the login page"
    return True, ("logged in — now at " + cur)


def scrape_dom(url, login=None, cb=None, headless=True, wait_secs=4):
    """Open `url` in Selenium Chrome, optionally log in, then harvest interactive
    elements. Returns a list of dicts:
        {"tag","type","id","name","text","placeholder","aria","css","xpath"}
    `login` (optional) = {
        "url": login page url (defaults to `url`),
        "user": username, "password": password,
        "user_locator": css for the username field,
        "pass_locator": css for the password field,
        "submit_locator": css for the submit button,
    }
    cb(msg, tone) optional logger.
    """
    cb = cb or (lambda *a, **k: None)
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    import time as _t

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-gpu")

    cb("Launching Chrome…", "dim")
    driver = webdriver.Chrome(options=opts)
    try:
        # ── optional login ──
        if login and login.get("user") and login.get("password"):
            login_url = login.get("url") or url
            cb(f"Opening login page: {login_url}", "dim")
            driver.get(login_url)
            _t.sleep(wait_secs)
            try:
                u = driver.find_element(By.CSS_SELECTOR, login.get("user_locator") or "input[type=email],input[type=text],input[name*=user]")
                u.clear(); u.send_keys(login["user"])
                p = driver.find_element(By.CSS_SELECTOR, login.get("pass_locator") or "input[type=password]")
                p.clear(); p.send_keys(login["password"])
                btn_sel = login.get("submit_locator") or "button[type=submit],input[type=submit],button"
                driver.find_element(By.CSS_SELECTOR, btn_sel).click()
                cb("Submitted login — waiting for redirect…", "dim")
                _t.sleep(wait_secs + 2)
            except Exception as e:
                cb(f"Login step issue (continuing): {e}", "warn")

        cb(f"Opening target page: {url}", "dim")
        driver.get(url)
        _t.sleep(wait_secs)

        # ── harvest interactive elements ──
        cb("Reading the live DOM…", "dim")
        js = r"""
        function robustCss(el){
          if(el.id) return '#'+CSS.escape(el.id);
          if(el.name) return el.tagName.toLowerCase()+'[name="'+el.name+'"]';
          let path=[], e=el;
          while(e && e.nodeType===1 && path.length<5){
            let sel=e.tagName.toLowerCase();
            if(e.className && typeof e.className==='string'){
              let c=e.className.trim().split(/\s+/).filter(Boolean).slice(0,2);
              if(c.length) sel+='.'+c.map(x=>CSS.escape(x)).join('.');
            }
            let p=e.parentNode, idx=1, sib=e;
            while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
            sel+=':nth-of-type('+idx+')';
            path.unshift(sel);
            e=e.parentNode;
            if(e && e.id){ path.unshift('#'+CSS.escape(e.id)); break; }
          }
          return path.join(' > ');
        }
        function xpathOf(el){
          if(el.id) return '//*[@id="'+el.id+'"]';
          let parts=[], e=el;
          while(e && e.nodeType===1){
            let idx=1, sib=e;
            while(sib=sib.previousElementSibling){ if(sib.tagName===e.tagName) idx++; }
            parts.unshift(e.tagName.toLowerCase()+'['+idx+']');
            e=e.parentNode;
          }
          return '/'+parts.join('/');
        }
        const sel='input,button,a,select,textarea,[role=button],[role=link],[role=tab],[contenteditable=true]';
        const els=[...document.querySelectorAll(sel)];
        return els.slice(0,200).map(el=>({
          tag: el.tagName.toLowerCase(),
          type: el.getAttribute('type')||'',
          id: el.id||'',
          name: el.getAttribute('name')||'',
          text: (el.innerText||el.value||'').trim().slice(0,60),
          placeholder: el.getAttribute('placeholder')||'',
          aria: el.getAttribute('aria-label')||'',
          css: robustCss(el),
          xpath: xpathOf(el)
        }));
        """
        elements = driver.execute_script(js) or []
        cb(f"Found {len(elements)} interactive element(s).", "ok")
        return elements
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  INTENT-DRIVEN EXPLORER  (compile → deterministic execute → AI tie-break)
#  Replaces the old "ask the AI to pick 1 of 120 elements per step" approach,
#  which produced repeated clicks (assertions/restated steps treated as actions)
#  and false guesses (preconditions treated as actions). Now:
#    1. compile_test_case()  — LLM turns messy steps into typed intents ONCE per
#       case (precondition / action / assertion), with page-language keywords.
#    2. _rank_candidates()   — deterministic locator binding against the live DOM.
#    3. AI is used only to break ties among a short candidate list, never to
#       invent locators. Assertions bind by DOM-diff (what newly appeared).
# ═══════════════════════════════════════════════════════════════════════════════
import unicodedata as _ud

_AR_DIACRITICS = "".join(chr(c) for c in list(range(0x0610, 0x061B)) +
                         list(range(0x064B, 0x0660)) + [0x0670, 0x0640])  # +tatweel

def _norm(s):
    """Normalize text for language-agnostic matching: lowercase, strip Arabic
    diacritics/tatweel, unify alef/ya/ta-marbuta, collapse whitespace."""
    s = (s or "").strip().lower()
    s = "".join(ch for ch in s if ch not in _AR_DIACRITICS)
    s = (s.replace("\u0623", "\u0627").replace("\u0625", "\u0627").replace("\u0622", "\u0627")
           .replace("\u0649", "\u064a").replace("\u0629", "\u0647"))
    s = _ud.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


# Framework-generated ids that change between renders/sessions and MUST NOT be
# captured as saved locators (the generated Java would break next run):
# PrimeNG (pn_id_*), Angular CDK/Material (cdk-*, mat-*), React useId (:r0:),
# GUIDs, and PrimeNG panel/header patterns like pn_id_18_0_header.
_VOLATILE_ID = re.compile(
    r"(^pn_id|^cdk-|^mat-|^ui-id-|^:r[0-9a-z]+:|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}|_[0-9]+_[0-9]+)", re.I)


def _xq(s):
    """Quote a string for an XPath literal, handling embedded quotes via concat()."""
    s = s or ""
    if '"' not in s:
        return '"' + s + '"'
    if "'" not in s:
        return "'" + s + "'"
    return "concat(" + ", '\"', ".join('"%s"' % p for p in s.split('"')) + ")"


_LOGIN_CTX_KWS = ("login", "log in", "logon", "sign in", "signin", "authenticat",
                  "تسجيل الدخول", "تسجيل دخول", "الدخول", "كلمة المرور", "كلمة مرور",
                  "اسم المستخدم", "اسم مستخدم")
_NEG_LOGIN_KWS = ("invalid", "wrong", "incorrect", "fail", "empty", "blank", "without",
                  "locked", "lockout", "bad credentials", "required",
                  "خاطئ", "خاطئة", "غير صحيح", "غير صحيحة", "بيانات غير", "فارغ",
                  "بدون", "فشل", "خطأ", "مطلوب")
_PRESENCE_KWS = ("التحقق من وجود", "من وجود", "وجود", "موجود", "ظهور",
                 "verify the existence", "existence of", "presence of", "exists",
                 "is displayed", "is visible", "is present")
_EMPTY_FIELD_KWS = ("empty", "blank", "without", "leave it", "leave the", "do not enter",
                    "don't enter", "no password", "no username", "missing",
                    "فارغ", "بدون", "اترك", "دون إدخال", "لا تدخل")


def _tc_blob(tc):
    blob = (tc.get("title", "") or "")
    for s in (tc.get("steps") or []):
        blob += " " + (s.get("action", "") or "") + " " + \
                (s.get("expected", "") or "") + " " + (s.get("precondition", "") or "")
    return blob.lower()


def _is_negative_login_tc(tc):
    """A negative/validation LOGIN case — must run on a fresh login page so the
    bad submit surfaces real error-state locators."""
    low = _tc_blob(tc)
    return (any(k in low for k in _LOGIN_CTX_KWS) and
            any(k.lower() in low for k in _NEG_LOGIN_KWS))


def _classify_case(tc):
    """Return 'negative_login' | 'presence' | 'interaction'."""
    if _is_negative_login_tc(tc):
        return "negative_login"
    # presence is a property of the case's INTENT (its title), not of an "appears"
    # word that may show up in any interaction case's expected result.
    title = _norm(tc.get("title", ""))
    if any(_norm(k) in title for k in _PRESENCE_KWS):
        return "presence"
    return "interaction"


# Words that say a case belongs on the LOGGED-OUT login page (where the Keycloak
# language DROPDOWN lives) vs. the authenticated app (where a single language
# TOGGLE button lives). Inferred from the case's own title/steps — no hard-coded
# per-story rules — exactly as the AI-authored case intends.
_LOGIN_PAGE_KWS = ("صفحة تسجيل الدخول", "صفحه تسجيل الدخول", "تسجيل الدخول",
                   "قبل تسجيل الدخول", "شاشة الدخول", "login page", "sign-in page",
                   "sign in page", "on login", "locale", "kc_locale")
_DROPDOWN_KWS = ("قائمة منسدلة", "قائمه منسدله", "منسدلة", "منسدله", "القائمة المنسدلة",
                 "الاختيار بين", "قائمة اللغات", "dropdown", "drop-down", "drop down")


def _infer_page_context(tc, case_type="interaction"):
    """Decide where this case runs: 'login' (logged-out, language dropdown) or
    'app' (post-login, single language toggle). Read from the case text. Ambiguous
    cases default to 'app' (the common case) and the choice is logged so a wrong
    inference is visible in the activity feed."""
    if case_type == "negative_login":
        return "login"
    blob = _norm(_tc_blob(tc))
    if any(_norm(k) in blob for k in _LOGIN_PAGE_KWS):
        return "login"
    # a language case described as a DROPDOWN / choose-between is the login page;
    # the in-app control is a single toggle with no dropdown.
    lang = any(_norm(k) in blob for k in ("اللغة", "لغة", "language", "locale"))
    if lang and any(_norm(k) in blob for k in _DROPDOWN_KWS):
        return "login"
    return "app"


def _wants_empty_field(text):
    t = (text or "").lower()
    return any(k in t for k in _EMPTY_FIELD_KWS)


def compile_test_case(tc, story=None, log=None, case_type="interaction"):
    """STAGE 1 — turn a test case's raw steps into a normalized, deduplicated list
    of typed INTENTS. The LLM reads the (often messy, Arabic) steps and returns
    JSON; it never sees locators, so it cannot hallucinate them.

    case_type ('presence'|'interaction'|'negative_login') shapes the output: a
    presence case must NOT be walked as a long interaction.

    Each intent:
      {"role":"precondition"|"action"|"assertion",
       "verb":"navigate|click|type|select|hover|wait",   # action only
       "target":"<human description>",
       "keywords":["visible text / aria tokens in the PAGE language", ...],
       "kind":"button|link|input|select|checkbox|menuitem|text|any",
       "value":"<text to type/select, '' for empty-field cases>",
       "check":"visible|hidden|text_contains|url_contains|enabled|disabled|count",
       "expected":"<expected value for the check>",
       "from_steps":[1-based original step indices this intent came from]}

    Returns a list of intents, or [] on failure (caller falls back to raw steps).
    """
    log = log or (lambda *a, **k: None)
    steps = tc.get("steps") or []
    raw = []
    for i, s in enumerate(steps, 1):
        raw.append({"n": i, "precondition": (s.get("precondition", "") or "").strip(),
                    "action": (s.get("action", "") or "").strip(),
                    "expected": (s.get("expected", "") or "").strip()})
    lang = "Arabic" if _is_arabic_out() else "English"
    shape = {
        "presence": ("This is a PRESENCE/visibility case. Emit the MINIMUM: an "
                     "optional navigate, then ONE assertion that the element is "
                     "visible. Do NOT click/select or walk an interaction."),
        "negative_login": ("This is a NEGATIVE-LOGIN case. Emit: type the (invalid "
                           "or empty) credentials, click submit, then ONE assertion "
                           "that the error/validation message is visible."),
        "interaction": ("This is an INTERACTION case. Emit the real action sequence "
                        "the user performs, each action ONCE."),
    }.get(case_type, "Emit the real action sequence, each action once.")
    prompt = (
        "You convert ONE UI test case into an ordered list of atomic INTENTS for a "
        "Selenium walker. The steps may be in Arabic or English and are often noisy: "
        "preconditions written as steps, the same action restated across several "
        "steps, or an action and its expected result merged together.\n\n"
        f"CASE TYPE: {case_type} — {shape}\n\n"
        "RULES:\n"
        "- Output ONLY a JSON array, no markdown, no commentary.\n"
        "- role='precondition' for environmental/state setup with NO UI action "
        "(internet available, browser open, user is on page X). Do NOT invent a click for these.\n"
        "- role='action' for ONE real UI operation: verb in "
        "[navigate,click,type,select,hover,wait]. Collapse repeated/restated steps "
        "that describe the SAME operation into a SINGLE action. Never emit the same "
        "action twice in a row.\n"
        "- role='assertion' for a verification/expected outcome. COLLAPSE assertions "
        "hard: emit at most ONE assertion per distinct observable outcome, and never "
        "two assertions in a row that check the same thing. Most cases need 1-2 "
        "assertions total, NOT one per step. An assertion is NOT an action — never click for it.\n"
        "- A custom dropdown (PrimeNG/Material, not a native <select>) is TWO actions: "
        "click the trigger to open it, then click/select the option. For 'select' set "
        "kind='menuitem' and value=the option's visible text (e.g. English / العربية).\n"
        "- Total intents should be SMALL — roughly (distinct actions) + 1 or 2 "
        "assertions. If your output has more assertions than actions, you over-split; redo it.\n"
        "- 'keywords' = the literal visible text / aria-label / placeholder tokens the "
        f"element most likely has, in the page language ({lang}); include both the "
        "Arabic and an English guess when unsure. For ICON-ONLY buttons (no text), "
        "also add likely icon-class tokens: globe, language, lang, flag, world, "
        "translate. Keep 1-6 short tokens.\n"
        "- 'kind' = the element type you expect.\n"
        "- For empty-field validation steps (leave a field blank), emit a type action "
        "with value='' so the walker leaves it empty.\n"
        "- 'from_steps' MUST list the original step number(s) each intent came from.\n\n"
        f"TEST CASE TITLE: {tc.get('title','')}\n"
        f"ACCEPTANCE CRITERIA: {((story or {}).get('criteria') or '')[:800]}\n"
        f"RAW STEPS (JSON): {json.dumps(raw, ensure_ascii=False)[:5000]}\n\n"
        'Example item: {"role":"action","verb":"click","target":"language switcher",'
        '"keywords":["اللغة","language","lang"],"kind":"button","value":"",'
        '"check":"","expected":"","from_steps":[4,5,6]}'
    )
    try:
        out = parse_json_robust(ai_complete(prompt, max_tokens=2048, timeout=90,
                                            on_retry=lambda m: log(m, "dim"),
                                            want_json=True))
        if isinstance(out, dict):
            out = out.get("intents") or out.get("items") or [out]
        if not isinstance(out, list) or not out:
            return []
        clean = []
        for it in out:
            if not isinstance(it, dict):
                continue
            role = (it.get("role") or "action").strip().lower()
            if role not in ("precondition", "action", "assertion"):
                role = "action"
            fs = it.get("from_steps") or []
            if isinstance(fs, int):
                fs = [fs]
            clean.append({
                "role": role,
                "verb": (it.get("verb") or ("navigate" if role == "action" else "")).strip().lower(),
                "target": (it.get("target") or "").strip(),
                "keywords": [str(k) for k in (it.get("keywords") or []) if str(k).strip()][:6],
                "kind": (it.get("kind") or "any").strip().lower(),
                "value": str(it.get("value") or ""),
                "check": (it.get("check") or "").strip().lower(),
                "expected": str(it.get("expected") or ""),
                "from_steps": [int(x) for x in fs if str(x).strip().lstrip("-").isdigit()],
            })
        # Safety net: collapse consecutive assertions that check the same thing
        # (the LLM sometimes still emits one per restated step). Merge their
        # from_steps so each original step still receives an assert_locator.
        collapsed = []
        for it in clean:
            if (it["role"] == "assertion" and collapsed and
                    collapsed[-1]["role"] == "assertion" and
                    _norm(collapsed[-1]["target"]) == _norm(it["target"]) and
                    _norm(collapsed[-1]["expected"]) == _norm(it["expected"])):
                collapsed[-1]["from_steps"] = sorted(set(collapsed[-1]["from_steps"] + it["from_steps"]))
                continue
            collapsed.append(it)
        return collapsed
    except CreditBalanceError:
        raise
    except Exception as e:
        # Recoverable provider errors (expired/invalid key, rate limit, outage)
        # propagate so the run can PAUSE and let the user fix it + Resume.
        if _is_recoverable_ai_error(e):
            raise
        # Genuine non-recoverable issues (e.g. a malformed JSON response) fall
        # back to raw steps with a single clean line — no giant JSON dump.
        log(f"    compile failed ({friendly_ai_error(e)}) — using raw steps", "warn")
        return []


def _intents_from_raw_steps(tc):
    """Fallback when the compiler is unavailable: derive simple intents from the
    raw steps so the walk never regresses below the old behavior."""
    intents = []
    for i, s in enumerate(tc.get("steps") or [], 1):
        action = (s.get("action", "") or "").strip()
        exp = (s.get("expected", "") or "").strip()
        disp = action
        for pfx in ("الشرط المسبق:", "الإجراء:", "Precondition:", "Action:"):
            disp = disp.replace(pfx, " ")
        disp = disp.strip()
        if not disp and not exp:
            intents.append({"role": "precondition", "verb": "", "target": "",
                            "keywords": [], "kind": "any", "value": "", "check": "",
                            "expected": "", "from_steps": [i]})
            continue
        if disp:
            low = _norm(disp)
            verb = ("type" if any(k in low for k in ("type", "enter", "ادخل", "أدخل", "اكتب", "كتابة"))
                    else "select" if any(k in low for k in ("select", "اختر", "اختيار"))
                    else "click")
            intents.append({"role": "action", "verb": verb, "target": disp,
                            "keywords": [w for w in re.split(r"[\s,.:؛،]+", disp) if len(w) > 2][:6],
                            "kind": "input" if verb == "type" else "any",
                            "value": "" if _wants_empty_field(disp) else "",
                            "check": "", "expected": "", "from_steps": [i]})
        if exp:
            intents.append({"role": "assertion", "verb": "", "target": exp,
                            "keywords": [w for w in re.split(r"[\s,.:؛،]+", exp) if len(w) > 2][:6],
                            "kind": "any", "value": "", "check": "visible",
                            "expected": "", "from_steps": [i]})
    return intents


def _el_haystack(el):
    return _norm(" ".join(str(el.get(k, "")) for k in
                          ("text", "aname", "aria", "placeholder", "name", "id",
                           "role", "testid", "type", "cls", "svgicon")))


def _kind_matches(kind, el):
    if not kind or kind == "any":
        return False
    tag = (el.get("tag") or "").lower(); typ = (el.get("type") or "").lower()
    role = (el.get("role") or "").lower(); cls = _norm(el.get("cls", ""))
    menu_cls = any(t in cls for t in ("menu-item", "menuitem", "dropdown-item",
                                      "dropdownitem", "list-item", "option"))
    m = {"button": tag == "button" or typ in ("button", "submit") or role == "button",
         "link": tag == "a" or role == "link",
         "input": tag in ("input", "textarea") and typ not in ("button", "submit", "checkbox"),
         "select": tag == "select" or role in ("combobox", "listbox"),
         "checkbox": typ == "checkbox" or role in ("checkbox", "switch"),
         "menuitem": role in ("menuitem", "option", "tab") or menu_cls}
    return bool(m.get(kind, False))


def _rank_candidates(intent, elements):
    """STAGE 2 — deterministic scoring of live elements against an intent.
    Returns a list of (score, element) sorted high→low. No LLM involved."""
    kws = [_norm(k) for k in (intent.get("keywords") or []) if _norm(k)]
    tgt = _norm(intent.get("target", ""))
    kind = intent.get("kind", "any")
    verb = intent.get("verb", "")
    ranked = []
    for el in elements:
        if not el.get("visible", True):
            continue
        hay = _el_haystack(el)
        score = 0.0
        for k in kws:
            if k and k in hay:
                score += 2.0
                if hay == k or (el.get("text") and _norm(el["text"]) == k):
                    score += 1.0           # exact label match
        # token overlap with the target description
        for tok in (t for t in tgt.split(" ") if len(t) > 2):
            if tok in hay:
                score += 0.5
        if _kind_matches(kind, el):
            score += 1.0
        if verb == "type" and el.get("tag") in ("input", "textarea"):
            score += 0.5
        if score > 0:
            ranked.append((score, el))
    ranked.sort(key=lambda t: t[0], reverse=True)
    return ranked


def _tiebreak_with_ai(intent, shortlist, cb):
    """The ONLY place the LLM picks an element — and only among a short list of
    real candidates (never the full DOM). Returns the chosen element or None."""
    brief = [{"idx": e["idx"], "tag": e.get("tag"), "type": e.get("type"),
              "text": e.get("text"), "aname": e.get("aname"), "aria": e.get("aria"),
              "placeholder": e.get("placeholder"), "id": e.get("id")}
             for e in shortlist]
    prompt = (
        "Pick the ONE element that best matches the intent. Reply ONLY JSON.\n"
        f"INTENT: {json.dumps({k: intent.get(k) for k in ('role','verb','target','keywords','kind')}, ensure_ascii=False)}\n"
        f"CANDIDATES: {json.dumps(brief, ensure_ascii=False)[:3000]}\n"
        '{"idx": <chosen idx or -1>}'
    )
    try:
        data = parse_json_robust(ai_complete(prompt, max_tokens=256, timeout=45, want_json=True))
        if isinstance(data, list) and data:
            data = data[0]
        idx = int(data.get("idx", -1))
        return next((e for e in shortlist if e.get("idx") == idx), None) if idx >= 0 else None
    except CreditBalanceError:
        raise
    except Exception as e:
        cb(f"    tiebreak error: {str(e)[:60]}", "warn")
        return None


def _to_locator(el):
    """Pick the most STABLE locator for the SAVED test (generated Java), so it
    survives re-renders. Order: data-testid > data-svgicon > stable id > name >
    aria-label > short visible text > css path > xpath. Framework-generated ids
    (PrimeNG pn_id_*, Angular/React, GUIDs) are skipped — they change every run."""
    if not el:
        return None
    tid = (el.get("testid") or "").strip()
    if tid:
        return {"by": "css", "value": '[data-testid="%s"]' % tid}
    svg = (el.get("svgicon") or "").strip()
    if svg:
        return {"by": "css", "value": '[data-svgicon="%s"]' % svg}
    eid = (el.get("id") or "").strip()
    if eid and not _VOLATILE_ID.search(eid):
        return {"by": "id", "value": eid}
    nm = (el.get("name") or "").strip()
    if nm:
        return {"by": "name", "value": nm}
    al = (el.get("aria") or el.get("aname") or "").strip()
    if al:
        return {"by": "xpath", "value": "//*[@aria-label=%s]" % _xq(al)}
    txt = (el.get("text") or "").strip()
    if txt and len(txt) <= 40:
        return {"by": "xpath",
                "value": "//%s[normalize-space()=%s]" % (el.get("tag", "*"), _xq(txt))}
    css = (el.get("css") or "")
    if css and not _VOLATILE_ID.search(css):
        return {"by": "css", "value": css}
    return {"by": "xpath", "value": el.get("xpath", "")}


def _match_step_to_element(action, elements, cb):
    """Ask the AI which real DOM element best matches a step's action.
    Returns (element_dict_or_None, kind) where kind in
    {'type','click','select','navigate','none'} and a value to type if relevant."""
    # Trim elements to the fields the model needs (keep idx for reference)
    brief = [{"idx": e["idx"], "tag": e["tag"], "type": e["type"], "id": e["id"],
              "name": e["name"], "text": e["text"], "placeholder": e["placeholder"],
              "aria": e["aria"], "visible": e["visible"]}
             for e in elements if e.get("visible", True)][:120]
    prompt = (
        "You map a single UI test step to ONE real element on the current page.\n"
        "The step action may be in Arabic or English. Choose the best matching element.\n\n"
        f"STEP ACTION: {action}\n\n"
        f"ELEMENTS (JSON, use the 'idx'):\n{json.dumps(brief, ensure_ascii=False)[:6000]}\n\n"
        "Reply with ONLY a JSON object, no markdown:\n"
        '{\"idx\": <element idx or -1 if none fits>, '
        '\"kind\": \"click|type|select|navigate|none\", '
        '\"value\": \"<text to type if kind==type/select, else empty>\"}'
    )
    try:
        raw = ai_complete(prompt, max_tokens=1024, timeout=60)
        if not (raw or "").strip():
            # reasoning models (e.g. NVIDIA qwen) sometimes spend the whole
            # budget thinking and return empty — retry once before giving up
            cb("    matcher returned empty — retrying once…", "dim")
            raw = ai_complete(prompt, max_tokens=1024, timeout=60)
        data = parse_json_robust(raw)
        if isinstance(data, list) and data:
            data = data[0]
        idx = int(data.get("idx", -1))
        kind = (data.get("kind") or "none").strip().lower()
        value = data.get("value", "") or ""
        if idx is None or idx < 0:
            return None, kind, value
        match = next((e for e in elements if e.get("idx") == idx), None)
        return match, kind, value
    except CreditBalanceError:
        raise  # propagate so explore_and_map can auto-stop on repeated hits
    except Exception as e:
        cb(f"  match error: {str(e)[:80]}", "warn")
        return None, "none", ""


def explore_and_map(stories_payload, login, site_url, cb=None, should_stop=None,
                    headless=False, wait_secs=3):
    """LIVE-WALK explorer: logs in (verifying success), then for each test case
    walks its steps in order — matching each step's action to a real element on
    the live page, recording the EXACT locator, and performing the action so the
    page advances to the next step's state.

    Returns the stories_payload enriched so each step gains:
        step["locator"]      = {"by": "id|name|css|xpath", "value": "..."} or None
        step["locator_src"]  = "live" | "snapshot" | "guess"
        step["assert_locator"] (best-effort target for the expected result)
    Also attaches each story's accumulated DOM snapshots for fallback generation.

    NOTE: this performs REAL clicks/typing on the target site. Use a TEST env.
    """
    cb = cb or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import time as _t

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-gpu")

    cb("Launching Chrome…", "dim")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    wait = WebDriverWait(driver, 20)
    all_snapshots = []   # union of every element seen (for fallback)

    def wait_dom_ready():
        """Wait until document.readyState is complete (page loaded)."""
        try:
            WebDriverWait(driver, 25).until(
                lambda d: d.execute_script("return document.readyState") == "complete")
        except Exception:
            pass

    def find_first(selectors, timeout=20):
        """Wait for and return the first element matching any CSS selector in the
        comma-or-list group. Tries each selector; returns None if none appear."""
        if isinstance(selectors, str):
            selectors = [s.strip() for s in selectors.split(",") if s.strip()]
        end = _t.time() + timeout
        while _t.time() < end:
            for sel in selectors:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for e in els:
                        if e.is_displayed() and e.is_enabled():
                            return e
                except Exception:
                    pass
            _t.sleep(0.4)
        return None

    def snapshot(tag="", with_errors=False):
        els = _harvest_dom(driver)
        if with_errors:
            errs = _harvest_errors(driver)
            base = len(els)
            for j, e in enumerate(errs):
                e2 = dict(e); e2["idx"] = base + j
                els.append(e2)
            if errs and tag:
                cb(f"  + {len(errs)} message/error element(s)", "dim")
        all_snapshots.extend(els)
        if tag:
            cb(f"  captured {len(els)} elements ({tag})", "dim")
        return els

    def _el_key(e):
        return (e.get("id"), e.get("name"), e.get("css"), e.get("xpath"))

    def to_locator(el):
        """Stable locator strategy (module-level _to_locator, see there)."""
        return _to_locator(el)

    def _dedup_reindex(els):
        """De-dup the merged snapshot union and assign fresh unique idx values,
        so the matcher can reference elements unambiguously (each per-page harvest
        restarts idx at 0, which would otherwise collide across the union)."""
        seen = set(); out = []
        for e in els:
            key = (e.get("id"), e.get("name"), e.get("css"), e.get("xpath"))
            if key in seen:
                continue
            seen.add(key)
            e2 = dict(e); e2["idx"] = len(out)
            out.append(e2)
        return out

    def find_live(el):
        """Locate the actual Selenium element for an harvested element dict."""
        try:
            if el.get("id"):
                return driver.find_element(By.ID, el["id"])
            if el.get("name"):
                return driver.find_element(By.NAME, el["name"])
            if el.get("css"):
                return driver.find_element(By.CSS_SELECTOR, el["css"])
            return driver.find_element(By.XPATH, el["xpath"])
        except Exception:
            return None

    def _safe_click(el):
        """Click that survives overlays: scroll into view, try native click,
        then fall back to a JS click if something intercepts it."""
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        try:
            el.click(); return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", el); return True
            except Exception as e:
                cb(f"    click fallback failed: {str(e)[:70]}", "warn"); return False

    def _describe(el):
        """Short, human-readable identity for a harvested element (for the log)."""
        if not el:
            return "?"
        if el.get("id"):
            return "#" + el["id"]
        if el.get("name"):
            return "[name=" + el["name"] + "]"
        t = (el.get("text") or el.get("aria") or el.get("placeholder") or "").strip()
        if t:
            return '"' + t[:24] + '"'
        return (el.get("css") or el.get("xpath") or "?")[:32]

    def _flash(live_el):
        """Briefly outline the element in the real browser so the human watching
        can SEE which element each step touched."""
        try:
            driver.execute_script(
                "var o=arguments[0];var p=o.style.outline;var q=o.style.outlineOffset;"
                "o.scrollIntoView({block:'center'});"
                "o.style.outline='3px solid #6A4DFF';o.style.outlineOffset='2px';"
                "setTimeout(function(){o.style.outline=p;o.style.outlineOffset=q;},900);",
                live_el)
        except Exception:
            pass

    def _settle(timeout=8):
        """Wait for the page to stop being busy: readyState complete, no
        aria-busy, and common spinner/loader overlays gone."""
        wait_dom_ready()
        end = _t.time() + timeout
        while _t.time() < end:
            try:
                busy = driver.execute_script(
                    "var b=document.querySelector('[aria-busy=true]');"
                    "var s=document.querySelector("
                    "  '.spinner,.loading,.loader,.MuiBackdrop-root,[class*=spinner i],[class*=loading i]');"
                    "function vis(e){return e&&(e.offsetWidth||e.offsetHeight||e.getClientRects().length);}"
                    "return !!(vis(b)||vis(s));")
            except Exception:
                busy = False
            if not busy:
                return
            _t.sleep(0.25)

    def _dismiss_overlays():
        """Best-effort dismissal of cookie/consent banners and stray modals that
        would intercept clicks. Only clicks clearly-dismissive controls."""
        labels = ["accept", "accept all", "agree", "i agree", "got it", "ok", "close",
                  "dismiss", "no thanks", "موافق", "قبول", "أوافق", "إغلاق", "تم", "حسنا"]
        try:
            btns = _harvest_dom(driver)
            for el in btns:
                txt = _norm(el.get("text") or el.get("aname") or el.get("aria"))
                if not txt:
                    continue
                if any(_norm(l) == txt or _norm(l) in txt for l in labels):
                    live = find_live(el)
                    if live is not None and live.is_displayed():
                        try:
                            driver.execute_script("arguments[0].click();", live)
                            _t.sleep(0.4)
                            return True
                        except Exception:
                            pass
        except Exception:
            pass
        return False

    def _topmost_ok(live_el):
        """True if the element is the topmost hit at its center (not covered by an
        overlay). Used to decide whether to dismiss an overlay before clicking."""
        try:
            return driver.execute_script(
                "var e=arguments[0];var r=e.getBoundingClientRect();"
                "if(!r.width||!r.height)return false;"
                "var x=r.left+r.width/2,y=r.top+r.height/2;"
                "var t=document.elementFromPoint(x,y);"
                "return !!t&&(t===e||e.contains(t)||t.contains(e));", live_el)
        except Exception:
            return True

    def _act(el_dict, verb, value, empty_ok=False):
        """STAGE 3 — perform an action so it survives overlays and timing.
        Re-finds the element, scrolls to center, waits to settle, clears overlays
        if it's covered, then runs a retry ladder (native → JS → ActionChains).
        Returns the live element acted on (or None)."""
        live = find_live(el_dict)
        if live is None:
            return None
        _flash(live)
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", live)
        except Exception:
            pass
        _settle(timeout=4)
        # type / select don't need topmost; clicks do
        if verb == "type":
            try:
                if empty_ok:
                    live.clear()
                else:
                    live.clear(); live.send_keys(value or "test")
                return live
            except Exception as e:
                cb(f"      type failed: {str(e)[:50]}", "warn"); return live
        if verb == "select":
            from selenium.webdriver.support.ui import Select
            # native <select>
            if (el_dict.get("tag") or "").lower() == "select":
                try:
                    Select(live).select_by_visible_text(value); return live
                except Exception:
                    try:
                        Select(live).select_by_value(value); return live
                    except Exception:
                        pass
            # custom dropdown (PrimeNG/Material/etc.): open the trigger, then click
            # the option whose visible text matches `value` (options render in an
            # overlay appended to <body> only after opening).
            self_open = _act(el_dict, "click", "", empty_ok)
            _settle(timeout=3); _t.sleep(0.4)
            want = _norm(value)
            want_toks = [t for t in want.split(" ") if len(t) > 1]
            best = None
            for o in _harvest_dom(driver):
                if not o.get("visible", True):
                    continue
                hay = _norm(" ".join(str(o.get(k, "")) for k in ("text", "aname", "aria")))
                if not hay:
                    continue
                if (want and want in hay) or any(tok in hay for tok in want_toks) or hay in want:
                    best = o; break
            if best is not None:
                lo = find_live(best)
                if lo is not None:
                    _flash(lo)
                    try:
                        lo.click()
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", lo)
                        except Exception:
                            pass
                    return lo
            cb(f"      select: option '{value[:24]}' not found after opening", "warn")
            return self_open or live
        # click / hover / navigate(default) — make it interception-proof
        if not _topmost_ok(live):
            if _dismiss_overlays():
                live = find_live(el_dict) or live
        try:
            WebDriverWait(driver, 6).until(EC.element_to_be_clickable(live))
        except Exception:
            pass
        # retry ladder
        for attempt in range(3):
            try:
                live.click(); return live
            except Exception:
                live = find_live(el_dict) or live      # handle staleness
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(driver).move_to_element(live).pause(0.1).click().perform()
                    return live
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", live); return live
                    except Exception:
                        _dismiss_overlays(); _t.sleep(0.3)
        cb("      could not click after retries", "warn")
        return live

    try:
        # ── login + verify ──
        login_url = (login or {}).get("url") or site_url
        # Keycloak (and similar) login URLs often carry one-time session params
        # (execution, tab_id, session_code, code, client_data). Hitting that exact
        # URL later yields "Cookie not found" because the session is gone. Strip
        # those so the IdP issues a FRESH login page + cookie.
        try:
            from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
            parts = urlsplit(login_url)
            if parts.query:
                stale = {"execution", "tab_id", "session_code", "code",
                         "client_data", "auth_session_id", "kc_locale"}
                kept = [(k, v) for k, v in parse_qsl(parts.query)
                        if k.lower() not in stale]
                login_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                        urlencode(kept), ""))
            # If the path points at the one-time login-actions endpoint, drop back
            # to the site URL so Keycloak starts a clean auth flow.
            if "login-actions/authenticate" in parts.path:
                cb("Login URL had one-time session params — using the site URL "
                   "to start a fresh login.", "warn")
                login_url = site_url
        except Exception:
            pass
        if login and login.get("user") and login.get("password"):
            def do_login(fresh=False):
                """Run the login flow on a clean login page. fresh=True clears the
                session first (used to re-establish auth after a negative-login
                case). Returns (ok, reason)."""
                if fresh:
                    try:
                        driver.delete_all_cookies()
                    except Exception:
                        pass
                cb("Opening login page\u2026", "dim")
                driver.get(login_url)
                wait_dom_ready()
                try:
                    user_sel = login.get("user_locator") or (
                        "#username,input[type=email],input[name=email],input[name=username],"
                        "input[name*=user i],input[id*=user i],input[type=text]")
                    cb("Waiting for the username field\u2026", "dim")
                    u = find_first(user_sel, timeout=25)
                    if u is None:
                        raise RuntimeError("username/email field did not appear")
                    u.clear(); u.send_keys(login["user"])
                    pass_sel = login.get("pass_locator") or "#password,input[type=password]"
                    p = find_first(pass_sel, timeout=10)
                    if p is None:
                        nxt = find_first("button[type=submit],#kc-login,button,input[type=submit]", timeout=5)
                        if nxt is not None:
                            nxt.click(); wait_dom_ready()
                        p = find_first(pass_sel, timeout=15)
                    if p is None:
                        raise RuntimeError("password field did not appear")
                    p.clear(); p.send_keys(login["password"])
                    submit_sel = login.get("submit_locator") or (
                        "#kc-login,button[type=submit],input[type=submit],button")
                    btn = find_first(submit_sel, timeout=10)
                    if btn is None:
                        p.submit()
                    else:
                        btn.click()
                    cb("Submitted login \u2014 verifying\u2026", "dim")
                    try:
                        WebDriverWait(driver, 20).until(
                            lambda d: (d.current_url or "").rstrip("/") != login_url.rstrip("/")
                                      or not d.find_elements(By.CSS_SELECTOR, "input[type=password]"))
                    except Exception:
                        pass
                    wait_dom_ready()
                except Exception as e:
                    raise RuntimeError(f"Login step failed: {str(e)[:160]}")
                return _verify_logged_in(driver, login_url, cb)

            have_creds = True
            ok, reason = do_login()
            if not ok:
                raise RuntimeError(f"Login could not be verified — {reason}. "
                                   f"Aborting so locators aren't captured from the wrong page.")
            cb(f"Login verified — {reason}", "ok")
        else:
            have_creds = False
            cb("No login provided — exploring as anonymous user.", "warn")

        # Navigate to the starting page
        cb(f"Opening target page…", "dim")
        driver.get(site_url)
        wait_dom_ready()
        _t.sleep(1.0)

        live_count = snap_count = guess_count = 0
        todos = []                 # steps that will carry a // TODO verify locator
        credit_hits = 0            # repeated AI credit-limit errors -> auto-stop
        CREDIT_STOP = 5
        abort_credit = False

        def _credit_guard(fn, *a, **k):
            """Run an AI-calling fn; count credit errors and trip abort_credit."""
            nonlocal credit_hits, abort_credit
            try:
                return fn(*a, **k)
            except CreditBalanceError:
                credit_hits += 1
                cb(f"AI credit limit hit ({credit_hits}/{CREDIT_STOP}).", "err")
                if credit_hits >= CREDIT_STOP:
                    abort_credit = True
                return None

        MIN_SCORE = 2.0   # below this, there is no real keyword/kind hit — don't
                          # auto-bind (that's how a click landed on #pn_id_*_header)

        def bind_target(intent, pool):
            """Deterministic-first binding of an intent to a live element.
            Returns (element_or_None, source) with source in
            {'live','snapshot','guess'}. The AI is used ONLY to break ties among a
            short candidate list — never to invent a locator. A weak best candidate
            (below MIN_SCORE) is NOT taken silently: the AI may rescue it, else it
            becomes a guess rather than a wrong click."""
            ranked = _rank_candidates(intent, pool)
            if ranked and ranked[0][0] >= MIN_SCORE:
                top = ranked[0][0]
                second = ranked[1][0] if len(ranked) > 1 else 0.0
                if len(ranked) == 1 or (top - second) >= 1:
                    return ranked[0][1], "live"          # confident — no AI call
                shortlist = [e for _, e in ranked[:5]]
                chosen = _credit_guard(_tiebreak_with_ai, intent, shortlist, cb)
                return (chosen or ranked[0][1]), "live"  # AI tie-break, else best deterministic
            if ranked:
                # weak candidates only — let the AI decide from the shortlist; if it
                # declines, fall through rather than clicking a low-confidence guess
                chosen = _credit_guard(_tiebreak_with_ai, intent, [e for _, e in ranked[:5]], cb)
                if chosen is not None:
                    return chosen, "live"
            # nothing solid on this page → try the union of everything seen so far
            union = _dedup_reindex(all_snapshots)
            if union:
                q = intent.get("target") or " ".join(intent.get("keywords") or [])
                r = _credit_guard(_match_step_to_element, q, union, cb)
                if r and r[0]:
                    return r[0], "snapshot"
            return None, "guess"

        def assign(step_idxs, locator, src, as_assert=False):
            """Write a captured locator back onto the ORIGINAL step(s) the intent
            came from, so the generated Java still mirrors the authored test case."""
            nonlocal live_count, snap_count, guess_count
            for n in step_idxs:
                if 1 <= n <= len(steps):
                    if as_assert:
                        steps[n - 1]["assert_locator"] = locator
                    else:
                        steps[n - 1]["locator"] = locator
                        steps[n - 1]["locator_src"] = src
            if not as_assert:
                if src == "live":      live_count += 1
                elif src == "snapshot": snap_count += 1
                elif src == "guess":    guess_count += 1

        def _todo(story, tc, idxs, target, kind):
            for n in idxs:
                todos.append({"s": story.get("id"), "tc": tc.get("title", ""),
                              "n": n, "a": (target or "")[:32], "kind": kind})

        # walk each test case (intent-driven)
        for sp in stories_payload:
            if should_stop() or abort_credit:
                break
            story = sp.get("story", {})
            cb(f"\u25b8 Story {story.get('id')} \u2014 {story.get('title','')}", "story")
            for tc in sp.get("test_cases", []):
                if should_stop() or abort_credit:
                    break
                steps = tc.get("steps", []) or []
                ctype = _classify_case(tc)
                is_neg = (ctype == "negative_login")
                pctx = _infer_page_context(tc, ctype)
                cb(f"  walking '{tc.get('title','')}'  [{ctype} \u00b7 {pctx}-page]  "
                   f"({len(steps)} steps)", "info")

                # STAGE 1 — compile messy steps into typed intents (collapses
                # restated/duplicate steps, routes preconditions away from clicks)
                intents = _credit_guard(compile_test_case, tc, story, cb, ctype) or []
                if not intents:
                    intents = _intents_from_raw_steps(tc)
                n_act = sum(1 for it in intents if it["role"] == "action")
                n_ass = sum(1 for it in intents if it["role"] == "assertion")
                cb(f"    compiled \u2192 {n_act} action(s), {n_ass} assertion(s), "
                   f"{len(intents) - n_act - n_ass} precondition(s)", "dim")

                # start page — login-page cases (incl. negative-login) walk on a
                # FRESH logged-out login page, where the language dropdown exists;
                # app cases walk on the authenticated page (single toggle).
                if pctx == "login":
                    cb(f"    \u21b3 {ctype} \u2014 walking on a fresh logged-out login "
                       f"page (where the language dropdown lives)", "info")
                    if have_creds:
                        try:
                            driver.delete_all_cookies()
                        except Exception:
                            pass
                    try:
                        driver.get(login_url); wait_dom_ready(); _t.sleep(wait_secs)
                    except Exception:
                        pass
                else:
                    try:
                        cb("    loading start page (authenticated app)\u2026", "dim")
                        driver.get(site_url); _t.sleep(wait_secs)
                    except Exception:
                        pass

                last_before = None   # snapshot keys just before the latest action
                for it in intents:
                    if should_stop() or abort_credit:
                        break
                    role = it["role"]; fs = it.get("from_steps") or []

                    if role == "precondition":
                        cb(f"    \u2022 precondition (no UI action): "
                           f"{(it.get('target') or '')[:40]}", "dim")
                        for n in fs:
                            if 1 <= n <= len(steps):
                                steps[n - 1].setdefault("locator", None)
                                steps[n - 1]["locator_src"] = "precondition"
                        continue

                    if role == "assertion":
                        # STAGE 2 (assert) — bind by DOM-diff: prefer elements that
                        # newly appeared/changed since the last action (the menu that
                        # opened, the error that showed). Same mechanism powers
                        # negative-login error capture.
                        _settle(timeout=4)
                        after = snapshot(with_errors=True)
                        new_pool = ([e for e in after if _el_key(e) not in last_before]
                                    if last_before is not None else after)
                        el, src = bind_target(it, new_pool or after)
                        assign(fs, to_locator(el) if el else None, src, as_assert=True)
                        if el:
                            tag = " (new)" if (last_before is not None and
                                               _el_key(el) not in last_before) else ""
                            cb(f"    \u2713 assertion \u2192 {_describe(el)}{tag}", "ok")
                        else:
                            cb("    ? assertion target not found on page", "warn")
                        continue

                    # role == 'action'
                    verb = it.get("verb") or "click"
                    cb(f"    \u2192 {verb}: {(it.get('target') or '')[:40]}", "dim")
                    cur = snapshot(with_errors=is_neg)
                    last_before = set(_el_key(e) for e in cur)
                    el, src = bind_target(it, cur)
                    if abort_credit:
                        break
                    if el is None:
                        assign(fs, None, "guess")
                        cb("      GUESS: no element matched \u2014 // TODO verify locator", "warn")
                        _todo(story, tc, fs, it.get("target"), "guess")
                        continue
                    assign(fs, to_locator(el), src)
                    if src == "snapshot":
                        cb(f"      SNAPSHOT: using {_describe(el)} from an earlier page "
                           f"\u2014 // TODO verify (from snapshot)", "warn")
                        _todo(story, tc, fs, it.get("target"), "snapshot")
                        continue
                    if verb == "navigate":
                        cb("      (navigate) \u2014 already on the target page", "dim")
                        continue
                    # STAGE 3 — interception-proof action
                    empty_ok = (verb == "type" and not (it.get("value") or "").strip()
                                and _wants_empty_field((it.get("target", "") + " " +
                                                        " ".join(it.get("keywords") or []))))
                    _act(el, verb, it.get("value", ""), empty_ok=empty_ok)
                    cb(f"      {verb} {_describe(el)}"
                       f"{' (left empty)' if empty_ok else ''}", "ok")
                    _settle(timeout=4); _t.sleep(0.6)

                # restore the authenticated session after a login-page case so
                # later app cases don't capture locators from the login page
                if pctx == "login" and have_creds and not (should_stop() or abort_credit):
                    cb("    \u21b3 re-establishing login after login-page case\u2026", "dim")
                    try:
                        ok2, reason2 = do_login(fresh=True)
                        cb(f"    \u21b3 re-login {'verified' if ok2 else 'NOT verified'} "
                           f"\u2014 {reason2}", "ok" if ok2 else "warn")
                    except Exception as e:
                        cb(f"    \u21b3 re-login failed: {str(e)[:80]}", "warn")

        if abort_credit:
            cb(f"Stopped automatically \u2014 the AI credit limit was hit {credit_hits} "
               f"times. Top up credits or switch provider, then run again.", "err")

        cb(f"Exploration done \u2014 {live_count} exact, {snap_count} from snapshots, "
           f"{guess_count} still need a guess.", "ok")
        # summary of every step that will carry a // TODO verify locator
        if todos:
            cb(f"{len(todos)} step(s) will need a // TODO verify locator:", "warn")
            for _t2 in todos:
                cb(f"   - story {_t2['s']} / {(_t2['tc'] or '')[:26]} / step {_t2['n']}  "
                   f"[{_t2['kind']}]  {_t2['a'][:32]}", "warn")
        else:
            cb("No TODOs \u2014 every walked step captured a real locator.", "ok")
        # de-dup snapshots
        seen = set(); uniq = []
        for e in all_snapshots:
            key = (e.get("id"), e.get("name"), e.get("css"))
            if key in seen:
                continue
            seen.add(key); uniq.append(e)
        return {"stories_payload": stories_payload, "dom_snapshot": uniq[:300],
                "stats": {"live": live_count, "snapshot": snap_count,
                          "guess": guess_count}}
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def generate_test_class(story, test_cases, dom_elements, base_package, log=None):
    """AI generates ONE TestNG page-object-style test class for a story.
    `story` = {"id","title","criteria"}
    `test_cases` = [{"title","steps":[{precondition,action,expected}...]}...]
    `dom_elements` = list from scrape_dom()
    Returns {"class_name","page_class","test_code","page_code"}.
    """
    log = log or (lambda *a, **k: None)
    cls = _safe_class_name(story.get("title"), f"Story{story.get('id','')}")
    test_class = f"{cls}Test"
    page_class = f"{cls}Page"

    # Trim DOM to the most useful fields to keep the prompt tight
    dom_brief = []
    for e in dom_elements[:120]:
        dom_brief.append({k: e.get(k, "") for k in
                          ("tag", "type", "id", "name", "text", "placeholder", "aria", "css", "xpath")})

    tc_brief = []
    for tc in test_cases:
        steps = tc.get("steps") or []
        tc_brief.append({"title": tc.get("title", ""),
                         "steps": [{"action": s.get("action", ""),
                                    "expected": s.get("expected", ""),
                                    "precondition": s.get("precondition", ""),
                                    # exact locator captured by the live-walk explorer
                                    "locator": s.get("locator"),
                                    "locator_src": s.get("locator_src", ""),
                                    "assert_locator": s.get("assert_locator")} for s in steps]})

    prompt = f"""
You are a senior SDET. Generate Selenium + Java + TestNG automation using the Page Object Model.

Return the TWO Java source files using EXACTLY these delimiters and nothing else
(no markdown, no JSON, no commentary outside the blocks):

===PAGE_OBJECT===
<full Java source of the Page Object class>
===TEST_CLASS===
<full Java source of the TestNG test class>
===END===

Requirements:
- Package: {base_package}
- Page Object class name: {page_class}  (in package {base_package}.pages, extending BasePage)
- Test class name: {test_class}  (in package {base_package}.tests, extending BaseTest)
- One @Test method per test case below. Method name = camelCase of the test case title.
- FOLLOW THE STEPS EXACTLY. Each test case has an ordered list of steps with an
  "action" and an "expected". For every step:
    * translate the "action" into the matching Selenium action via a Page Object method
      (navigate, type into a field, click a button, select, etc.), in the same order;
    * if the "expected" is non-empty, add a TestNG assertion (Assert.assertTrue/assertEquals
      with isDisplayed()/getText()/etc.) verifying that outcome right after the action.
  Do NOT invent steps that aren't listed; do NOT skip listed steps. Steps may be in Arabic —
  read them and map intent to UI actions. Add a short `// <step n>: <action>` comment above
  each block so the mapping is traceable.
- LOCATORS — each step may include a "locator" captured live from the real page
  (an object like {{"by":"id|name|css|xpath","value":"..."}}) and "locator_src":
    * If "locator" is present and locator_src=="live", you MUST use that exact
      locator for the step's element — it was verified on the live page. Build the
      matching By (By.id/By.name/By.cssSelector/By.xpath).
    * "assert_locator" (when present) is the verified element for the expected
      result — use it for the assertion.
    * If "locator" is present and locator_src=="snapshot", it is a REAL locator
      captured from a page seen earlier in the walk (not the exact step page).
      Use it as-is, but add `// TODO verify locator (from snapshot)` above the line.
    * If "locator" is null, fall back to the REAL DOM list below; pick the most
      stable (id > name > css > xpath) and add `// TODO verify locator`.
- Use locators from the REAL DOM list only when a step has no captured locator.
- The Page Object exposes action methods (e.g. clickAddQuestion(), enterAnswer(String)).
- Add plain TestNG (no Allure). Include necessary imports. Code must compile.
- Add a class-level Javadoc with the story id and title.

Story:
  id: {story.get('id')}
  title: {story.get('title')}
  acceptance_criteria: {story.get('criteria','')[:1500]}

Test cases (each becomes a @Test):
{json.dumps(tc_brief, ensure_ascii=False)[:6000]}

REAL DOM elements (use these locators):
{json.dumps(dom_brief, ensure_ascii=False)[:8000]}
"""
    raw = ai_complete(prompt, max_tokens=8192)
    page_code, test_code = _split_generated_code(raw)
    return {
        "class_name": test_class,
        "page_class": page_class,
        "test_code": test_code or "// generation failed — see activity log",
        "page_code": page_code or "// generation failed — see activity log",
    }


def _split_generated_code(raw):
    """Extract page-object and test-class Java from the delimited AI output.
    Falls back to JSON parsing if the model ignored the delimiters."""
    if not raw:
        return "", ""
    txt = raw.strip()
    # Strip accidental markdown fences
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?", "", txt)
        txt = re.sub(r"\n?```$", "", txt)
    if "===PAGE_OBJECT===" in txt and "===TEST_CLASS===" in txt:
        try:
            after_po = txt.split("===PAGE_OBJECT===", 1)[1]
            page_part, rest = after_po.split("===TEST_CLASS===", 1)
            test_part = rest.split("===END===", 1)[0]
            return page_part.strip(), test_part.strip()
        except Exception:
            pass
    # Fallback: maybe the model returned JSON after all
    try:
        data = parse_json_robust(txt)
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            return data.get("page_code", ""), data.get("test_code", "")
    except Exception:
        pass
    return "", ""


# ── Static scaffolding files (base classes, pom.xml, testng.xml) ───────────────
def _pom_xml(group_id, artifact_id):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>{group_id}</groupId>
  <artifactId>{artifact_id}</artifactId>
  <version>1.0.0</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <selenium.version>4.21.0</selenium.version>
    <testng.version>7.10.2</testng.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.seleniumhq.selenium</groupId>
      <artifactId>selenium-java</artifactId>
      <version>${{selenium.version}}</version>
    </dependency>
    <dependency>
      <groupId>org.testng</groupId>
      <artifactId>testng</artifactId>
      <version>${{testng.version}}</version>
    </dependency>
    <dependency>
      <groupId>io.github.bonigarcia</groupId>
      <artifactId>webdrivermanager</artifactId>
      <version>5.9.2</version>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
        <configuration>
          <suiteXmlFiles><suiteXmlFile>testng.xml</suiteXmlFile></suiteXmlFiles>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""

def _driver_factory(pkg):
    return f"""package {pkg}.core;

import io.github.bonigarcia.wdm.WebDriverManager;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;

public final class DriverFactory {{
    private DriverFactory() {{}}

    public static WebDriver create() {{
        WebDriverManager.chromedriver().setup();
        ChromeOptions options = new ChromeOptions();
        // options.addArguments("--headless=new");
        options.addArguments("--start-maximized");
        return new ChromeDriver(options);
    }}
}}
"""

def _base_test(pkg, base_url):
    return f"""package {pkg}.tests;

import {pkg}.core.DriverFactory;
import org.openqa.selenium.WebDriver;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;

import java.time.Duration;

public abstract class BaseTest {{
    protected WebDriver driver;
    protected static final String BASE_URL = "{base_url}";

    @BeforeMethod
    public void setUp() {{
        driver = DriverFactory.create();
        driver.manage().timeouts().implicitlyWait(Duration.ofSeconds(10));
        driver.get(BASE_URL);
    }}

    @AfterMethod
    public void tearDown() {{
        if (driver != null) driver.quit();
    }}
}}
"""

def _base_page(pkg):
    return f"""package {pkg}.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.time.Duration;

public abstract class BasePage {{
    protected final WebDriver driver;
    protected final WebDriverWait wait;

    protected BasePage(WebDriver driver) {{
        this.driver = driver;
        this.wait = new WebDriverWait(driver, Duration.ofSeconds(15));
    }}

    protected WebElement visible(By by) {{
        return wait.until(ExpectedConditions.visibilityOfElementLocated(by));
    }}
    protected WebElement clickable(By by) {{
        return wait.until(ExpectedConditions.elementToBeClickable(by));
    }}
    protected void type(By by, String text) {{
        WebElement el = visible(by); el.clear(); el.sendKeys(text);
    }}
    protected void click(By by) {{ clickable(by).click(); }}
    protected String textOf(By by) {{ return visible(by).getText(); }}
    protected boolean isPresent(By by) {{
        try {{ return !driver.findElements(by).isEmpty(); }} catch (Exception e) {{ return false; }}
    }}
}}
"""

def _testng_xml(test_classes, pkg):
    items = "\n".join(f'      <class name="{pkg}.tests.{c}"/>' for c in test_classes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="QA Studio Automation Suite" verbose="1">
  <test name="Sprint Tests">
    <classes>
{items}
    </classes>
  </test>
</suite>
"""


def build_automation_project(out_dir, stories_payload, dom_elements, base_url,
                             group_id="com.qastudio", artifact_id="automation-tests",
                             cb=None, should_stop=lambda: False):
    """Generate a full Maven TestNG+POM project under out_dir.
    `stories_payload` = [{"story": {...}, "test_cases": [...]}, ...]
    Returns list of relative file paths written.
    """
    cb = cb or (lambda *a, **k: None)
    pkg = group_id
    pkg_path = pkg.replace(".", "/")
    src_main = os.path.join(out_dir, "src", "main", "java", pkg_path)
    src_test = os.path.join(out_dir, "src", "test", "java", pkg_path)
    os.makedirs(os.path.join(src_main, "core"), exist_ok=True)
    os.makedirs(os.path.join(src_main, "pages"), exist_ok=True)
    os.makedirs(os.path.join(src_test, "tests"), exist_ok=True)

    written = []
    def _w(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(os.path.relpath(path, out_dir))

    # scaffolding
    cb("Writing project scaffolding (pom.xml, base classes)…", "dim")
    _w(os.path.join(out_dir, "pom.xml"), _pom_xml(group_id, artifact_id))
    _w(os.path.join(src_main, "core", "DriverFactory.java"), _driver_factory(pkg))
    _w(os.path.join(src_main, "pages", "BasePage.java"), _base_page(pkg))
    _w(os.path.join(src_test, "tests", "BaseTest.java"), _base_test(pkg, base_url))

    test_classes = []
    total = len(stories_payload)
    for i, item in enumerate(stories_payload, 1):
        if should_stop():
            break
        story = item["story"]; tcs = item["test_cases"]
        cb(f"[{i}/{total}] Generating tests for story {story.get('id')} — {story.get('title','')}", "story")
        try:
            gen = generate_test_class(story, tcs, dom_elements, pkg,
                                      log=lambda m, t="warn": cb(m, t))
            _w(os.path.join(src_main, "pages", gen["page_class"] + ".java"), gen["page_code"])
            _w(os.path.join(src_test, "tests", gen["class_name"] + ".java"), gen["test_code"])
            test_classes.append(gen["class_name"])
            cb(f"  ✓ {gen['class_name']} ({len(tcs)} test method"
               + ("s" if len(tcs) != 1 else "") + ")", "ok")
        except CreditBalanceError:
            cb("Out of AI credits — stopping generation.", "err")
            break
        except Exception as e:
            cb(f"  ✗ story {story.get('id')} failed: {e}", "err")

    # testng.xml referencing whatever classes succeeded
    _w(os.path.join(out_dir, "testng.xml"), _testng_xml(test_classes, pkg))
    cb(f"Project ready — {len(test_classes)} test class(es).", "ok")
    return written


# ── Incremental generation: survive prior stories, append only new test cases ──
def _manifest_path(project_dir):
    return os.path.join(project_dir, ".qastudio", "manifest.json")

def load_manifest(project_dir):
    """Read the per-project manifest that records which stories/test-cases have
    already been generated. Missing/corrupt → empty manifest."""
    try:
        with open(_manifest_path(project_dir), "r", encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        m = {}
    if not isinstance(m, dict):
        m = {}
    m.setdefault("stories", {})
    return m

def save_manifest(project_dir, m):
    try:
        os.makedirs(os.path.dirname(_manifest_path(project_dir)), exist_ok=True)
        with open(_manifest_path(project_dir), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _tc_key(tc):
    """Stable identity for a test case: Azure id if present, else a title slug."""
    k = tc.get("id")
    if k is not None and str(k).strip():
        return str(k)
    return "t_" + _safe_class_name(tc.get("title", ""), "TC")

def _method_name(title):
    n = _safe_class_name(title, "test")
    return (n[:1].lower() + n[1:]) if n else "test"

def classify_selection(project_dir, stories_payload):
    """Compare the current selection against the on-disk manifest. Returns
    (new, grew, done, new_tcs):
      new  = story ids never generated here
      grew = story ids already generated that have NEW test cases to add
      done = story ids already generated with nothing new
      new_tcs = {story_id: [fresh test-case dicts]} for the grew set
    All ids are strings."""
    m = load_manifest(project_dir)
    new, grew, done, new_tcs = [], [], [], {}
    for sp in stories_payload:
        sid = str(sp.get("story", {}).get("id"))
        rec = m["stories"].get(sid)
        tcs = sp.get("test_cases", []) or []
        if not rec:
            new.append(sid); continue
        have = set(rec.get("test_cases", {}).keys())
        fresh = [tc for tc in tcs if _tc_key(tc) not in have]
        if fresh:
            grew.append(sid); new_tcs[sid] = fresh
        else:
            done.append(sid)
    return new, grew, done, new_tcs

def _splice_members(java_src, new_members, new_imports=""):
    """Append new methods/fields into an existing Java class WITHOUT touching the
    code already there: merge any missing imports, then insert the new members
    just before the class's final closing brace."""
    if not java_src:
        return java_src
    src = java_src
    if new_imports and new_imports.strip():
        want = [ln.strip() for ln in new_imports.splitlines() if ln.strip().startswith("import ")]
        have = set(ln.strip() for ln in src.splitlines() if ln.strip().startswith("import "))
        add = [ln for ln in want if ln not in have]
        if add:
            lines = src.splitlines()
            idx = 0
            for i, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith("import "):
                    idx = i + 1
                elif s.startswith("package ") and idx == 0:
                    idx = i + 1
            lines[idx:idx] = add
            src = "\n".join(lines)
    if new_members and new_members.strip():
        pos = src.rfind("}")
        if pos != -1:
            src = src[:pos] + "\n" + new_members.rstrip() + "\n}\n" + src[pos + 1:].lstrip()
        else:
            src = src + "\n" + new_members
    return src

def generate_additional_methods(story, new_test_cases, dom_elements, base_package,
                                page_class, test_class, existing_methods=None, log=None):
    """Ask the AI for ONLY the Java members needed by NEW test cases, so they can
    be spliced into the already-generated classes without regenerating (and thus
    re-evaluating) the existing methods."""
    log = log or (lambda *a, **k: None)
    existing_methods = existing_methods or []
    dom_brief = [{k: e.get(k, "") for k in
                  ("tag", "type", "id", "name", "text", "placeholder", "aria", "css", "xpath")}
                 for e in (dom_elements or [])[:120]]
    tc_brief = []
    for tc in new_test_cases:
        steps = tc.get("steps") or []
        tc_brief.append({"title": tc.get("title", ""),
                         "steps": [{"action": s.get("action", ""),
                                    "expected": s.get("expected", ""),
                                    "precondition": s.get("precondition", ""),
                                    "locator": s.get("locator"),
                                    "locator_src": s.get("locator_src", ""),
                                    "assert_locator": s.get("assert_locator")} for s in steps]})
    prompt = f"""
You are a senior SDET EXTENDING an existing Selenium + Java + TestNG project (Page Object Model).
Do NOT regenerate or restate existing code. Output ONLY the new members to append.

Existing classes (already on disk - reference only, never rewrite):
  Page Object: {base_package}.pages.{page_class}  (extends BasePage)
  Test class : {base_package}.tests.{test_class}  (extends BaseTest)
Existing @Test methods you must NOT redefine: {json.dumps(existing_methods, ensure_ascii=False)}

Return EXACTLY these delimited blocks and nothing else (no markdown, no commentary):

===TEST_IMPORTS===
<extra import lines the new test methods need, or empty>
===TEST_METHODS===
<one new @Test method per new test case - method declarations only, NO class wrapper>
===PAGE_IMPORTS===
<extra import lines the new page methods need, or empty>
===PAGE_METHODS===
<new Page Object methods / By fields the new tests call - NO class wrapper>
===END===

Rules:
- One @Test per new test case below; name = camelCase of its title; if it collides
  with an existing name, suffix a number.
- Follow each step in order; add a TestNG assertion when "expected" is non-empty.
- Use a step's "locator" exactly when locator_src=="live". If locator_src=="snapshot",
  use it but add `// TODO verify locator (from snapshot)`. If null, use the REAL DOM
  list and add `// TODO verify locator`.
- Reuse existing Page Object methods where possible; only add page members that are new.
- Members must compile when pasted into the existing classes.

Story: id={story.get('id')} title={story.get('title')}
New test cases:
{json.dumps(tc_brief, ensure_ascii=False)[:6000]}
REAL DOM elements:
{json.dumps(dom_brief, ensure_ascii=False)[:6000]}
"""
    raw = ai_complete(prompt, max_tokens=6000) or ""
    def _blk(a, b):
        try:
            return raw.split(a, 1)[1].split(b, 1)[0].strip()
        except Exception:
            return ""
    return {
        "test_imports": _blk("===TEST_IMPORTS===", "===TEST_METHODS==="),
        "test_methods": _blk("===TEST_METHODS===", "===PAGE_IMPORTS==="),
        "page_imports": _blk("===PAGE_IMPORTS===", "===PAGE_METHODS==="),
        "page_methods": _blk("===PAGE_METHODS===", "===END==="),
    }

def build_or_merge_project(out_dir, stories_payload, dom_elements, base_url,
                           reeval_ids=None, group_id="com.qastudio",
                           artifact_id="automation-tests", cb=None,
                           should_stop=lambda: False):
    """Incrementally build/extend a Maven project in `out_dir` (the user's local
    folder, which persists across runs). Stories already in the manifest are KEPT;
    only brand-new stories, newly-added test cases, or stories whose id is in
    `reeval_ids` are sent to the AI. Scaffolding is written only when missing, so a
    working tree is never clobbered. Returns a summary dict."""
    cb = cb or (lambda *a, **k: None)
    reeval_ids = set(str(x) for x in (reeval_ids or []))
    pkg = group_id; pkg_path = pkg.replace(".", "/")
    src_main = os.path.join(out_dir, "src", "main", "java", pkg_path)
    src_test = os.path.join(out_dir, "src", "test", "java", pkg_path)
    os.makedirs(os.path.join(src_main, "core"), exist_ok=True)
    os.makedirs(os.path.join(src_main, "pages"), exist_ok=True)
    os.makedirs(os.path.join(src_test, "tests"), exist_ok=True)

    def _w(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    for p, c in (
        (os.path.join(out_dir, "pom.xml"), _pom_xml(group_id, artifact_id)),
        (os.path.join(src_main, "core", "DriverFactory.java"), _driver_factory(pkg)),
        (os.path.join(src_main, "pages", "BasePage.java"), _base_page(pkg)),
        (os.path.join(src_test, "tests", "BaseTest.java"), _base_test(pkg, base_url)),
    ):
        if not os.path.exists(p):
            _w(p, c)

    m = load_manifest(out_dir)
    counts = {"new": 0, "extended": 0, "reeval": 0, "kept": 0, "added_methods": 0}

    def _persist_progress():
        # Save the manifest + testng.xml as they stand so a stop / pause / app-close
        # keeps every story already written to disk. A later re-run reads the
        # manifest and resumes (regenerating only what's missing) instead of
        # starting from scratch.
        try:
            _dc = sorted({r.get("test_class") for r in m["stories"].values()
                          if r.get("test_class")})
            _w(os.path.join(out_dir, "testng.xml"), _testng_xml(_dc, pkg))
            save_manifest(out_dir, m)
        except Exception:
            pass

    for item in stories_payload:
        # Persist BEFORE the stop check so a story finished on the previous pass is
        # never lost to a stop that lands between stories.
        _persist_progress()
        if should_stop():
            break
        story = item["story"]; tcs = item.get("test_cases", []) or []
        sid = str(story.get("id"))
        rec = m["stories"].get(sid)

        # full (re)generation: brand-new story OR an explicit re-evaluation
        if rec is None or sid in reeval_ids:
            mode = "reeval" if rec is not None else "new"
            cb(f"[{mode}] story {sid} - {story.get('title','')}", "story")
            try:
                gen = generate_test_class(story, tcs, dom_elements, pkg,
                                          log=lambda mm, t="warn": cb(mm, t))
                _w(os.path.join(src_main, "pages", gen["page_class"] + ".java"), gen["page_code"])
                _w(os.path.join(src_test, "tests", gen["class_name"] + ".java"), gen["test_code"])
                m["stories"][sid] = {
                    "page_class": gen["page_class"],
                    "test_class": gen["class_name"],
                    "test_cases": {_tc_key(tc): _method_name(tc.get("title", "")) for tc in tcs},
                }
                counts["reeval" if mode == "reeval" else "new"] += 1
                cb(f"  ok {gen['class_name']} ({len(tcs)} test method(s))", "ok")
            except CreditBalanceError:
                cb("Out of AI credits - stopping.", "err"); break
            except Exception as e:
                cb(f"  x story {sid} failed: {e}", "err")
            continue

        # existing story: keep methods, append only the NEW test cases
        have = set(rec.get("test_cases", {}).keys())
        fresh = [tc for tc in tcs if _tc_key(tc) not in have]
        if not fresh:
            counts["kept"] += 1
            cb(f"[keep] story {sid} - already complete, nothing new.", "dim")
            continue

        cb(f"[extend] story {sid} - appending {len(fresh)} new test case(s).", "story")
        test_file = os.path.join(src_test, "tests", rec["test_class"] + ".java")
        page_file = os.path.join(src_main, "pages", rec["page_class"] + ".java")
        try:
            with open(test_file, "r", encoding="utf-8") as f:
                test_src = f.read()
            with open(page_file, "r", encoding="utf-8") as f:
                page_src = f.read()
        except Exception as e:
            cb(f"  ! files for {sid} missing ({e}) - regenerating the whole story.", "warn")
            try:
                gen = generate_test_class(story, tcs, dom_elements, pkg,
                                          log=lambda mm, t="warn": cb(mm, t))
                _w(page_file, gen["page_code"]); _w(test_file, gen["test_code"])
                m["stories"][sid]["test_cases"] = {_tc_key(tc): _method_name(tc.get("title", "")) for tc in tcs}
                m["stories"][sid]["page_class"] = gen["page_class"]
                m["stories"][sid]["test_class"] = gen["class_name"]
                counts["reeval"] += 1
            except Exception as e2:
                cb(f"  x regen failed for {sid}: {e2}", "err")
            continue

        try:
            add = generate_additional_methods(
                story, fresh, dom_elements, pkg, rec["page_class"], rec["test_class"],
                existing_methods=list(rec.get("test_cases", {}).values()),
                log=lambda mm, t="warn": cb(mm, t))
        except CreditBalanceError:
            cb("Out of AI credits - stopping.", "err"); break
        except Exception as e:
            cb(f"  x new-method generation failed for {sid}: {e}", "err"); continue

        if not (add.get("test_methods") or "").strip():
            cb(f"  ! AI returned no new methods for {sid} - skipped (existing kept).", "warn")
            continue
        test_src = _splice_members(test_src, add.get("test_methods", ""), add.get("test_imports", ""))
        page_src = _splice_members(page_src, add.get("page_methods", ""), add.get("page_imports", ""))
        _w(test_file, test_src); _w(page_file, page_src)
        for tc in fresh:
            m["stories"][sid].setdefault("test_cases", {})[_tc_key(tc)] = _method_name(tc.get("title", ""))
        counts["extended"] += 1; counts["added_methods"] += len(fresh)
        cb(f"  ok appended {len(fresh)} method(s) to {rec['test_class']}", "ok")

    # testng.xml across EVERY recorded class so a push carries all stories, not just this run
    all_classes = sorted({r.get("test_class") for r in m["stories"].values() if r.get("test_class")})
    _w(os.path.join(out_dir, "testng.xml"), _testng_xml(all_classes, pkg))
    save_manifest(out_dir, m)
    counts["classes_total"] = len(all_classes)
    cb(f"Project updated - {counts['new']} new, {counts['extended']} extended "
       f"(+{counts['added_methods']} methods), {counts['reeval']} re-evaluated, "
       f"{counts['kept']} kept. {counts['classes_total']} class(es) total.", "ok")
    return counts


def _validate_remote_url(url):
    """Validate a git remote URL before we hand it to git, so a wrong URL gives a
    clear message (and a suggested fix) instead of a cryptic git failure.
    Returns (ok: bool, message_or_suggestion: str)."""
    import urllib.parse
    u = (url or "").strip()
    if not u:
        return (False, "Repository URL is empty. Paste your repo URL, e.g. "
                       "https://github.com/owner/repo.git")
    # SSH remotes (git@host:owner/repo.git or ssh://…) — accept as-is.
    if u.startswith("git@") or u.startswith("ssh://"):
        return (True, u)
    if not (u.startswith("https://") or u.startswith("http://")):
        return (False, "Repository URL must start with https:// (or be an SSH "
                       f"git@ URL). Got: {u[:70]}")
    try:
        p = urllib.parse.urlparse(u)
    except Exception:
        return (False, f"Repository URL could not be parsed: {u[:70]}")
    if not p.netloc or "." not in p.netloc:
        return (False, f"Repository URL has no valid host: {u[:70]}")
    segs = [s for s in p.path.split("/") if s]
    if len(segs) < 2:
        return (False, "Repository URL is missing the owner/repo, e.g. "
                       f"https://{p.netloc}/<owner>/<repo>.git")
    owner, repo = segs[0], segs[1]
    clean_repo = repo.split(".git")[0] if ".git" in repo else repo
    suggestion = f"https://{p.netloc}/{owner}/{clean_repo}.git"
    host = p.netloc.lower()
    github_like = any(h in host for h in ("github.com", "gitlab.com", "bitbucket.org"))
    # ".git" appearing anywhere but the end (e.g. ".gitm", ".git/extra") is malformed
    path_no_slash = p.path.rstrip("/")
    bad_git = (".git" in path_no_slash and not path_no_slash.endswith(".git"))
    # GitHub/GitLab repos are exactly owner/repo; extra path segments are wrong
    extra_segs = github_like and len(segs) > 2
    if bad_git or extra_segs:
        return (False, "Repository URL looks malformed (check for a typo like "
                       "'.gitm' or extra text after the repo name). Try: " + suggestion)
    return (True, u)


def push_to_git(repo_dir, remote_url, token, branch="main", message="Add QA Studio automation tests", cb=None, force=False):
    """Init/commit/push the generated project to a Git remote using the git CLI.
    `token` is embedded into the HTTPS URL for auth (GitHub/Azure DevOps style).
    Returns (ok, message).
    """
    cb = cb or (lambda *a, **k: None)
    import subprocess

    # --- pre-flight: the folder must exist and contain the generated project ---
    if not repo_dir or not os.path.isdir(repo_dir):
        return False, f"Project folder not found: {repo_dir or '(blank)'}. Generate scripts first."
    try:
        entries = [e for e in os.listdir(repo_dir) if e != ".git"]
    except Exception:
        entries = []
    if not entries:
        return False, ("Project folder is empty — nothing to push. Generate the "
                       "automation scripts to this folder first.")

    # --- pre-flight: validate the remote URL with a friendly message ---
    ok_url, url_msg = _validate_remote_url(remote_url)
    if not ok_url:
        return False, url_msg
    remote_url = url_msg if url_msg.startswith(("http", "git@", "ssh://")) else remote_url

    def run(args, **kw):
        return subprocess.run(args, cwd=repo_dir, capture_output=True, text=True, **kw)

    # check git is available
    try:
        v = subprocess.run(["git", "--version"], capture_output=True, text=True)
        if v.returncode != 0:
            return False, "Git is not installed on this machine."
    except Exception:
        return False, "Git is not installed or not on PATH."

    # build authenticated URL (https://<token>@host/path.git)
    auth_url = remote_url
    try:
        if remote_url.startswith("https://") and token:
            auth_url = remote_url.replace("https://", f"https://{token}@", 1)
    except Exception:
        pass

    cb("Initializing git repository…", "dim")
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        run(["git", "init"])
    run(["git", "checkout", "-B", branch])
    # write a .gitignore for Maven/IntelliJ noise
    try:
        with open(os.path.join(repo_dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("target/\n.idea/\n*.iml\n.DS_Store\n")
    except Exception:
        pass
    run(["git", "add", "-A"])
    # ensure identity exists (use a neutral default if unset)
    if run(["git", "config", "user.email"]).stdout.strip() == "":
        run(["git", "config", "user.email", "qastudio@local"])
        run(["git", "config", "user.name", "QA Studio"])
    c = run(["git", "commit", "-m", message])
    if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr).lower():
        cb(c.stdout + c.stderr, "warn")

    # set remote
    run(["git", "remote", "remove", "origin"])
    run(["git", "remote", "add", "origin", auth_url])

    cb(f"Pushing to {branch}…", "dim")
    _push = ["git", "push", "-u", "origin", branch]
    if force:
        _push.append("--force")
    p = run(_push)
    out = (p.stdout + p.stderr)
    # scrub token from any echoed output
    if token:
        out = out.replace(token, "***")
    # SECURITY: `git remote add` wrote the authenticated URL (with the PAT) into
    # .git/config. Reset origin to the token-less URL so the secret is not left in
    # plaintext on disk after the push.
    try:
        if auth_url != remote_url:
            run(["git", "remote", "set-url", "origin", remote_url])
    except Exception:
        pass
    if p.returncode == 0:
        cb("Push complete.", "ok")
        return True, "Pushed successfully."
    return False, out.strip()[:400] or "git push failed."


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO-UPDATE — compare a VERSION file in the GitHub repo to the local one
# ═══════════════════════════════════════════════════════════════════════════════
GITHUB_OWNER  = "AhmedSayedRepo"
GITHUB_REPO   = "qa-studio"
GITHUB_BRANCH = "main"

def _github_token():
    """Optional token for private-repo update checks. Read from the env var
    QA_STUDIO_GH_TOKEN or GITHUB_TOKEN, or a 'gh_token.txt' file next to the app.
    Without it, update checks only work for a PUBLIC repo."""
    for var in ("QA_STUDIO_GH_TOKEN", "GITHUB_TOKEN"):
        t = (os.environ.get(var) or "").strip()
        if t:
            return t
    for base in (_exe_dir(), _app_dir()):
        try:
            with open(os.path.join(base, "gh_token.txt"), "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass
    return ""

def _app_dir():
    return os.path.dirname(os.path.abspath(__file__))

def _exe_dir():
    """Folder of the running program: the .exe's folder when frozen, else source."""
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _app_dir()

def _resource_dir():
    """Where bundled read-only files (e.g. VERSION) live. For a PyInstaller/flet
    onefile build that's sys._MEIPASS; otherwise the source folder."""
    import sys
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", _exe_dir())
    return _app_dir()

def _clean_ver(s):
    """Tolerate a VERSION file written as UTF-16 (e.g. PowerShell `echo x > VERSION`),
    which adds a BOM and a null byte between characters. Strip BOM, nulls, and any
    non [digit/dot] noise so '\\ufeff1\\x00.\\x008\\x00.\\x004' still reads as '1.8.4'."""
    s = (s or "").replace("\x00", "").lstrip("\ufeff\ufffe").strip()
    m = re.search(r"\d+(?:\.\d+){0,3}", s)
    return m.group(0) if m else s

def local_version():
    """Read the local VERSION file (next to this module). Returns str or '0.0.0'."""
    try:
        with open(os.path.join(_resource_dir(), "VERSION"), "rb") as f:
            raw = f.read().decode("utf-8-sig", "ignore")
        return _clean_ver(raw) or "0.0.0"
    except Exception:
        return "0.0.0"

def _parse_ver(v):
    """'1.2.0' -> (1,2,0); tolerant of extra/missing parts."""
    parts = re.findall(r"\d+", str(v))
    nums = [int(p) for p in parts[:4]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

def _ver_newer(remote, local):
    return _parse_ver(remote) > _parse_ver(local)

def check_for_update(timeout=6):
    """Fetch the repo's VERSION file and compare to local.
    Returns dict: {"update": bool, "local": str, "remote": str|None, "error": str|None}.
    Network failures are swallowed (update=False) so startup is never blocked.

    Sources tried in order:
      1) GitHub API contents endpoint (works for private repos when a token is set)
      2) Cache-busted raw.githubusercontent.com URL (public repos)
    A token (see _github_token) is sent when available so PRIVATE repos work.
    """
    import time as _t, base64 as _b64
    local = local_version()
    bust = int(_t.time())
    token = _github_token()

    def _auth_headers(extra):
        h = dict(extra)
        if token:
            h["Authorization"] = f"Bearer {token}"
            h["X-GitHub-Api-Version"] = "2022-11-28"
        return h

    def _via_api():
        url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/"
               f"contents/VERSION?ref={GITHUB_BRANCH}")
        r = requests.get(url, timeout=timeout, headers=_auth_headers({
            "Accept": "application/vnd.github.raw+json",
            "Cache-Control": "no-cache",
        }))
        if r.status_code == 404:
            raise RuntimeError("API 404 — repo or VERSION file not found "
                               "(private repo needs a token; or commit a VERSION file).")
        if r.status_code in (401, 403):
            raise RuntimeError(f"API {r.status_code} — auth/rate-limit; set a valid token.")
        if r.status_code != 200:
            raise RuntimeError(f"API HTTP {r.status_code}")
        txt = r.text or ""
        if txt.lstrip().startswith("{"):
            import json as _json
            data = _json.loads(txt)
            txt = _b64.b64decode(data.get("content", "")).decode("utf-8", "ignore")
        return _clean_ver(txt)

    def _via_raw():
        url = (f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
               f"{GITHUB_BRANCH}/VERSION?cb={bust}")
        r = requests.get(url, timeout=timeout, headers=_auth_headers(
            {"Cache-Control": "no-cache", "Pragma": "no-cache"}))
        if r.status_code == 404:
            raise RuntimeError("raw 404 — VERSION file missing at repo root (or private repo).")
        if r.status_code != 200:
            raise RuntimeError(f"raw HTTP {r.status_code}")
        return _clean_ver(r.text or "")

    remote = None
    err = None
    for fetch in (_via_api, _via_raw):
        try:
            remote = fetch()
            if remote:
                break
        except Exception as e:
            err = str(e)[:160]
            continue
    if not remote:
        return {"update": False, "local": local, "remote": None, "error": err}
    return {"update": _ver_newer(remote, local), "local": local,
            "remote": remote, "error": None}

_RESTART_BAT = r'''@echo off
set "PID=__PID__"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
ping -n 2 127.0.0.1 >nul
__LAUNCH__
del "%~f0" >nul 2>&1
'''

def schedule_restart():
    """Arm a detached helper that waits for THIS process to exit, then relaunches
    QA Studio (the exe when frozen, else `pythonw main.py`). Returns True if armed."""
    import sys, tempfile, subprocess
    try:
        if getattr(sys, "frozen", False):
            launch = f'start "" "{os.path.abspath(sys.executable)}"'
        else:
            pyw = os.path.abspath(sys.executable)
            mainpy = os.path.join(_app_dir(), "main.py")
            launch = f'start "" /d "{_app_dir()}" "{pyw}" "{mainpy}"'
        bat = os.path.join(tempfile.gettempdir(), "qastudio_restart.bat")
        script = _RESTART_BAT.replace("__PID__", str(os.getpid())).replace("__LAUNCH__", launch)
        with open(bat, "w", encoding="ascii", errors="ignore", newline="\r\n") as f:
            f.write(script)
        DETACHED, NEW_GROUP, NO_WINDOW = 0x00000008, 0x00000200, 0x08000000
        subprocess.Popen(["cmd", "/c", bat],
                         creationflags=DETACHED | NEW_GROUP | NO_WINDOW, close_fds=True)
        return True
    except Exception:
        return False

_SWAP_BAT = r'''@echo off
set "PID=__PID__"
set "NEW=__NEW__"
set "CUR=__CUR__"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
set "n=0"
:try
ping -n 2 127.0.0.1 >nul
copy /y "%NEW%" "%CUR%" >nul && goto done
set /a n+=1
if %n% lss 20 goto try
:done
del "%NEW%" >nul 2>&1
start "" "%CUR%"
del "%~f0" >nul 2>&1
'''

def _latest_release(timeout=6):
    """Return (tag, (asset_name, asset_url), sums) for the newest GitHub release.
    `asset` is None when the release has no .exe attached; `sums` is the
    (name, url) of a published SHA-256 checksum file (SHA256SUMS / *.sha256), or
    None when the release doesn't publish one."""
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    data = r.json()
    tag = (data.get("tag_name") or "").lstrip("vV")
    asset = None
    sums = None
    for a in data.get("assets", []):
        nm = str(a.get("name", "")).lower()
        if asset is None and nm.endswith(".exe"):
            asset = (a["name"], a["browser_download_url"])
        if sums is None and (nm in ("sha256sums", "sha256sums.txt", "checksums.txt")
                             or nm.endswith(".sha256")):
            sums = (a["name"], a["browser_download_url"])
    return tag, asset, sums


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_download(path, name, sums, headers, cb):
    """Verify a downloaded artifact against the release's published SHA-256.
    Enforced when a checksum file exists; if none is published the download
    proceeds but is flagged as unverified (so existing releases keep working)."""
    if not sums:
        cb("Note: this update is not checksum-verified (no SHA256SUMS published "
           "in the release).", "warn")
        return True, ""
    try:
        sr = requests.get(sums[1], timeout=30, headers=headers)
        sr.raise_for_status()
        text = sr.text
    except Exception as e:
        return False, f"Couldn't fetch the release checksum file: {str(e)[:140]}"
    want = None
    for line in text.splitlines():
        parts = line.replace("*", " ").split()
        if len(parts) >= 2 and parts[1].lower().endswith(name.lower()):
            want = parts[0].lower()
            break
        if len(parts) == 1 and len(parts[0]) == 64:   # bare single-asset hash
            want = parts[0].lower()
    if not want:
        return False, "The release checksum file has no entry for this download."
    if _sha256_file(path).lower() != want:
        return False, ("Checksum mismatch — the download may be corrupted or "
                       "tampered with. Update aborted.")
    cb("Checksum verified.", "ok")
    return True, ""

def _apply_update_exe(cb):
    """Frozen-build updater: download the new .exe, then hand off to a detached
    .bat that waits for THIS process to exit, swaps the file, and relaunches."""
    import sys, time, tempfile, subprocess
    cb = cb or (lambda *a, **k: None)
    try:
        tag, asset, sums = _latest_release()
    except Exception as e:
        return (False, f"Couldn't reach GitHub releases: {str(e)[:160]}")
    if not asset:
        return (False, "The latest release has no .exe attached. Upload the built "
                       "exe as a release asset so the app can self-update.")
    _name, dl_url = asset
    cur = os.path.abspath(sys.executable)
    new = os.path.join(tempfile.gettempdir(), f"qastudio_new_{int(time.time())}.exe")
    cb("Downloading the new version…", "dim")
    try:
        headers = {}
        token = _github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with requests.get(dl_url, stream=True, timeout=120, headers=headers) as r:
            r.raise_for_status()
            with open(new, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        return (False, f"Download failed: {str(e)[:160]}")
    # SECURITY: verify the downloaded binary against the release SHA-256 before we
    # ever swap/execute it. Aborts on mismatch; warns (but proceeds) if the release
    # publishes no checksum, so existing releases keep updating.
    ok_v, vmsg = _verify_download(new, _name, sums, headers, cb)
    if not ok_v:
        try: os.remove(new)
        except Exception: pass
        return (False, vmsg)
    bat = os.path.join(tempfile.gettempdir(), "qastudio_update.bat")
    script = (_SWAP_BAT.replace("__PID__", str(os.getpid()))
                       .replace("__NEW__", new).replace("__CUR__", cur))
    try:
        with open(bat, "w", encoding="ascii", errors="ignore", newline="\r\n") as f:
            f.write(script)
    except Exception as e:
        return (False, f"Couldn't write the updater helper: {str(e)[:160]}")
    DETACHED, NEW_GROUP, NO_WINDOW = 0x00000008, 0x00000200, 0x08000000
    try:
        subprocess.Popen(["cmd", "/c", bat],
                         creationflags=DETACHED | NEW_GROUP | NO_WINDOW,
                         close_fds=True)
    except Exception as e:
        return (False, f"Couldn't start the updater: {str(e)[:160]}")
    cb("Update ready.", "ok")
    return (True, "Update downloaded. Close QA Studio and it will reopen on the "
                  "new version automatically.")

def _latest_zipball(timeout=6):
    """Return (tag_name, zipball_url) for the newest release (source archive)."""
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    data = r.json()
    return (data.get("tag_name") or ""), (data.get("zipball_url") or "")

def _apply_update_zip(cb):
    """Source (non-git) updater: download the latest release's source zip and copy
    it over the app folder in place, then reinstall deps. Used by ZIP/.bat
    installs that aren't git clones and aren't frozen exes."""
    import sys, tempfile, zipfile, shutil, subprocess
    cb = cb or (lambda *a, **k: None)
    try:
        tag, zb = _latest_zipball()
    except Exception as e:
        return (False, f"Couldn't reach GitHub releases: {str(e)[:160]}")
    if not zb:
        return (False, "No release archive found — publish a release on GitHub first.")
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tmp = tempfile.mkdtemp(prefix="qastudio_up_")
    zpath = os.path.join(tmp, "src.zip")
    cb("Downloading the latest version…", "dim")
    try:
        with requests.get(zb, headers=headers, stream=True, timeout=180,
                          allow_redirects=True) as r:
            r.raise_for_status()
            with open(zpath, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return (False, f"Download failed: {str(e)[:160]}")
    cb("Installing…", "dim")
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return (False, f"Couldn't unpack the update: {str(e)[:160]}")
    roots = [os.path.join(tmp, d) for d in os.listdir(tmp)
             if os.path.isdir(os.path.join(tmp, d))]
    if not roots:
        shutil.rmtree(tmp, ignore_errors=True)
        return (False, "Update archive was empty.")
    src_root, dst = roots[0], _app_dir()
    try:
        for name in os.listdir(src_root):
            s = os.path.join(src_root, name)
            d = os.path.join(dst, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return (False, f"Couldn't write update files: {str(e)[:160]}. "
                       f"Close the app and try again.")
    shutil.rmtree(tmp, ignore_errors=True)
    # best-effort: install any new dependencies the update introduced
    try:
        req = os.path.join(dst, "requirements.txt")
        if os.path.exists(req):
            cb("Updating dependencies…", "dim")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req,
                            "--disable-pip-version-check"],
                           creationflags=0x08000000, timeout=300)
    except Exception:
        pass
    cb("Update installed.", "ok")
    return (True, "Updated to the latest version.")

def apply_update(cb=None):
    """Self-update. For a frozen .exe build, download + swap the binary; for a
    source/git clone, `git pull`. Returns (ok, message).
    """
    import sys
    cb = cb or (lambda *a, **k: None)
    if getattr(sys, "frozen", False):
        return _apply_update_exe(cb)
    import subprocess
    d = _app_dir()
    if not os.path.isdir(os.path.join(d, ".git")):
        return _apply_update_zip(cb)
    try:
        v = subprocess.run(["git", "--version"], capture_output=True, text=True)
        if v.returncode != 0:
            return (False, "Git isn't installed, so the app can't self-update. "
                           "Download the latest version from GitHub.")
    except Exception:
        return (False, "Git isn't installed. Download the latest version from GitHub.")

    def run(args):
        return subprocess.run(args, cwd=d, capture_output=True, text=True)

    cb("Fetching the latest version…", "dim")
    # discard local edits to tracked files so pull can't conflict, then pull
    run(["git", "stash", "--include-untracked"])
    p = run(["git", "pull", "--ff-only", "origin", GITHUB_BRANCH])
    out = (p.stdout + p.stderr).strip()
    if p.returncode != 0:
        # try a non-ff pull as a fallback
        p2 = run(["git", "pull", "origin", GITHUB_BRANCH])
        out = (p2.stdout + p2.stderr).strip()
        if p2.returncode != 0:
            return (False, out[:300] or "git pull failed.")
    cb("Update downloaded.", "ok")
    return (True, "Updated. Please restart QA Studio to use the new version.")

# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-HEALING AUTOMATION (no live browser in QA Studio)
#  The Automation screen compiles each story's cases into intents, orders them
#  into a logical sequence (logged-out negatives/validation/login-page cases →
#  successful login → app cases), and GENERATES a Maven/TestNG/Selenium project
#  whose runtime heals locators by calling the Anthropic API when a seed locator
#  fails. QA Studio never drives the browser; IntelliJ runs `mvn test` and the
#  generated framework self-heals + caches locators.
# ═══════════════════════════════════════════════════════════════════════════════

def validate_and_sequence_suite(stories_payload, log=None, want_ai=True,
                                should_stop=lambda: False, on_error=None, gate=None):
    """Validate/repair each test case for automatability and order the suite.

    Pause/stop hooks:
      should_stop()  -> True to abort.
      gate()         -> called per case; blocks while the user paused; returns
                        False if stopping (so we abort cleanly).
      on_error(msg)  -> called on a recoverable AI error (e.g. low credit). It
                        blocks until the user switches provider + resumes
                        ('retry') or stops ('stop'); we retry the compile on
                        'retry' so the new provider takes effect.

    Ordering buckets (so we never log out to re-test invalids):
      0  login-page negative/validation  (logged OUT)
      1  login-page presence/interaction (logged OUT, e.g. language dropdown)
      2  the successful-login case        (transition; synthesized if absent)
      3  app cases                        (logged IN; e.g. language toggle)"""
    log = log or (lambda *a, **k: None)
    out = []
    for sp in stories_payload:
        if should_stop():
            return out
        story = sp.get("story", {})
        cases = []
        has_app = False
        has_positive_login = False
        for tc in sp.get("test_cases", []):
            if should_stop():
                return out
            if gate and not gate():   # manual pause point (returns False on stop)
                return out
            ctype = _classify_case(tc)
            pctx = _infer_page_context(tc, ctype)
            # bucket + priority
            if pctx == "login" and ctype == "negative_login":
                bucket = 0
            elif pctx == "login":
                bucket = 1
            else:
                bucket = 3
            low = _norm(tc.get("title", ""))
            positive_login = (pctx == "login" and ctype not in ("negative_login", "presence")
                              and any(k in low for k in ("نجاح", "صحيح", "الصحيحة", "valid",
                                                         "success", "successful")))
            if positive_login:
                bucket = 2
                has_positive_login = True
            # compile to intents, pausing (not aborting) on a recoverable AI error
            intents = []
            if want_ai:
                while True:
                    if should_stop():
                        return out
                    try:
                        intents = compile_test_case(tc, story, log, ctype) or []
                        break
                    except Exception as e:
                        # compile_test_case only raises for RECOVERABLE provider
                        # errors (credit, expired/invalid key, rate limit, outage).
                        # Pause and let the user fix it / switch provider + Resume.
                        decision = on_error(friendly_ai_error(e)) if on_error else "stop"
                        if decision == "retry":
                            log("Retrying compile with the current provider…", "dim")
                            continue
                        return out   # user chose Stop (should_stop is now True)
            if not intents:
                intents = _intents_from_raw_steps(tc)
            # A case that drives username/password fields belongs on the LOGIN page,
            # not the app page — otherwise it runs logged-in against BASE_URL where
            # those fields don't exist (guaranteed failure). Pull it back to a
            # logged-out login bucket.
            if bucket == 3:
                _blob = _norm(" ".join(
                    [i.get("target", "") for i in intents]
                    + [w for i in intents for w in (i.get("keywords") or [])]
                    + [tc.get("title", "")]))
                if any(s in _blob for s in ("password", "username", "كلمة المرور",
                                            "كلمه المرور", "البريد", "تسجيل الدخول",
                                            "login button", "login field", "login submit")):
                    bucket = 0
                    pctx = "login"
            if bucket == 3:
                has_app = True
            n_act = sum(1 for i in intents if i["role"] == "action")
            has_assert = any(i["role"] == "assertion" for i in intents)
            if n_act == 0 and bucket != 2 and not has_assert:
                # A presence case ("verify X exists") is still automatable as a
                # single visibility check — synthesize one rather than drop it.
                if ctype == "presence":
                    title = tc.get("title", "")
                    intents.append({
                        "role": "assertion", "verb": "", "target": title,
                        "keywords": [w for w in re.split(r"\s+", title) if len(w) > 1][:6],
                        "kind": "any", "value": "", "check": "visible",
                        "expected": "", "from_steps": []})
                    has_assert = True
                else:
                    log(f"    skipping non-automatable case (no actions): "
                        f"{tc.get('title','')[:40]}", "warn")
                    continue
            cases.append({"tc": tc, "title": tc.get("title", ""), "ctype": ctype,
                          "page_context": pctx, "bucket": bucket, "intents": intents})
        # synthesize a successful-login transition if app cases exist but no
        # explicit positive-login case was authored
        if has_app and not has_positive_login:
            cases.append({"tc": {"title": "Successful login (auto-inserted)", "steps": []},
                          "title": "Successful login (auto-inserted)",
                          "ctype": "login", "page_context": "login", "bucket": 2,
                          "intents": [{"role": "action", "verb": "login", "target": "login",
                                       "keywords": [], "kind": "any", "value": "",
                                       "check": "", "expected": "", "from_steps": []}],
                          "synthetic_login": True})
            log("    + inserted a successful-login step before app cases", "dim")
        cases.sort(key=lambda c: (c["bucket"], c["title"]))
        for i, c in enumerate(cases):
            c["priority"] = c["bucket"] * 100 + i
        out.append({"story": story, "cases": cases})
        log(f"  sequenced story {story.get('id')}: "
            f"{sum(1 for c in cases if c['bucket']<2)} logged-out, "
            f"{sum(1 for c in cases if c['bucket']==2)} login, "
            f"{sum(1 for c in cases if c['bucket']>2)} app case(s)", "info")
    return out


def _seed_locator_for_intent(intent):
    """Best-effort seed Selenium By for an intent, BEFORE any runtime healing.
    'Stable where known, // TODO only where unknown.' Returns (by, value, known)."""
    kws = [k for k in (intent.get("keywords") or []) if str(k).strip()]
    kind = intent.get("kind", "any")
    low = _norm(" ".join(kws) + " " + (intent.get("target", "") or ""))
    # known stable patterns we validated against the real DOM
    if any(k in low for k in ("languageswitch", "language toggle", "زر اللغة", "زر اللغه")):
        return ("cssSelector", '[data-svgicon="languageSwitch"]', True)
    if "kc-current-locale" in low or ("locale" in low and "dropdown" in low):
        return ("id", "kc-current-locale-link", True)
    if kind == "input" and any(k in low for k in ("username", "user", "email", "اسم المستخدم")):
        return ("cssSelector", "#username,input[name=username],input[type=email]", True)
    if kind == "input" and any(k in low for k in ("password", "كلمة المرور", "كلمة مرور")):
        return ("cssSelector", "#password,input[type=password]", True)
    if any(k in low for k in ("kc-login", "submit", "تسجيل الدخول", "login button", "sign in")):
        return ("cssSelector", "#kc-login,button[type=submit],input[type=submit]", True)
    # text-based xpath for an option/button with a clear label
    label = next((k for k in kws if len(str(k)) >= 2 and not str(k).isascii() or
                  (str(k).isascii() and len(str(k)) >= 3)), None)
    if label and kind in ("menuitem", "link", "button"):
        tag = {"menuitem": "a", "link": "a", "button": "button"}.get(kind, "*")
        return ("xpath", '//%s[normalize-space()="%s"]' % (tag, label), False)
    return ("cssSelector", "TODO_RESOLVE_AT_RUNTIME", False)


def _sh_pom(group_id, artifact_id):
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>__GID__</groupId>
  <artifactId>__AID__</artifactId>
  <version>1.0.0</version>
  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency><groupId>org.seleniumhq.selenium</groupId><artifactId>selenium-java</artifactId><version>4.21.0</version></dependency>
    <dependency><groupId>org.testng</groupId><artifactId>testng</artifactId><version>7.10.2</version></dependency>
    <dependency><groupId>io.github.bonigarcia</groupId><artifactId>webdrivermanager</artifactId><version>5.9.2</version></dependency>
    <dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId><version>2.17.1</version></dependency>
  </dependencies>
  <build><plugins>
    <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.2.5</version>
      <configuration><suiteXmlFiles><suiteXmlFile>testng.xml</suiteXmlFile></suiteXmlFiles></configuration>
    </plugin>
  </plugins></build>
</project>
""".replace("__GID__", group_id).replace("__AID__", artifact_id))


def _sh_config(pkg, base_url, login_url):
    return ("""package __PKG__.core;

/** Runtime config. Secrets come from environment variables, never hard-coded. */
public final class Config {
    private Config() {}
    public static final String BASE_URL  = env("APP_BASE_URL",  "__BASE__");
    public static final String LOGIN_URL = env("APP_LOGIN_URL", "__LOGIN__");
    public static final String USER      = env("APP_USER",  "");
    public static final String PASS      = env("APP_PASS",  "");
    public static final String API_KEY   = env("ANTHROPIC_API_KEY", "");
    public static final String MODEL     = env("ANTHROPIC_MODEL", "claude-sonnet-4-6");
    public static final boolean HEAL      = !API_KEY.isEmpty();
    private static String env(String k, String d) {
        String v = System.getenv(k); return (v == null || v.isEmpty()) ? d : v;
    }
}
""".replace("__PKG__", pkg).replace("__BASE__", base_url).replace("__LOGIN__", login_url))


def _sh_locator_store(pkg):
    return ("""package __PKG__.core;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.openqa.selenium.By;
import java.io.File;
import java.util.HashMap;
import java.util.Map;

/** Persists healed locators to locators-cache.json so each key is resolved by
 *  the AI at most once, then reused on later runs. */
public final class LocatorStore {
    private static final File FILE = new File("locators-cache.json");
    private static final ObjectMapper M = new ObjectMapper();
    private final Map<String, Map<String, String>> cache;

    @SuppressWarnings("unchecked")
    public LocatorStore() {
        Map<String, Map<String, String>> c = new HashMap<>();
        try { if (FILE.exists()) c = M.readValue(FILE, Map.class); } catch (Exception ignored) {}
        this.cache = c;
    }
    public By get(String key) {
        Map<String, String> e = cache.get(key);
        if (e == null) return null;
        return Healer.toBy(e.get("by"), e.get("value"));
    }
    public void put(String key, String by, String value) {
        Map<String, String> e = new HashMap<>(); e.put("by", by); e.put("value", value);
        cache.put(key, e);
        try { M.writerWithDefaultPrettyPrinter().writeValue(FILE, cache); } catch (Exception ignored) {}
    }
}
""".replace("__PKG__", pkg))


def _sh_anthropic_client(pkg):
    return ("""package __PKG__.core;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;

/** Minimal Anthropic Messages API client used ONLY to pick a locator when a seed
 *  fails. Returns {"by","value"} or null. */
public final class AnthropicClient {
    private static final ObjectMapper M = new ObjectMapper();
    private static final HttpClient HTTP = HttpClient.newHttpClient();

    /** Ask Claude to choose the best Selenium locator for `intentJson` from the
     *  harvested `candidatesJson` (a JSON array of elements on the live page). */
    public static Map<String, String> pickLocator(String intentJson, String candidatesJson) {
        if (Config.API_KEY.isEmpty()) return null;
        try {
            String prompt = "You resolve a Selenium locator for a UI test step that failed to "
                + "find its element. Choose the ONE element that matches the intent and return a "
                + "STABLE locator. Reply ONLY JSON: {\\"by\\":\\"id|name|cssSelector|xpath\\",\\"value\\":\\"...\\"}. "
                + "Prefer id (non-generated) > data-testid/[data-svgicon] css > name > aria/text xpath. "
                + "Never use framework ids like pn_id_*, cdk-, mat-, GUIDs.\\n\\nINTENT: " + intentJson
                + "\\n\\nCANDIDATES: " + candidatesJson;
            ObjectNode body = M.createObjectNode();
            body.put("model", Config.MODEL);
            body.put("max_tokens", 256);
            ArrayNode msgs = body.putArray("messages");
            ObjectNode m = msgs.addObject(); m.put("role", "user"); m.put("content", prompt);
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create("https://api.anthropic.com/v1/messages"))
                .header("x-api-key", Config.API_KEY)
                .header("anthropic-version", "2023-06-01")
                .header("content-type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(M.writeValueAsString(body)))
                .build();
            HttpResponse<String> resp = HTTP.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() / 100 != 2) {
                System.out.println("[heal] API error " + resp.statusCode() + ": " + resp.body());
                return null;
            }
            JsonNode root = M.readTree(resp.body());
            String text = root.path("content").path(0).path("text").asText("");
            int a = text.indexOf('{'), b = text.lastIndexOf('}');
            if (a < 0 || b < a) return null;
            JsonNode loc = M.readTree(text.substring(a, b + 1));
            String by = loc.path("by").asText(""), value = loc.path("value").asText("");
            if (by.isEmpty() || value.isEmpty()) return null;
            return Map.of("by", by, "value", value);
        } catch (Exception e) {
            System.out.println("[heal] pickLocator failed: " + e.getMessage());
            return null;
        }
    }
}
""".replace("__PKG__", pkg))


def _sh_healer(pkg):
    return ("""package __PKG__.core;

import org.openqa.selenium.*;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/** Finds elements with runtime self-healing. Order per step key:
 *  1) cached healed locator  2) the generated seed locator
 *  3) ask the Anthropic API to pick one from the live DOM, then cache it. */
public final class Healer {
    private final WebDriver driver;
    private final WebDriverWait wait;
    private final LocatorStore store = new LocatorStore();
    private static String HARVEST_JS;

    public Healer(WebDriver driver) {
        this.driver = driver;
        this.wait = new WebDriverWait(driver, Duration.ofSeconds(12));
    }

    public WebElement find(String key, By seed, String intentJson) {
        By cached = store.get(key);
        if (cached != null) {
            WebElement e = tryFind(cached);
            if (e != null) return e;
        }
        if (seed != null) {
            WebElement e = tryFind(seed);
            if (e != null) return e;
        }
        if (Config.HEAL) {
            System.out.println("[heal] resolving '" + key + "' via Anthropic API");
            String dom = harvest();
            Map<String, String> picked = AnthropicClient.pickLocator(intentJson, dom);
            if (picked != null) {
                By by = toBy(picked.get("by"), picked.get("value"));
                WebElement e = tryFind(by);
                if (e != null) {
                    store.put(key, picked.get("by"), picked.get("value"));
                    System.out.println("[heal] '" + key + "' -> " + picked.get("by")
                        + "=" + picked.get("value"));
                    return e;
                }
            }
        }
        throw new NoSuchElementException("Could not resolve step '" + key
            + "'. Seed=" + seed + ". Set ANTHROPIC_API_KEY to enable healing.");
    }

    public void act(String key, String verb, By seed, String intentJson, String value) {
        if ("navigate".equals(verb) || "wait".equals(verb)) return;
        WebElement el = find(key, seed, intentJson);
        ((JavascriptExecutor) driver).executeScript(
            "arguments[0].scrollIntoView({block:'center'});", el);
        switch (verb == null ? "click" : verb) {
            case "type":
                el.clear(); if (value != null && !value.isEmpty()) el.sendKeys(value); break;
            case "select":
                el.click(); break;   // custom dropdowns: open; the next intent picks the option
            case "hover":
                el.click(); break;
            default:
                try { el.click(); }
                catch (Exception e) {
                    ((JavascriptExecutor) driver).executeScript("arguments[0].click();", el);
                }
        }
    }

    public boolean assertVisible(String key, By seed, String intentJson) {
        try { return find(key, seed, intentJson).isDisplayed(); }
        catch (Exception e) { return false; }
    }

    /** Verify by PAGE TEXT — true if the rendered page contains ANY of the given
     *  keywords. Used for error/validation/message checks instead of locating an
     *  element (no AI call; tolerant of where the message renders). Polls up to 8s
     *  so async messages have time to appear. Case-insensitive. */
    public boolean assertTextPresent(String[] keywords) {
        long end = System.currentTimeMillis() + 8000;
        do {
            String txt = "";
            try {
                Object r = ((JavascriptExecutor) driver).executeScript(
                    "return document.body ? document.body.innerText : '';");
                txt = r == null ? "" : r.toString().toLowerCase();
            } catch (Exception ignored) {}
            for (String k : keywords) {
                if (k != null && !k.isEmpty() && txt.contains(k.toLowerCase())) return true;
            }
            try { Thread.sleep(300); } catch (InterruptedException e) { break; }
        } while (System.currentTimeMillis() < end);
        return false;
    }

    private WebElement tryFind(By by) {
        try { return wait.until(ExpectedConditions.visibilityOfElementLocated(by)); }
        catch (Exception e) { return null; }
    }

    private String harvest() {
        try {
            if (HARVEST_JS == null) {
                var in = Healer.class.getResourceAsStream("/harvest.js");
                HARVEST_JS = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            }
            Object r = ((JavascriptExecutor) driver).executeScript(
                "return JSON.stringify((function(){" + HARVEST_JS + "})());");
            return r == null ? "[]" : r.toString();
        } catch (Exception e) { return "[]"; }
    }

    public static By toBy(String by, String value) {
        if (by == null) return By.cssSelector(value);
        switch (by) {
            case "id":          return By.id(value);
            case "name":        return By.name(value);
            case "xpath":       return By.xpath(value);
            case "linkText":    return By.linkText(value);
            case "className":   return By.className(value);
            default:            return By.cssSelector(value);
        }
    }
}
""".replace("__PKG__", pkg))


def _sh_base_test(pkg):
    return ("""package __PKG__.tests;

import __PKG__.core.*;
import org.openqa.selenium.*;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import java.time.Duration;

/** One browser per test class. Logged-out cases run first (priority order),
 *  then the successful-login step, then app cases — no logout-to-retest. */
public abstract class BaseTest {
    protected WebDriver driver;
    protected Healer heal;

    @BeforeClass
    public void setUp() {
        driver = DriverFactory.create();
        driver.manage().timeouts().implicitlyWait(Duration.ofSeconds(6));
        heal = new Healer(driver);
        openLoginPage();
    }
    @AfterClass
    public void tearDown() { if (driver != null) driver.quit(); }

    /** Fresh, logged-out login page (clears any session).
     *  Navigates to the app ROOT and lets it redirect to a freshly-issued login
     *  page. OAuth/OIDC params (code_challenge, nonce, state) are generated per
     *  session, so a frozen LOGIN_URL can go stale — only fall back to it if the
     *  app doesn't redirect to a login form on its own. */
    protected void openLoginPage() {
        try { driver.manage().deleteAllCookies(); } catch (Exception ignored) {}
        driver.get(Config.BASE_URL);
        if (!loginFieldPresent(4000)) {
            driver.get(Config.LOGIN_URL);
            loginFieldPresent(4000);
        }
    }

    /** True once a username/password field is on the page (polls up to ms). */
    protected boolean loginFieldPresent(long ms) {
        long end = System.currentTimeMillis() + ms;
        do {
            try {
                Object r = ((JavascriptExecutor) driver).executeScript(
                    "return !!document.querySelector("
                    + "'input[type=password],#username,input[name=username]');");
                if (Boolean.TRUE.equals(r)) return true;
            } catch (Exception ignored) {}
            try { Thread.sleep(250); } catch (InterruptedException e) { return false; }
        } while (System.currentTimeMillis() < end);
        return false;
    }

    /** Perform a real successful login using the seed login locators + healing. */
    protected void performLogin() {
        heal.act("login.username", "type",
            Healer.toBy("cssSelector", "#username,input[name=username],input[type=email]"),
            "{\\"target\\":\\"username field\\",\\"kind\\":\\"input\\"}", Config.USER);
        heal.act("login.password", "type",
            Healer.toBy("cssSelector", "#password,input[type=password]"),
            "{\\"target\\":\\"password field\\",\\"kind\\":\\"input\\"}", Config.PASS);
        String beforeUrl = driver.getCurrentUrl();
        heal.act("login.submit", "click",
            Healer.toBy("cssSelector", "#kc-login,button[type=submit],input[type=submit]"),
            "{\\"target\\":\\"login submit button\\",\\"kind\\":\\"button\\"}", "");
        // Wait for the post-login navigation (URL change) instead of a blind sleep.
        long end = System.currentTimeMillis() + 12000;
        while (System.currentTimeMillis() < end) {
            try { if (!driver.getCurrentUrl().equals(beforeUrl)) break; } catch (Exception ignored) {}
            try { Thread.sleep(250); } catch (InterruptedException e) { break; }
        }
    }
}
""".replace("__PKG__", pkg))


def _sh_gitignore():
    return "target/\n*.iml\n.idea/\n# healed locators are environment-specific:\nlocators-cache.json\n"


def _sh_readme(base_url, login_url):
    return ("""# QA Studio — self-healing automation

Generated by QA Studio. QA Studio did NOT drive a browser; locators are resolved
at RUNTIME: each step has a seed locator, and when it fails, the framework asks the
Anthropic API to pick the right element from the live DOM, then caches it in
`locators-cache.json` (reused on later runs, so the AI is called at most once per step).

## Run
1. Set environment variables (never hard-code secrets):
   - `ANTHROPIC_API_KEY`  (enables healing; without it, only seed locators are used)
   - `APP_USER`, `APP_PASS`  (login credentials)
   - optional `APP_BASE_URL` (default __BASE__), `APP_LOGIN_URL` (default __LOGIN__),
     `ANTHROPIC_MODEL` (default claude-sonnet-4-6)
2. `mvn test`

## Sequence
Tests run by TestNG priority: logged-out cases (invalid login, validation, the
login-page language dropdown) first, then the successful-login step, then the
authenticated app cases — all in one browser, no logout-to-retest.

## Healing log
Watch stdout for `[heal] resolving '<step>' ...` and `[heal] '<step>' -> by=value`.
Commit the resulting `locators-cache.json` only if you want to share resolved
locators across machines (it is git-ignored by default).
""".replace("__BASE__", base_url).replace("__LOGIN__", login_url))


def _java_str(s):
    """Escape a Python string for a Java double-quoted literal."""
    return (str(s or "").replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", " ").replace("\r", " ").replace("\t", " "))


def _java_ident(s, fallback):
    out = re.sub(r"[^A-Za-z0-9_]", "", (s or "").title().replace(" ", ""))
    if not out or not (out[0].isalpha() or out[0] == "_"):
        out = fallback
    return out[:60]


def _emit_intent(lines, key, intent):
    """Append the Java for one intent to `lines`."""
    role = intent.get("role")
    target = intent.get("target", "")
    ij = json.dumps({"target": target, "keywords": intent.get("keywords", []),
                     "kind": intent.get("kind", "any"), "verb": intent.get("verb", "")},
                    ensure_ascii=False)
    by, val, _known = _seed_locator_for_intent(intent)
    seed = ("null" if val == "TODO_RESOLVE_AT_RUNTIME"
            else 'Healer.toBy("%s", "%s")' % (by, _java_str(val)))
    todo = "  // TODO verify locator (resolved at runtime)" if val == "TODO_RESOLVE_AT_RUNTIME" else ""
    if role == "precondition":
        lines.append('        // precondition (no UI action): %s' % _java_str(target)[:70])
        return
    if role == "assertion":
        kind = (intent.get("kind") or "").lower()
        kws = [k for k in (intent.get("keywords") or []) if k] or ([target] if target else [])
        # Text/message/menu checks with no locatable element → verify by PAGE TEXT
        # (no AI heal): faster, cheaper, and robust to where the message renders.
        if seed == "null" and (kind in ("text", "message", "menu", "validation", "error")
                               or not kind):
            arr = ", ".join('"%s"' % _java_str(k) for k in kws)
            lines.append('        org.testng.Assert.assertTrue('
                         'heal.assertTextPresent(new String[]{%s}),' % arr)
            lines.append('            "expected text: %s");' % _java_str(target)[:60])
            return
        lines.append('        org.testng.Assert.assertTrue(heal.assertVisible("%s", %s, "%s"),'
                     % (_java_str(key), seed, _java_str(ij)))
        lines.append('            "expected: %s");%s' % (_java_str(target)[:60], todo))
        return
    verb = intent.get("verb") or "click"
    value = _java_str(intent.get("value", ""))
    lines.append('        heal.act("%s", "%s", %s, "%s", "%s");%s'
                 % (_java_str(key), verb, seed, _java_str(ij), value, todo))


def generate_selfhealing_test_class(story, cases, pkg):
    """Emit a TestNG class for one story DETERMINISTICALLY from compiled intents
    (no LLM writing Java). Cases run by priority: logged-out → login → app."""
    sid = str(story.get("id", "0"))
    cls = "Story%sTests" % re.sub(r"[^A-Za-z0-9]", "", sid)
    L = []
    L.append("package %s.tests;" % pkg)
    L.append("")
    L.append("import %s.core.*;" % pkg)
    L.append("import org.testng.annotations.Test;")
    L.append("")
    L.append("/** Story %s — %s */" % (sid, _java_str(story.get("title", ""))))
    L.append("public class %s extends BaseTest {" % cls)
    for ci, c in enumerate(cases):
        bucket = c["bucket"]; pr = c.get("priority", bucket * 100 + ci)
        mname = "tc_%d_%s" % (pr, _java_ident(c.get("title", ""), "case%d" % ci))
        L.append("")
        L.append('    @Test(priority = %d, description = "%s")'
                 % (pr, _java_str(c.get("title", ""))))
        L.append("    public void %s() {" % mname)
        L.append('        // [%s · %s-page]' % (c["ctype"], c["page_context"]))
        if bucket < 2:
            L.append("        openLoginPage();")
        elif bucket == 2:
            L.append("        openLoginPage();")
            L.append("        performLogin();")
        else:
            L.append("        driver.get(Config.BASE_URL);")
        # emit intents (for the login-transition case, skip its action intents —
        # performLogin() already did the real login — but keep its assertions)
        for ii, intent in enumerate(c.get("intents", [])):
            if bucket == 2 and intent.get("role") == "action":
                continue
            _emit_intent(L, "%s.%d.%d" % (sid, ci, ii), intent)
        L.append("    }")
    L.append("}")
    return "\n".join(L) + "\n", cls


def _sh_write_testng(out_dir, pkg, m):
    """(Re)write testng.xml from EVERY story recorded in the manifest so a push
    after a partial/resumed run carries all generated classes, not just this run's."""
    classes = sorted({r.get("test_class") for r in (m.get("stories") or {}).values()
                      if r.get("test_class")})
    items = "\n".join('      <class name="%s.tests.%s"/>' % (pkg, c) for c in classes)
    with open(os.path.join(out_dir, "testng.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">\n'
                '<suite name="QA Studio Self-Healing Suite" verbose="1">\n'
                '  <test name="Sequenced Tests"><classes>\n%s\n  </classes></test>\n'
                '</suite>\n' % items)


def build_selfhealing_project(out_dir, sequenced, base_url, login=None,
                              group_id="com.qastudio", artifact_id="automation-tests",
                              cb=None, should_stop=lambda: False, orig_tcs=None):
    """Write a full self-healing Maven/TestNG project (no browser was driven).
    `sequenced` = output of validate_and_sequence_suite(). Returns written paths.
    `orig_tcs` = {story_id: [original test-case dicts]} so each generated story is
    recorded in the manifest — that's what lets a stopped/closed run RESUME instead
    of regenerating from scratch (classify_selection reads the manifest)."""
    cb = cb or (lambda *a, **k: None)
    pkg = group_id
    pkg_path = pkg.replace(".", "/")
    src_main = os.path.join(out_dir, "src", "main", "java", pkg_path, "core")
    src_test = os.path.join(out_dir, "src", "test", "java", pkg_path, "tests")
    res_dir = os.path.join(out_dir, "src", "test", "resources")
    for d in (src_main, src_test, res_dir):
        os.makedirs(d, exist_ok=True)
    login_url = (login or {}).get("url") or base_url
    written = []
    orig_tcs = orig_tcs or {}
    m = load_manifest(out_dir)          # resume: record of already-generated stories
    m.setdefault("stories", {})

    def _w(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(os.path.relpath(path, out_dir))

    cb("Writing self-healing framework (Config, Healer, Anthropic client)\u2026", "dim")
    _w(os.path.join(out_dir, "pom.xml"), _sh_pom(group_id, artifact_id))
    _w(os.path.join(out_dir, ".gitignore"), _sh_gitignore())
    _w(os.path.join(out_dir, "README.md"), _sh_readme(base_url, login_url))
    _w(os.path.join(src_main, "DriverFactory.java"), _driver_factory(pkg))
    _w(os.path.join(src_main, "Config.java"), _sh_config(pkg, base_url, login_url))
    _w(os.path.join(src_main, "LocatorStore.java"), _sh_locator_store(pkg))
    _w(os.path.join(src_main, "AnthropicClient.java"), _sh_anthropic_client(pkg))
    _w(os.path.join(src_main, "Healer.java"), _sh_healer(pkg))
    _w(os.path.join(src_test, "BaseTest.java"), _sh_base_test(pkg))
    _w(os.path.join(res_dir, "harvest.js"), _HARVEST_JS)

    test_classes = []
    for entry in sequenced:
        if should_stop():
            break
        story = entry["story"]
        if not entry.get("cases"):
            continue
        sid = str(story.get("id"))
        cb(f"  generating tests for story {story.get('id')} "
           f"({len(entry['cases'])} case(s))", "dim")
        java, cls = generate_selfhealing_test_class(story, entry["cases"], pkg)
        _w(os.path.join(src_test, "%s.java" % cls), java)
        test_classes.append(cls)
        # Record + persist this story immediately so a stop / pause / app-close keeps
        # it; the keys mirror what classify_selection() computes from the original
        # test cases, so a re-run recognises it as done and skips it.
        m["stories"][sid] = {
            "test_class": cls,
            "test_cases": {_tc_key(tc): _method_name(tc.get("title", ""))
                           for tc in orig_tcs.get(sid, [])},
        }
        try:
            save_manifest(out_dir, m)
            _sh_write_testng(out_dir, pkg, m)
        except Exception:
            pass

    # Final testng + manifest across EVERY recorded class (this run + prior runs).
    _sh_write_testng(out_dir, pkg, m)
    save_manifest(out_dir, m)
    cb(f"Wrote {len(written)} files, {len(test_classes)} test class(es) this run.", "ok")
    return written


def generate_and_push_selfhealing(out_dir, stories_payload, base_url, login=None,
                                  group_id="com.qastudio", artifact_id="automation-tests",
                                  cb=None, should_stop=lambda: False, want_ai=True,
                                  on_error=None, gate=None):
    """End-to-end no-browser path: validate+sequence → generate self-healing
    project. (Push is done separately via push_to_git, as today.)
    on_error/gate enable pause-on-error and manual pause (see
    validate_and_sequence_suite)."""
    cb = cb or (lambda *a, **k: None)
    cb("Validating and sequencing test cases (no browser)\u2026", "info")
    sequenced = validate_and_sequence_suite(stories_payload, log=cb, want_ai=want_ai,
                                            should_stop=should_stop, on_error=on_error,
                                            gate=gate)
    if should_stop():
        return []
    # Original test cases per story → recorded in the manifest for resume support.
    _orig_tcs = {str(sp.get("story", {}).get("id")): (sp.get("test_cases", []) or [])
                 for sp in stories_payload}
    return build_selfhealing_project(out_dir, sequenced, base_url, login,
                                     group_id, artifact_id, cb, should_stop,
                                     orig_tcs=_orig_tcs)