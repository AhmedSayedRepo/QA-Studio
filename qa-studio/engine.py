"""engine.py — provider-agnostic AI + Azure DevOps engine (no UI dependency).
Ported from the original QA tool scripts so the Flet UI can drive it directly.
Configure provider keys in AI_CONFIG below, or pass them at runtime via set_credentials().
"""
import os, re, json, base64, html as _html, requests

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
}

FEATURE_DESCRIPTION = ""   # optional global feature context for step generation

# Email
GMAIL_SENDER   = "wsstestteam2@gmail.com"
GMAIL_APP_PASS = ""

# Runtime credentials (set by the UI)
AZURE_PAT = ""

def set_credentials(provider=None, api_key=None, pat=None, gmail=None):
    global AI_PROVIDER, AZURE_PAT, GMAIL_APP_PASS
    if provider:
        AI_PROVIDER = provider
    if api_key and AI_PROVIDER in AI_CONFIG:
        AI_CONFIG[AI_PROVIDER]["api_key"] = api_key
    if pat is not None:
        AZURE_PAT = pat
    if gmail is not None:
        GMAIL_APP_PASS = gmail


# ═══════════════════════════════════════════════════════════════════════════════
#  AI PROVIDER LAYER
# ═══════════════════════════════════════════════════════════════════════════════
class CreditBalanceError(Exception):
    pass

def _ai_cfg():
    cfg = AI_CONFIG.get(AI_PROVIDER)
    if not cfg:
        raise RuntimeError(f"Unknown AI_PROVIDER '{AI_PROVIDER}'.")
    return cfg

def _is_credit_error(msg):
    m = msg.lower()
    return ("credit balance is too low" in m or "insufficient_quota" in m
            or ("quota" in m and "exceeded" in m) or ("billing" in m and "hard limit" in m))

def active_providers():
    """Provider names that have a usable key."""
    out = []
    for name, cfg in AI_CONFIG.items():
        k = (cfg.get("api_key") or "").strip()
        if k and not k.startswith("your-") and "-here" not in k:
            out.append(name)
    return out

def ai_complete(prompt_text, images=None, max_tokens=4096):
    cfg = _ai_cfg(); provider = AI_PROVIDER; images = images or []
    try:
        if provider == "anthropic":
            import anthropic
            content = []
            for im in images:
                content.append({"type": "image", "source": {"type": "base64",
                    "media_type": im["media_type"], "data": im["data"]}})
            content.append({"type": "text", "text": prompt_text})
            resp = anthropic.Anthropic(api_key=cfg["api_key"]).messages.create(
                model=cfg["model"], max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            return resp.content[0].text

        elif provider in ("openai", "nvidia"):
            from openai import OpenAI
            client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url")) if cfg.get("base_url") \
                     else OpenAI(api_key=cfg["api_key"])
            content = [{"type": "text", "text": prompt_text}]
            for im in images:
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{im['media_type']};base64,{im['data']}"}})
            resp = client.chat.completions.create(
                model=cfg["model"], max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            return resp.choices[0].message.content

        elif provider == "azure_openai":
            from openai import AzureOpenAI
            client = AzureOpenAI(api_key=cfg["api_key"], azure_endpoint=cfg["endpoint"],
                                 api_version=cfg["api_version"])
            content = [{"type": "text", "text": prompt_text}]
            for im in images:
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{im['media_type']};base64,{im['data']}"}})
            resp = client.chat.completions.create(
                model=cfg["deployment"], max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            return resp.choices[0].message.content

        elif provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=cfg["api_key"])
            model = genai.GenerativeModel(cfg["model"])
            parts = [prompt_text]
            for im in images:
                parts.append({"mime_type": im["media_type"], "data": base64.b64decode(im["data"])})
            resp = model.generate_content(parts, generation_config={"max_output_tokens": max_tokens})
            return resp.text

        elif provider == "ollama":
            payload = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt_text}], "stream": False}
            r = requests.post(f"{cfg['base_url']}/api/chat", json=payload, timeout=180)
            r.raise_for_status()
            return r.json()["message"]["content"]
        else:
            raise RuntimeError(f"Unhandled provider '{provider}'")
    except CreditBalanceError:
        raise
    except Exception as e:
        if _is_credit_error(str(e)):
            raise CreditBalanceError(str(e))
        raise


