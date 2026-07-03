# AI Providers — Architecture Refactor Plan

## The problem
Providers fail to connect on many end-user machines. The recurring root cause is **Python / dependency mismatch**, not the provider APIs themselves.

Today `engine.py` talks to each provider through that provider's **native Python SDK**, imported lazily inside `_ai_call_once(provider, cfg, ...)`:

| Provider(s) | Current call path | SDK pulled in |
|---|---|---|
| `anthropic` | `import anthropic` → `Anthropic().messages.create` | `anthropic` (+ httpx, pydantic) |
| `openai`, `nvidia`, `deepseek`, `qwen` | `from openai import OpenAI` → `chat.completions.create` | `openai` (+ httpx, pydantic) |
| `azure_openai` | `from openai import AzureOpenAI` | `openai` |
| `gemini` | `import google.generativeai as genai` → `generate_content` | `google-generativeai` (+ **protobuf, grpcio**, google-api-core) |
| `manus` | OpenAI *Responses* API via `openai` | `openai` |
| `ollama` | local HTTP (`base_url`) | — |

Why this breaks per-machine:
1. **Binary-wheel deps tied to the Python version.** `google-generativeai` drags in `grpcio` and `protobuf` — C-extension packages whose wheels are built per Python minor version. On a user's Python that has no matching wheel, pip either fails to build or installs an ABI-incompatible one → import/connect errors. This is the single biggest offender.
2. **Version pin conflicts.** `openai` and `anthropic` each pin `httpx`/`pydantic` ranges. Installed together (or against a pre-existing environment) they resolve to versions that satisfy one and break another.
3. **Heavy, slow installs** that fail silently behind the installer, leaving a provider that "won't connect."
4. **Moving targets.** Each SDK's breaking changes (e.g. pydantic v1→v2, openai 0.x→1.x) ripple into us.

The irony: **almost everything we do is a single POST to a chat-completions endpoint.** We are paying the full SDK dependency cost for a thin slice of functionality.

## Goal
Make provider connectivity depend only on **pure-Python HTTP** so it works on any Python 3.8+ with no compiled dependencies and no version negotiation. Keep behavior (models, vision, JSON mode, error surfacing) identical.

## Target architecture — a uniform HTTP adapter layer

Replace per-provider SDKs with one thin transport (`httpx`, pure-Python, already a transitive dep everywhere) and a small set of **adapters** that map our normalized request/response to each provider's REST contract.

```
engine.complete(prompt, images, ...)         # unchanged public surface
        │
        ▼
_ai_call_once(provider, cfg, ...)            # becomes a dispatch to an adapter
        │
        ▼
ADAPTERS[provider].call(cfg, messages, ...)  # returns normalized text  (raises EmptyAIResponse)
        │
        ▼
httpx.post(url, headers, json=payload, timeout=...)
```

Adapter interface (one function, or a tiny class per family):
```python
def call(cfg, prompt_text, images, max_tokens, timeout, want_json) -> str
```

Only **three** wire formats are needed — most providers already share one:

