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
    total_tc = 0
    for s in stories:
        suite_id = smap.get(s["id"])
        tc = 0
        if suite_id:
            try:
                tc = len(fetch_test_cases_for_suite(project, plan_id, suite_id))
            except Exception:
                tc = 0
        s["test_cases"] = tc
        total_tc += tc

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

def build_sprint_summary_email(data):
    """Build an HTML email for the sprint summary report.
    `data` is the dict returned by sprint_summary()."""
    plan_name = _html.escape(str(data.get("plan_name", "")))
    iteration = _html.escape(str(data.get("iteration", "") or "—"))
    total_stories = data.get("total_stories", 0)
    total_tc = data.get("total_test_cases", 0)
    by_state = data.get("by_state", {})
    stories = data.get("stories", [])

    def _state_colors(state):
        s = (state or "").lower()
        if s in ("done", "closed", "completed", "resolved"):
            return ("#1F9D57", "#E5F6EC")
        if s in ("active", "in progress", "committed", "doing"):
            return ("#5234E0", "#ECE8FF")
        if s in ("new", "to do", "proposed", "open"):
            return ("#C2860C", "#FAF1DD")
        return ("#74727E", "#F1F0F5")

    # stat cards
    def _card(label, value, fg, bg):
        return (f"<td style='padding:6px'>"
                f"<div style='background:{bg};border-radius:12px;padding:14px 16px;text-align:center'>"
                f"<div style='font-size:11px;color:#74727E;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:.4px'>{label}</div>"
                f"<div style='font-size:26px;font-weight:800;color:{fg};margin-top:4px'>{value}</div>"
                f"</div></td>")
    cards = (_card("Stories", total_stories, "#5234E0", "#ECE8FF")
             + _card("Test Cases", total_tc, "#1F9D57", "#E5F6EC")
             + _card("Statuses", len(by_state), "#C2860C", "#FAF1DD"))
    cards_row = f"<table style='width:100%;border-collapse:collapse'><tr>{cards}</tr></table>"

    # status breakdown chips
    chips = ""
    for st, cnt in sorted(by_state.items(), key=lambda x: -x[1]):
        fg, bg = _state_colors(st)
        chips += (f"<span style='background:{bg};color:{fg};font-size:12px;font-weight:700;"
                  f"padding:4px 11px;border-radius:20px;margin:0 6px 6px 0;display:inline-block'>"
                  f"{_html.escape(str(st))}: {cnt}</span>")
    status_block = (f"<h3 style='color:#1B1A22;font-size:14px;margin:22px 0 10px'>Status breakdown</h3>"
                    f"<div>{chips or '<span style=\"color:#A3A1AD;font-size:13px\">No stories.</span>'}</div>")

    # story table
    rows = ""
    for s in stories:
        title = _html.escape(str(s.get("title", "")))
        sid = _html.escape(str(s.get("id", "")))
        state = str(s.get("state", ""))
        tc = int(s.get("test_cases", 0) or 0)
        fg, bg = _state_colors(state)
        rtl = "direction:rtl;text-align:right;" if any('\u0600' <= c <= '\u06ff' for c in title) else ""
        rows += (f"<tr style='border-bottom:1px solid #EEEDF3'>"
                 f"<td style='padding:10px 12px;vertical-align:top'>"
                 f"<div style='font-size:13px;font-weight:700;color:#1B1A22;{rtl}'>{title}</div>"
                 f"<div style='font-family:monospace;font-size:11px;color:#A3A1AD;font-weight:700;"
                 f"margin-top:2px'>#{sid}</div></td>"
                 f"<td style='padding:10px 12px;text-align:center;white-space:nowrap;vertical-align:top'>"
                 f"<span style='font-family:monospace;font-size:12px;color:#74727E;font-weight:700'>{tc} TC</span></td>"
                 f"<td style='padding:10px 12px;text-align:right;white-space:nowrap;vertical-align:top'>"
                 f"<span style='background:{bg};color:{fg};font-size:11px;font-weight:700;"
                 f"padding:3px 10px;border-radius:20px'>{_html.escape(state)}</span></td>"
                 f"</tr>")
    story_block = (f"<h3 style='color:#1B1A22;font-size:14px;margin:22px 0 10px'>"
                   f"Stories ({len(stories)})</h3>"
                   f"<table style='width:100%;border-collapse:collapse;border:1px solid #E8E7EE;"
                   f"border-radius:10px;overflow:hidden'>{rows}</table>") if stories else ""

    return f"""<html><body style='font-family:Segoe UI,Arial,sans-serif;color:#1B1A22;background:#FBFBFD;
    max-width:640px;margin:auto;padding:0'>
    <div style='background:#5234E0;padding:28px 30px;border-radius:14px 14px 0 0'>
      <h1 style='color:#ffffff;margin:0;font-size:24px;font-weight:800;
      letter-spacing:-0.3px;line-height:1.2'>QA Studio</h1>
      <div style='color:#ffffff;font-size:15px;font-weight:700;margin-top:2px'>Sprint Summary</div>
      <div style='color:#D8CEFF;font-size:13px;margin-top:6px'>{plan_name} &middot; {iteration}</div>
    </div>
    <div style='background:#fff;padding:24px 28px;border:1px solid #E8E7EE;border-top:none;
    border-radius:0 0 14px 14px'>
      {cards_row}
      {status_block}
      {story_block}
    </div>
    <div style='text-align:center;color:#A3A1AD;font-size:11px;padding:16px'>
      Generated by QA Studio · Azure DevOps + AI
    </div>
    </body></html>"""


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
                                    "precondition": s.get("precondition", "")} for s in steps]})

    prompt = f"""
You are a senior SDET. Generate Selenium + Java + TestNG automation using the Page Object Model.

Return ONLY a JSON object (no markdown), exactly:
{{"page_code": "<full Java source of the Page Object class>",
  "test_code": "<full Java source of the TestNG test class>"}}

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
- Use ONLY locators from the REAL DOM list provided. Pick the most stable: prefer id, then name,
  then css, then xpath. Define them as `By` fields in the Page Object.
- If a needed element is NOT in the DOM list, create the By with a best-guess css and add a
  comment `// TODO verify locator`.
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
    data = parse_json_robust(raw)
    if isinstance(data, list) and data:
        data = data[0]
    return {
        "class_name": test_class,
        "page_class": page_class,
        "test_code": data.get("test_code", "// generation failed"),
        "page_code": data.get("page_code", "// generation failed"),
    }


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


def push_to_git(repo_dir, remote_url, token, branch="main", message="Add QA Studio automation tests", cb=None):
    """Init/commit/push the generated project to a Git remote using the git CLI.
    `token` is embedded into the HTTPS URL for auth (GitHub/Azure DevOps style).
    Returns (ok, message).
    """
    cb = cb or (lambda *a, **k: None)
    import subprocess
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
    p = run(["git", "push", "-u", "origin", branch, "--force"])
    out = (p.stdout + p.stderr)
    # scrub token from any echoed output
    if token:
        out = out.replace(token, "***")
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

def _app_dir():
    return os.path.dirname(os.path.abspath(__file__))

def local_version():
    """Read the local VERSION file (next to this module). Returns str or '0.0.0'."""
    try:
        with open(os.path.join(_app_dir(), "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
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
    Returns dict: {"update": bool, "local": str, "remote": str, "error": str|None}.
    Network failures are swallowed (update=False) so startup is never blocked.
    """
    local = local_version()
    raw = (f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
           f"{GITHUB_BRANCH}/VERSION")
    try:
        r = requests.get(raw, timeout=timeout, headers={"Cache-Control": "no-cache"})
        if r.status_code != 200:
            return {"update": False, "local": local, "remote": None,
                    "error": f"HTTP {r.status_code}"}
        remote = (r.text or "").strip()
        return {"update": _ver_newer(remote, local), "local": local,
                "remote": remote, "error": None}
    except Exception as e:
        return {"update": False, "local": local, "remote": None, "error": str(e)[:120]}

def apply_update(cb=None):
    """Run `git pull` in the app directory to fetch the new version.
    Returns (ok, message). Requires the app folder to be a git clone.
    """
    cb = cb or (lambda *a, **k: None)
    import subprocess
    d = _app_dir()
    if not os.path.isdir(os.path.join(d, ".git")):
        return (False, "This install isn't a git clone, so it can't self-update. "
                       "Please download the latest version from GitHub.")
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
