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

def set_credentials(provider=None, api_key=None, pat=None, gmail=None,
                    org=None, gmail_sender=None):
    global AI_PROVIDER, AZURE_PAT, GMAIL_APP_PASS, AZURE_ORG, GMAIL_SENDER
    if provider:
        AI_PROVIDER = provider
    if api_key and AI_PROVIDER in AI_CONFIG:
        AI_CONFIG[AI_PROVIDER]["api_key"] = api_key
    if pat is not None:
        AZURE_PAT = pat
    if gmail is not None:
        GMAIL_APP_PASS = gmail
    if org:
        AZURE_ORG = org.strip()
    if gmail_sender:
        GMAIL_SENDER = gmail_sender.strip()


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

def ai_complete(prompt_text, images=None, max_tokens=4096, timeout=None):
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
                model=cfg["model"], max_tokens=max_tokens, timeout=timeout,
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
                model=cfg["model"], max_tokens=max_tokens, timeout=timeout,
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
                model=cfg["deployment"], max_tokens=max_tokens, timeout=timeout,
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

    # Build records: {id, title, step_count}
    recs = []
    for c in cases:
        wi = c.get("workItem", {})
        tc_id = wi.get("id")
        title = wi.get("name", "")
        if not tc_id:
            continue
        try:
            steps = fetch_test_case_steps(tc_id)
            sc = len(steps)
        except Exception:
            sc = 0
        recs.append({"id": int(tc_id), "title": title, "steps": sc,
                     "key": _semantic_key(title), "norm": _norm_title(title)})

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

    removed = []
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
            if do_delete:
                ok = delete_test_case(project, plan_id, suite_id, victim["id"])
                tone = "skip" if ok else "err"
                verb = "deleted" if ok else "delete FAILED"
            else:
                tone = "warn"; verb = "duplicate (not deleted)"
            cb("log", {"msg": f"{victim['title']}", "tone": tone, "ar": True,
                       "id": victim["id"],
                       "detail": f"{verb} · {victim['steps']} steps · dup of #{keeper['id']}"})
            removed.append({"id": victim["id"], "title": victim["title"],
                            "kept_id": keeper["id"], "deleted": (do_delete and ok)})
    if dup_groups:
        cb("log", {"msg": f"Removed {len(removed)} duplicate test case"
                          + ("s" if len(removed) != 1 else "")
                          + f" across {dup_groups} group" + ("s" if dup_groups != 1 else ""),
                   "tone": "ok"})
    else:
        cb("log", {"msg": "No duplicate test cases found in the suite.", "tone": "dim"})
    return {"removed": removed, "groups": dup_groups}


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
                    err += 1; done += 1; err_by_story[story_id] += 1
                    cb("log", {"msg": tc_title + f" — {e}", "tone": "err", "id": tc_id,
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
    VIOLET="#6A4DFF"; VIOLET_INK="#5234E0"; VIOLET_SOFT="#EEEAFF"
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
        f"<td width='34' valign='middle' style='padding-right:13px'>"
        f"<div style='width:34px;height:34px;background:{VIOLET_INK};border-radius:9px;"
        f"color:#fff;font-size:13px;font-weight:700;text-align:center;line-height:34px;"
        f"font-family:{MONO}'>QA</div></td>"
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
        shown = log_lines[:600]
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
                f"&hellip; and {len(log_lines)-600} more lines</div>") if len(log_lines) > 600 else ""
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
        log_box = (f"<div style='border:1px solid {LINE};border-radius:12px;overflow:hidden;background:{TINT}'>"
                   f"{toolbar}"
                   f"<div style='max-height:420px;overflow-y:auto;overflow-x:hidden'>{rows}</div>"
                   f"{more}</div>")
        sections += (f"<tr><td style='padding:26px 32px;border-top:1px solid {LINE}'>"
                     f"{_sec_head(INK, 'Run activity log', str(len(log_lines)) + ' lines')}"
                     f"<div style='margin-top:14px'>{log_box}</div></td></tr>")

    footer = (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
              f"<td valign='middle' style='padding-right:9px'><span style='display:inline-block;"
              f"width:20px;height:20px;background:{VIOLET_SOFT};border-radius:6px;color:{VIOLET_INK};"
              f"text-align:center;line-height:20px;font-size:11px;font-weight:700;font-family:{MONO}'>Q</span></td>"
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

    # status breakdown — small color-coded cards that WRAP to the next line
    # (inline-block, not a single table row) so they never overflow the frame.
    status_cells = ""
    for st, cnt in sorted(by_state.items(), key=lambda x: -x[1]):
        fg, bg = _state_colors(st)
        status_cells += (
            f"<div style='display:inline-block;vertical-align:top;background:{bg};"
            f"border-radius:10px;padding:12px 8px;text-align:center;width:96px;"
            f"margin:0 6px 8px 0;box-sizing:border-box'>"
            f"<div style='font-size:22px;font-weight:800;color:{fg}'>{cnt}</div>"
            f"<div style='font-size:11px;color:#74727E;font-weight:700;margin-top:3px;"
            f"line-height:1.3'>{_html.escape(str(st))}</div></div>")
    status_block = (
        f"<h3 style='color:#1B1A22;font-size:14px;margin:22px 0 10px'>Status breakdown</h3>"
        + (f"<div style='font-size:0'>{status_cells}</div>"
           if status_cells else '<span style="color:#A3A1AD;font-size:13px">No stories.</span>'))

    # story table
    _proj = data.get("project", "")
    _org = data.get("org", AZURE_ORG)
    rows = ""
    for s in stories:
        title = _html.escape(str(s.get("title", "")))
        sid = _html.escape(str(s.get("id", "")))
        state = str(s.get("state", ""))
        tc = int(s.get("test_cases", 0) or 0)
        fg, bg = _state_colors(state)
        rtl = "direction:rtl;text-align:right;" if any('\u0600' <= c <= '\u06ff' for c in title) else ""
        wi_url = (f"https://dev.azure.com/{_org}/{_proj}/_workitems/edit/{s.get('id','')}"
                  if _proj and s.get("id") else "")
        title_html = (f"<a href='{_html.escape(wi_url, quote=True)}' "
                      f"style='color:#1B1A22;text-decoration:none'>{title}</a>"
                      if wi_url else title)
        id_html = (f"<a href='{_html.escape(wi_url, quote=True)}' "
                   f"style='color:#5234E0;text-decoration:none'>#{sid} &nbsp;&rarr;</a>"
                   if wi_url else f"#{sid}")
        rows += (f"<tr style='border-bottom:1px solid #EEEDF3'>"
                 f"<td style='padding:10px 12px;vertical-align:middle'>"
                 f"<div style='font-size:13px;font-weight:700;color:#1B1A22;{rtl}'>{title_html}</div>"
                 f"<div style='font-family:monospace;font-size:11px;color:#A3A1AD;font-weight:700;"
                 f"margin-top:2px'>{id_html}</div></td>"
                 f"<td style='padding:10px 12px;text-align:center;white-space:nowrap;vertical-align:middle;"
                 f"width:70px'>"
                 f"<span style='font-family:monospace;font-size:12px;color:#74727E;font-weight:700'>{tc} TC</span></td>"
                 f"<td style='padding:10px 12px;text-align:right;white-space:nowrap;vertical-align:middle;"
                 f"width:130px'>"
                 f"<span style='background:{bg};color:{fg};font-size:11px;font-weight:700;"
                 f"padding:3px 10px;border-radius:20px;display:inline-block'>{_html.escape(state)}</span></td>"
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
const sel='input,button,a,select,textarea,[role=button],[role=link],[role=tab],[contenteditable=true]';
const els=[...document.querySelectorAll(sel)];
return els.slice(0,250).map((el,i)=>({
  idx: i,
  tag: el.tagName.toLowerCase(),
  type: el.getAttribute('type')||'',
  id: el.id||'',
  name: el.getAttribute('name')||'',
  text: (el.innerText||el.value||'').trim().slice(0,60),
  placeholder: el.getAttribute('placeholder')||'',
  aria: el.getAttribute('aria-label')||'',
  visible: !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length),
  css: robustCss(el),
  xpath: xpathOf(el)
}));
"""


def _harvest_dom(driver):
    """Return the list of interactive elements on the current page."""
    try:
        return driver.execute_script("return (function(){" + _HARVEST_JS + "})();") or []
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

    def snapshot(tag=""):
        els = _harvest_dom(driver)
        all_snapshots.extend(els)
        if tag:
            cb(f"  captured {len(els)} elements ({tag})", "dim")
        return els

    def to_locator(el):
        if not el:
            return None
        if el.get("id"):
            return {"by": "id", "value": el["id"]}
        if el.get("name"):
            return {"by": "name", "value": el["name"]}
        if el.get("css"):
            return {"by": "css", "value": el["css"]}
        return {"by": "xpath", "value": el.get("xpath", "")}

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
            cb("Opening login page…", "dim")
            driver.get(login_url)
            wait_dom_ready()
            try:
                # Username/email — wait for it to actually appear. Covers Keycloak
                # (#username), generic email/text inputs, and common name patterns.
                user_sel = login.get("user_locator") or (
                    "#username,input[type=email],input[name=email],input[name=username],"
                    "input[name*=user i],input[id*=user i],input[type=text]")
                cb("Waiting for the username field…", "dim")
                u = find_first(user_sel, timeout=25)
                if u is None:
                    raise RuntimeError("username/email field did not appear")
                u.clear(); u.send_keys(login["user"])

                # Password — may be on the same page or a second step. Wait for it.
                pass_sel = login.get("pass_locator") or "#password,input[type=password]"
                p = find_first(pass_sel, timeout=10)
                if p is None:
                    # Two-step login: click Next/Continue first, then wait for password
                    nxt = find_first("button[type=submit],#kc-login,button,input[type=submit]", timeout=5)
                    if nxt is not None:
                        nxt.click(); wait_dom_ready()
                    p = find_first(pass_sel, timeout=15)
                if p is None:
                    raise RuntimeError("password field did not appear")
                p.clear(); p.send_keys(login["password"])

                # Submit
                submit_sel = login.get("submit_locator") or (
                    "#kc-login,button[type=submit],input[type=submit],button")
                btn = find_first(submit_sel, timeout=10)
                if btn is None:
                    p.submit()
                else:
                    btn.click()
                cb("Submitted login — verifying…", "dim")
                # wait for navigation away from the login form
                try:
                    WebDriverWait(driver, 20).until(
                        lambda d: (d.current_url or "").rstrip("/") != login_url.rstrip("/")
                                  or not d.find_elements(By.CSS_SELECTOR, "input[type=password]"))
                except Exception:
                    pass
                wait_dom_ready()
            except Exception as e:
                raise RuntimeError(f"Login step failed: {str(e)[:160]}")
            ok, reason = _verify_logged_in(driver, login_url, cb)
            if not ok:
                raise RuntimeError(f"Login could not be verified — {reason}. "
                                   f"Aborting so locators aren't captured from the wrong page.")
            cb(f"Login verified — {reason}", "ok")
        else:
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

        def try_match(a, els):
            """Run the AI matcher, but treat repeated credit-limit errors as a
            signal to stop the whole walk instead of hammering the API."""
            nonlocal credit_hits, abort_credit
            try:
                return _match_step_to_element(a, els, cb)
            except CreditBalanceError:
                credit_hits += 1
                cb(f"AI credit limit hit ({credit_hits}/{CREDIT_STOP}).", "err")
                if credit_hits >= CREDIT_STOP:
                    abort_credit = True
                return None, "none", ""

        # walk each test case
        for sp in stories_payload:
            if should_stop() or abort_credit:
                break
            story = sp.get("story", {})
            cb(f"\u25b8 Story {story.get('id')} \u2014 {story.get('title','')}", "story")
            for tc in sp.get("test_cases", []):
                if should_stop() or abort_credit:
                    break
                steps = tc.get("steps", []) or []
                cb(f"  walking '{tc.get('title','')}' ({len(steps)} steps)", "info")
                try:
                    cb("    loading start page\u2026", "dim")
                    driver.get(site_url); _t.sleep(wait_secs)
                except Exception:
                    pass
                for si, st in enumerate(steps, 1):
                    if should_stop() or abort_credit:
                        break
                    action = st.get("action", "") or ""
                    if not action.strip():
                        continue
                    disp = action.strip()
                    for _pfx in ("\u0627\u0644\u0634\u0631\u0637 \u0627\u0644\u0645\u0633\u0628\u0642:", "Precondition:", "precondition:"):
                        if disp.startswith(_pfx):
                            disp = disp[len(_pfx):].strip(); break
                    cb(f"  {si}/{len(steps)}  {disp[:45]}", "dim")
                    els = snapshot()
                    match, kind, value = try_match(action, els)
                    if abort_credit:
                        break
                    if match:
                        st["locator"] = to_locator(match)
                        st["locator_src"] = "live"
                        live_count += 1
                        live_el = find_live(match)
                        if live_el is not None:
                            _flash(live_el)  # outline it in the browser
                            try:
                                if kind == "type":
                                    live_el.clear(); live_el.send_keys(value or "test")
                                    cb(f"      typed into {_describe(match)}", "ok")
                                elif kind == "select":
                                    from selenium.webdriver.support.ui import Select
                                    try:
                                        Select(live_el).select_by_visible_text(value)
                                    except Exception:
                                        _safe_click(live_el)
                                    cb(f"      selected on {_describe(match)}", "ok")
                                else:  # click / navigate / default
                                    _safe_click(live_el)
                                    cb(f"      clicked {_describe(match)}", "ok")
                                _t.sleep(1.2)  # let the page react
                            except Exception as ae:
                                cb(f"      couldn't act on {_describe(match)}: {str(ae)[:50]}", "warn")
                        else:
                            cb(f"      matched {_describe(match)} but it left the page", "warn")
                        exp = st.get("expected", "") or ""
                        if exp.strip():
                            els2 = snapshot()
                            m2, _, _ = try_match(exp, els2)
                            st["assert_locator"] = to_locator(m2) if m2 else None
                    else:
                        union = _dedup_reindex(all_snapshots)
                        m2, _k2, _v2 = (try_match(action, union) if union else (None, "none", ""))
                        if abort_credit:
                            break
                        if m2:
                            st["locator"] = to_locator(m2)
                            st["locator_src"] = "snapshot"
                            snap_count += 1
                            cb(f"      SNAPSHOT: using {_describe(m2)} from an earlier page "
                               f"\u2014 will be marked // TODO verify (from snapshot)", "warn")
                            todos.append({"s": story.get("id"), "tc": tc.get("title", ""),
                                          "n": si, "a": disp, "kind": "snapshot"})
                            exp = st.get("expected", "") or ""
                            if exp.strip():
                                ma, _, _ = try_match(exp, union)
                                st["assert_locator"] = to_locator(ma) if ma else None
                        else:
                            st["locator"] = None
                            st["locator_src"] = "guess"
                            guess_count += 1
                            cb("      GUESS: no element matched \u2014 step will be marked "
                               "// TODO verify locator", "warn")
                            todos.append({"s": story.get("id"), "tc": tc.get("title", ""),
                                          "n": si, "a": disp, "kind": "guess"})

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

    for item in stories_payload:
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


def push_to_git(repo_dir, remote_url, token, branch="main", message="Add QA Studio automation tests", cb=None, force=False):
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
    _push = ["git", "push", "-u", "origin", branch]
    if force:
        _push.append("--force")
    p = run(_push)
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

    Primary source is the GitHub *API* (api.github.com), which returns the live
    file content and is not subject to the raw-CDN caching that delayed detection.
    Falls back to the cache-busted raw URL if the API is unavailable.
    """
    import time as _t, base64 as _b64
    local = local_version()
    bust = int(_t.time())

    def _via_api():
        url = (f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/"
               f"contents/VERSION?ref={GITHUB_BRANCH}")
        r = requests.get(url, timeout=timeout, headers={
            "Accept": "application/vnd.github.raw+json",
            "Cache-Control": "no-cache",
        })
        if r.status_code != 200:
            raise RuntimeError(f"API HTTP {r.status_code}")
        txt = r.text or ""
        # With the raw+json Accept header GitHub returns the file content directly.
        # If it returned JSON (older behavior), decode the base64 content field.
        if txt.lstrip().startswith("{"):
            import json as _json
            data = _json.loads(txt)
            txt = _b64.b64decode(data.get("content", "")).decode("utf-8", "ignore")
        return txt.strip()

    def _via_raw():
        url = (f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
               f"{GITHUB_BRANCH}/VERSION?cb={bust}")
        r = requests.get(url, timeout=timeout,
                         headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        if r.status_code != 200:
            raise RuntimeError(f"raw HTTP {r.status_code}")
        return (r.text or "").strip()

    remote = None
    err = None
    for fetch in (_via_api, _via_raw):
        try:
            remote = fetch()
            if remote:
                break
        except Exception as e:
            err = str(e)[:120]
            continue
    if not remote:
        return {"update": False, "local": local, "remote": None, "error": err}
    return {"update": _ver_newer(remote, local), "local": local,
            "remote": remote, "error": None}

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