# ═══════════════════════════════════════════════════════════════════════════════
#  AZURE REST HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _azure_get(url, pat=None, timeout=12):
    pat = pat or AZURE_PAT
    try:
        r = requests.get(url, auth=("", pat), timeout=timeout)
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
    Returns (ok, message). Uses a tiny ai_complete call and maps auth errors."""
    try:
        out = ai_complete("ping", max_tokens=5)
        # Some providers return None/empty on success-but-no-content; only treat
        # an exception as failure. A returned string (even empty) means auth passed.
        if out is None:
            # ambiguous — try once more with a real token request
            out = ai_complete("Reply with: ok", max_tokens=5)
        return True, "ok"
    except CreditBalanceError as e:
        # key is valid but out of credits — let the user proceed/decide
        return True, "credit"
    except Exception as e:
        m = str(e).lower()
        if ("401" in m or "403" in m or "invalid" in m or "unauthor" in m
                or "forbidden" in m or "authentication" in m or "api key" in m
                or "permission" in m or "x-api-key" in m):
            return False, "auth"
        if "429" in m or "rate" in m:
            return True, "ratelimited"  # key valid, just throttled
        if "connect" in m or "timeout" in m or "timed out" in m or "ssl" in m:
            return False, "network"
        return False, str(e)[:120]


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
    url = (f"https://dev.azure.com/{AZURE_ORG}/{project}/"
           f"_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase?api-version=7.0")
    resp = requests.get(url, auth=("", AZURE_PAT), timeout=30)
    if resp.status_code == 200:
        return resp.json().get("value", [])
    raise RuntimeError(f"HTTP {resp.status_code}")


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
            return parse_json_robust(ai_complete(text, max_tokens=4096))
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
            return parse_json_robust(ai_complete(prompt, max_tokens=4096))
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
            try:
                tc_id = create_test_case(project, plan_id, suite_id, tc_title, sid)
                total_created += 1
                ps["ok"] += 1
                cb("log", {"msg": tc_title, "tone": "ok", "id": tc_id, "ar": True})
            except Exception as e:
                errors += 1
                ps["err"] += 1
                cb("log", {"msg": f"{tc_title} — {e}", "tone": "err", "ar": True})
            cb("stat", {"total": total_created, "stories_done": stories_done,
                        "total_stories": total_stories, "done": total_created,
                        "skipped": 0, "errors": errors})
        if not should_stop():
            stories_done += 1
            cb("log", {"msg": f"Story {sid} completed", "tone": "ok", "ico": "└"})

    cb("done", {"summary": f"{total_created} created · {errors} failed",
                "created": total_created, "errors": errors,
                "stories_done": stories_done, "total_stories": total_stories,
                "per_story": list(per_story_stats.values())})


def run_steps(project, plan_id, story_ids, cb, should_stop=lambda: False,
              existing_mode="skip"):
    """existing_mode: 'skip' or 'evaluate'."""
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
            skipped_items.append({"id": tc_id, "title": tc_title, "reason": "Already had steps"})
            cb("log", {"msg": tc_title + " — already has steps, skipped", "tone": "warn", "id": tc_id, "ico": "⏭", "ar": True})
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
                    skipped_items.append({"id": tc_id, "title": tc_title,
                                          "reason": verdict.get("reason","Existing steps adequate")})
                    cb("log", {"msg": tc_title + " — existing steps adequate", "tone": "ok", "id": tc_id, "ar": True})
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
                    cb("log", {"msg": tc_title, "tone": "ok", "id": tc_id, "ar": True,
                               "replace_wip": tc_id,
                               "detail": f"{len(steps)} steps · pre {npre} · action {len(steps)} · expected {len(steps)}"})
                    if inadequate_reason:
                        action_items.append({"id": tc_id, "title": tc_title, "reason": inadequate_reason})
                except CreditBalanceError:
                    cb("done", {"summary": "Stopped — out of AI credits", "reason": "credit",
                                "action_items": action_items}); return
                except Exception as e:
                    err += 1; done += 1; err_by_story[story_id] += 1
                    cb("log", {"msg": tc_title + f" — {e}", "tone": "err", "id": tc_id,
                               "ar": True, "replace_wip": tc_id})

        # update per-story progress snapshot
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
                          "skipped": skip_by_story.get(sid, 0), "err": err_by_story.get(sid, 0)})
    cb("done", {"summary": f"{ok} updated · {skipped} skipped · {err} failed",
                "updated": ok, "skipped": skipped, "errors": err,
                "created": seeded_total,
                "stories_done": stories_done, "total_stories": total_stories,
                "action_items": action_items, "skipped_items": skipped_items,
                "per_story": per_story})


def validate_stories_in_plan(project, plan_id, story_ids):
    """Read-only check: returns (found, missing) story-id lists for the plan.
    A story is 'found' if it maps to a requirement suite already in the plan."""
    smap = discover_suites_for_stories(project, plan_id, set(story_ids), create_missing=False)
    found = [sid for sid in story_ids if sid in smap]
    missing = [sid for sid in story_ids if sid not in smap]
    return found, missing


def count_existing_steps(project, plan_id, story_ids):
    """Count test cases that already have steps (for the existing-steps modal)."""
    wit, test = connect_azure_sdk(project)
    smap = discover_suites_for_stories(project, plan_id, set(story_ids), create_missing=False)
    ids = []
    for sid, suite_id in smap.items():
        try:
            for tc in fetch_test_cases_for_suite(project, plan_id, suite_id):
                wid = tc.get("workItem", {}).get("id")
                if wid: ids.append(wid)
        except Exception:
            pass
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
def send_report(to_addrs, subject, html_body):
    """Send an HTML email via Gmail SMTP. Returns (ok, error_msg)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    if not GMAIL_APP_PASS or not to_addrs:
        return False, "No Gmail password or recipients configured."
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = ", ".join(to_addrs) if isinstance(to_addrs, list) else to_addrs
    msg.attach(MIMEText(html_body, "html"))
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