| Adapter | Endpoint | Auth header | Notes |
|---|---|---|---|
| **openai_compat** (openai, nvidia, deepseek, qwen, ollama, + manus-lite path) | `POST {base_url or https://api.openai.com/v1}/chat/completions` | `Authorization: Bearer <key>` | our current `_extract_openai_text` logic maps 1:1 to the JSON body `choices[0].message.content` |
| **azure** | `POST {endpoint}/openai/deployments/{deployment}/chat/completions?api-version={ver}` | `api-key: <key>` | same body shape as openai_compat |
| **anthropic** | `POST https://api.anthropic.com/v1/messages` | `x-api-key: <key>`, `anthropic-version: 2023-06-01` | response `content[]` text blocks |
| **gemini** | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=<key>` | key in query | **drops `google-generativeai` entirely** — the biggest win; `response_mime_type=application/json` for `want_json` |

Vision/images: each adapter already knows how it encodes images today (OpenAI `image_url` data-URI, Anthropic `image` base64 block, Gemini `inline_data`) — that logic moves verbatim into the adapter, just emitted as JSON instead of via the SDK object.

## What changes in `engine.py`
- `_ai_call_once` → thin dispatch into `ADAPTERS[provider]`. Each existing `if provider == ...:` branch becomes an adapter function with the SAME request-building logic, calling `httpx.post` instead of the SDK.
- `_extract_openai_text` stays (operate on `resp.json()` dict instead of SDK object — trivial).
- `EmptyAIResponse` / refusal / content-filter / truncation handling: preserved, mapped from JSON fields (`finish_reason`, `stop_reason`, `promptFeedback.blockReason`).
- `active_providers()`, `set_credentials()`, `current_model()`, `AI_CONFIG`: **unchanged** — the config schema (api_key/base_url/model/deployment/vision) already carries everything the adapters need.
- Connection test (the "Connected" check the Setup screen runs): point it at the same adapter with a tiny request, so a green dot means the real path works.

## Dependencies
- **Remove** `anthropic`, `openai`, `google-generativeai` from `requirements.txt` / the installer.
- **Add** `httpx` (pure-Python; likely already present). No `grpcio`, no `protobuf`, no `pydantic` requirement of ours.
- Net effect: dramatically smaller, faster, Python-version-agnostic install — which is exactly the failure mode we're removing.

## Migration steps (incremental, behavior-preserving)
> **Status — ALL PROVIDERS DONE (in `engine.py`), `USE_HTTP_ADAPTERS` now default ON.** Pure-HTTP adapters via `requests` (no `httpx` needed) for every provider, covering both completion (`_ai_call_once`) and connect/model-list (`list_models`):
> - `_openai_compat_http` / `_openai_compat_models_http` — openai, nvidia, deepseek, qwen (validated live ✅)
> - `_anthropic_http` / `_anthropic_models_http`
> - `_azure_http` / `_azure_models_http`
> - `_gemini_http` / `_gemini_models_http` — **drops `google-generativeai`/grpcio/protobuf**, the heaviest/most Python-fragile dep
> - `_manus_http` — Responses API create→poll→read
> - ollama was already pure-HTTP (unchanged)
>
> **REFACTOR COMPLETE.** The flag is gone — every provider call (generate via `_ai_call_once`, connect via `validate_api_key`, model-list via `list_models`) goes through the HTTP adapters unconditionally. All SDK branches deleted; `engine.py` imports no vendor SDK. `requirements.txt` trimmed by 16 packages: `anthropic`, `openai`, `google-generativeai` + their compiled tree (`grpcio`, `protobuf`, `proto-plus`, `google-*`, `googleapis-common-protos`, `httplib2`, `uritemplate`, `jiter`) — i.e. the exact wheels that failed to install on mismatched Python.

1. ~~`_http_post_json` / `_http_get_json` helpers.~~ ✅
2. ~~openai_compat adapter (completion + models).~~ ✅ validated live
3. ~~anthropic + gemini + azure + manus adapters.~~ ✅
4. ~~Flip default ON.~~ ✅ (then removed the flag entirely)
5. ~~Remove SDK branches + SDK deps from requirements.~~ ✅
3. Add **anthropic** and **gemini** adapters (gemini removes the heaviest dep).
4. Add **azure** and **manus** adapters.
5. Flip `_ai_call_once` to dispatch to adapters; keep the SDK branches behind the flag for one release as a fallback.
6. Remove SDK imports + requirements once every provider is validated. Delete the flag.

## Risks & mitigations
- **Subtle response-shape differences** (e.g. Qwen/DeepSeek quirks): mitigated because we already normalize via `_extract_openai_text`; add a golden-response test per provider.
- **Streaming** (if used anywhere): httpx supports SSE; port only if a feature depends on it (current calls appear non-streaming).
- **Gemini safety-block semantics**: preserve by reading `promptFeedback.blockReason` + `candidates[].finishReason` from JSON (same info the SDK exposed).
- **Auth edge cases** (Azure `api-key` header vs Bearer, Gemini key-in-query): captured explicitly in the table above.

## Testing
- Per-provider live smoke test: connect + one tiny completion + one vision call (for `vision:true` providers).
- A `want_json=True` test for the intent-compiler path (Gemini + one OpenAI-compat).
- Install test on a clean machine with a **different** Python minor version than dev — the exact scenario that fails today — and confirm every provider connects.

## Bottom line
The providers aren't the problem; the SDKs' compiled, version-pinned dependency trees are. Collapsing all provider I/O onto a pure-HTTP adapter layer removes the Python-mismatch failure class entirely, shrinks the install, and gives us one place to own timeouts, retries, and error mapping — with no change to the app's public behavior or the `AI_CONFIG` schema.