def build_report_email(tool, summary, stats, action_items=None, skipped_items=None, per_story=None):
    """Build a polished card-based HTML email for the run report."""
    # Stat cards
    tone_map = {"Updated": ("#1F9D57", "#E5F6EC"), "Created": ("#5234E0", "#ECE8FF"),
                "Skipped": ("#C2860C", "#FAF1DD"), "Failed": ("#E0474D", "#FCEBEC"),
                "Stories": ("#5234E0", "#ECE8FF")}
    cards = ""
    for k, v in stats.items():
        fg, bg = tone_map.get(k, ("#1B1A22", "#F6F5FA"))
        cards += (f"<td style='padding:6px'>"
                  f"<div style='background:{bg};border-radius:12px;padding:14px 16px;text-align:center'>"
                  f"<div style='font-size:11px;color:#74727E;font-weight:700;text-transform:uppercase;"
                  f"letter-spacing:.4px'>{k}</div>"
                  f"<div style='font-size:26px;font-weight:800;color:{fg};margin-top:4px'>{v}</div>"
                  f"</div></td>")
    cards_row = f"<table style='width:100%;border-collapse:collapse'><tr>{cards}</tr></table>"

    def _item_card(a, tone_fg, tone_bg, label):
        title = _html.escape(str(a.get("title", "")))
        reason = _html.escape(str(a.get("reason", "")))
        item_id = _html.escape(str(a.get("id", "")))
        rtl = "direction:rtl;text-align:right;" if any('\u0600' <= c <= '\u06ff' for c in title) else ""
        return (f"<div style='border:1px solid #E8E7EE;border-radius:10px;padding:12px 14px;"
                f"margin-bottom:10px;background:#fff'>"
                f"<div style='margin-bottom:6px'>"
                f"<span style='background:{tone_bg};color:{tone_fg};font-size:11px;font-weight:700;"
                f"padding:3px 9px;border-radius:20px'>{label}</span> "
                f"<span style='font-family:monospace;font-size:12px;color:#A3A1AD;font-weight:700'>"
                f"#{item_id}</span></div>"
                f"<div style='font-size:13px;font-weight:700;color:#1B1A22;{rtl}'>{title}</div>"
                + (f"<div style='font-size:12px;color:#74727E;margin-top:4px;{rtl}'>{reason}</div>" if reason else "")
                + "</div>")

    review_block = ""
    if action_items:
        cards_html = "".join(_item_card(a, "#C2860C", "#FAF1DD", "Review") for a in action_items)
        review_block = (f"<h3 style='color:#C2860C;font-size:15px;margin:24px 0 12px'>"
                        f"⚠ Needs review ({len(action_items)})</h3>{cards_html}")

    # Per-story breakdown table (mirrors the Report screen)
    per_story_block = ""
    if per_story:
        rows = ""
        for sp in per_story:
            sid = _html.escape(str(sp.get("id", "")))
            title = _html.escape(str(sp.get("title", "")))
            total = int(sp.get("total", 0) or 0)
            ok = int(sp.get("ok", 0) or 0); sk = int(sp.get("skipped", 0) or 0); er = int(sp.get("err", 0) or 0)
            rtl = "direction:rtl;text-align:right;" if any('\u0600' <= c <= '\u06ff' for c in title) else ""
            chips = ""
            if ok: chips += (f"<span style='background:#E5F6EC;color:#1F9D57;font-size:11px;font-weight:700;"
                             f"padding:2px 8px;border-radius:20px;margin-right:4px'>✓ {ok}</span>")
            if sk: chips += (f"<span style='background:#FAF1DD;color:#C2860C;font-size:11px;font-weight:700;"
                             f"padding:2px 8px;border-radius:20px;margin-right:4px'>⏭ {sk}</span>")
            if er: chips += (f"<span style='background:#FCEBEC;color:#E0474D;font-size:11px;font-weight:700;"
                             f"padding:2px 8px;border-radius:20px;margin-right:4px'>✕ {er}</span>")
            rows += (f"<tr style='border-bottom:1px solid #EEEDF3'>"
                     f"<td style='padding:10px 12px;vertical-align:top'>"
                     f"<div style='font-size:13px;font-weight:700;color:#1B1A22;{rtl}'>{title}</div>"
                     f"<div style='font-family:monospace;font-size:11px;color:#A3A1AD;font-weight:700;"
                     f"margin-top:2px'>#{sid} · {total} test case" + ("s" if total != 1 else "") + "</div>"
                     f"</td>"
                     f"<td style='padding:10px 12px;text-align:right;white-space:nowrap;vertical-align:top'>{chips}</td>"
                     f"</tr>")
        per_story_block = (
            f"<h3 style='color:#1B1A22;font-size:15px;margin:24px 0 12px'>"
            f"Per-story breakdown ({len(per_story)})</h3>"
            f"<table style='width:100%;border-collapse:collapse;border:1px solid #E8E7EE;"
            f"border-radius:10px;overflow:hidden'>{rows}</table>")

    skipped_block = ""
    if skipped_items:
        shown = skipped_items[:30]
        cards_html = "".join(_item_card(a, "#74727E", "#F1F0F5", "Skipped") for a in shown)
        more = (f"<div style='font-size:12px;color:#A3A1AD;margin-top:6px'>"
                f"…and {len(skipped_items)-30} more</div>") if len(skipped_items) > 30 else ""
        skipped_block = (f"<h3 style='color:#74727E;font-size:15px;margin:24px 0 12px'>"
                         f"⏭ Skipped ({len(skipped_items)})</h3>{cards_html}{more}")

    tool_safe = _html.escape(str(tool))
    summary_safe = _html.escape(str(summary))
    return f"""<html><body style='font-family:Segoe UI,Arial,sans-serif;color:#1B1A22;background:#FBFBFD;
    max-width:640px;margin:auto;padding:0'>
    <div style='background:#5234E0;padding:28px 30px;border-radius:14px 14px 0 0'>
      <h1 style='color:#ffffff;margin:0;font-size:24px;font-weight:800;
      letter-spacing:-0.3px;line-height:1.2'>QA Studio</h1>
      <div style='color:#ffffff;font-size:15px;font-weight:700;margin-top:2px'>{tool_safe}</div>
      <div style='display:inline-block;margin-top:12px;background:rgba(255,255,255,0.18);
      color:#ffffff;font-size:13px;font-weight:700;padding:6px 14px;border-radius:20px'>{summary_safe}</div>
    </div>
    <div style='background:#fff;padding:24px 28px;border:1px solid #E8E7EE;border-top:none;
    border-radius:0 0 14px 14px'>
      {cards_row}
      {per_story_block}
      {review_block}
      {skipped_block}
    </div>
    <div style='text-align:center;color:#A3A1AD;font-size:11px;padding:16px'>
      Generated by QA Studio · Azure DevOps + AI
    </div>
    </body></html>"""