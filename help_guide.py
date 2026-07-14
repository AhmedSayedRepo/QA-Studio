"""help_guide.py — In-app feature guide.

A searchable Help overlay: a left-hand nav lists every feature; selecting one
shows a short briefing (what it does + a few key points). Opened from the nav
rail's "Help & guide" button via app._open_help_guide().

Self-contained: FEATURES holds the content; show(app) builds the modal and wires
search + selection with in-place control updates (no full re-render).
"""
import flet as ft
import theme as T
from ui import ghost_btn


# Each feature: key, icon, title, one-line blurb, `details` (fuller prose
# paragraphs), and `points` (key bullets).
FEATURES = [
    {"key": "setup", "icon": ft.Icons.TUNE, "title": "Setup",
     "blurb": "Connect your AI provider and Azure DevOps, then pick a project, "
              "test plan, and user stories. Everything else runs on this selection.",
     "details": [
         "Setup is the starting point for the whole app: the AI connection and the "
         "Azure DevOps selection you make here are what Run, Report, Regression "
         "Plan, Sprint Plan, Sprint Report and Automation all operate on.",
         "First connect an AI provider: choose the provider and model, paste its "
         "API key, and Connect validates it before saving. The model list is fetched "
         "live from the provider once a valid key is saved. Then add your Azure "
         "DevOps organization and Personal Access Token (PAT) to load your projects.",
         "Finally choose what to generate — test-case Titles or full Steps — and the "
         "output language, Arabic or English. Changing provider, model, project or "
         "plan while connected drops the live connection so you never run against a "
         "stale selection.",
         "Email sender setup (the address, display name, and Gmail App Password used "
         "to send reports) is Admin-only and shared: an Admin sets it once here and it "
         "syncs to every signed-in user's install automatically — non-admins don't see "
         "these fields at all, and never need their own copy. If sharing fails "
         "(offline, not signed in, etc.) the Admin's own save still succeeds locally; "
         "only the sync to others is affected, and a toast says so.",
     ],
     "points": [
         "Provider + model + API key, validated on Connect; keys are stored locally "
         "and never leave your device except to call that provider.",
         "Each provider keeps its own key; NVIDIA keeps a separate key per model, and "
         "the model dropdown marks which models have a key (● active).",
         "Azure DevOps PAT + organization load projects; pick project → test plan → "
         "stories.",
         "Choose Titles vs Steps and Arabic / English — these defaults also live on "
         "the Settings screen.",
         "Email sender (address / name / Gmail App Password): Admin-only, configured "
         "once and shared with every user — not a per-device setting.",
     ]},
    {"key": "run", "icon": ft.Icons.PLAY_ARROW, "title": "Run",
     "blurb": "Generates test cases into your Azure test plan — either test-case "
              "titles or full step-by-step steps.",
     "details": [
         "Run reads the stories you selected on Setup together with their acceptance "
         "criteria, and writes results straight back into your Azure DevOps test "
         "plan. It works in one of two modes depending on your Setup choice.",
         "In Titles mode it proposes new test-case titles per story and skips any it "
         "recognizes as duplicates already in the suite. In Steps mode it writes "
         "detailed precondition / action / expected steps into the story's test "
         "cases. It verifies and de-duplicates as it goes, so re-running only adds "
         "what's genuinely missing.",
         "Duplicate detection runs in two passes, in both modes: a quick check first, "
         "then an AI review that catches duplicates worded completely differently "
         "(e.g. \"requests submitted for the branch\" vs \"actions taken for the "
         "branch\" — same test, different words) that the quick check alone would "
         "miss. This also cleans up duplicates already sitting in the suite from "
         "earlier runs or manual entry — not just new ones — keeping whichever copy "
         "has the most complete steps and removing the rest, with the kept/removed "
         "test case IDs logged.",
         "Progress is live — elapsed time, an ETA, and a running log — and you can "
         "Stop at any point; work already written to Azure is kept.",
         "The RECENT ACTIVITY log has Copy and Clear buttons pinned next to its "
         "title, same as Automation's Activity log — copy the whole log to your "
         "clipboard, or clear it, without losing your place elsewhere on screen.",
         "Before a Steps run starts, if any selected story's suite already has test "
         "cases with steps written, a prompt asks whether to Skip them (leave "
         "existing steps untouched, only fill in what's missing) or Evaluate with "
         "AI (re-check existing steps against the acceptance criteria and rewrite "
         "any that are incomplete). If that check itself can't be completed (e.g. "
         "a connection hiccup), the run continues automatically in Skip mode and "
         "a warning explains why the prompt didn't appear, instead of failing silently.",
     ],
     "points": [
         "Titles: writes NEW test-case titles per story, skipping duplicates in the suite.",
         "Steps: writes precondition / action / expected steps into the test cases.",
         "Two-pass de-dup (quick check + AI review) also cleans up duplicates already in the suite.",
         "Live elapsed / ETA / log, with a Stop button that keeps whatever was saved.",
         "Copy / Clear buttons on the activity log, same as Automation.",
         "Steps runs: a Skip vs Evaluate-with-AI prompt appears when a suite already "
         "has steps written.",
     ]},
    {"key": "report", "icon": ft.Icons.DESCRIPTION_OUTLINED, "title": "Report",
     "blurb": "The results of the last run: how many were created, skipped or "
              "failed, a per-story breakdown, and anything flagged for review.",
     "details": [
         "Report is the summary of your most recent Run. It shows Created / Skipped "
         "/ Failed counters, a per-story pass indicator, and a 'Needs your review' "
         "list of anything the AI wasn't fully confident about, so you can spot-check "
         "the edge cases rather than re-reading everything.",
         "From here you can email the report to stakeholders or jump straight to the "
         "test plan in Azure DevOps to see the generated cases in context.",
     ],
     "points": [
         "Created / Skipped / Failed counters and a per-story pass indicator.",
         "'Needs your review' surfaces low-confidence items for a quick check.",
         "Email the report, or open the test plan directly in Azure DevOps.",
     ]},
    {"key": "regression", "icon": ft.Icons.FACT_CHECK_OUTLINED, "title": "Regression Plan",
     "blurb": "Build a regression plan from your existing test plans and their "
              "stories, with weighted effort estimates balanced across your team.",
     "details": [
         "Regression Plan assembles a regression scope from test plans you already "
         "have. Pick the source plans and it pulls in their stories and test cases, "
         "then estimates the effort: test cases × minutes × the Azure DevOps priority "
         "weight → estimated hours, balanced across your named resources.",
         "The plan table is editable inline — delete a story and the totals and the "
         "per-resource workload recalculate instantly. When you're done you can "
         "export to Word / Excel / PDF / JSON, or email the plan; the email "
         "auto-attaches the Excel and Word versions with an inline summary, so there "
         "is no separate export step.",
     ],
     "points": [
         "Pick source test plans — it pulls in their stories and test cases.",
         "Weighted effort estimate (cases × minutes × priority weight), balanced "
         "across named resources.",
         "Inline-editable table: delete a story and totals/workload recompute at once.",
         "Export to Word / Excel / PDF / JSON, or email (Excel + Word auto-attached).",
     ]},
    {"key": "testplan", "icon": ft.Icons.ASSIGNMENT_OUTLINED, "title": "Sprint Plan",
     "blurb": "Plan and estimate a sprint's testing scope, with an inline editable "
              "plan table and an email-ready report.",
     "details": [
         "Sprint Plan sizes the testing for a single sprint. It pulls the sprint's "
         "user stories from Azure, lets you estimate hours per story and assign a "
         "tester to each, and keeps a running total so you can see the whole "
         "sprint's testing load in one place.",
         "Like Regression Plan, the table is editable inline and the email sends an "
         "Excel-attached, report-ready summary — no need to click an export button "
         "first.",
     ],
     "points": [
         "Pulls the sprint's user stories from Azure DevOps.",
         "Estimate hours per story and assign a tester to each.",
         "Inline-editable plan table with live totals.",
         "Email the plan — the Excel (and Word) attach automatically.",
     ]},
    {"key": "titles", "icon": ft.Icons.ARTICLE_OUTLINED, "title": "Sprint Report",
     "blurb": "A sprint-closure report: stories grouped by status plus a bug "
              "summary, in Arabic or English.",
     "details": [
         "Sprint Report produces an end-of-sprint summary: the sprint's stories "
         "grouped by their status, together with a bug summary, formatted for "
         "sharing. Pick Arabic or English and the whole report — including "
         "right-to-left layout for Arabic — is generated accordingly.",
         "Export the finished report or email it directly to your stakeholders.",
     ],
     "points": [
         "Groups the sprint's stories by status and summarizes its bugs.",
         "Arabic or English output, with correct RTL layout for Arabic.",
         "Export or email the finished report.",
     ]},
    {"key": "automation", "icon": ft.Icons.CODE, "title": "Automation",
     "blurb": "Turns your Azure test cases into a ready-to-run, self-healing UI "
              "test project — Selenium, Playwright, or Cypress — and pushes it to Git.",
     "details": [
         "Automation has its own Source & stories section (A) — pick one or more "
         "test plans and stories right here. It's independent of Setup's own "
         "selection, so the screen unlocks as soon as you're connected to a "
         "project; Generate stays disabled until at least one plan and one story "
         "are picked.",
         "Automation converts the test cases from your selection into a complete, "
         "runnable automation project with minimal human involvement. It compiles "
         "each test case's steps into a framework-neutral intent model, sequences "
         "the cases sensibly (logged-out validations → the successful login → the "
         "authenticated app cases, all in one browser), and emits a full project.",
         "Before sequencing, duplicate test cases within a story (same scenario, "
         "different wording) are skipped — only the most complete case of each "
         "duplicate set is carried forward — so you don't get two generated tests "
         "for the same thing. Nothing is deleted from Azure DevOps; it's a local, "
         "skip-only check. The Activity log names the story currently being "
         "checked/sequenced and how long each step took, so a multi-story run "
         "stays trackable end to end.",
         "An optional report email (section F) sends a run summary — stories, "
         "test cases, duplicates skipped, self-healed locators, elapsed time, and "
         "the full activity log — to whoever you pick, once a generation finishes "
         "or fails, using the same Azure member picker as the other report-email "
         "fields.",
         "Pick your Test framework at the top: Selenium (Java + TestNG), Playwright "
         "(JavaScript), or Cypress (JavaScript). All three share the same "
         "AI-generated steps and the same self-healing locators — only the emitted "
         "project differs, so you can standardize on whichever your team runs.",
         "Self-healing means low maintenance: each step gets a stable seed locator, "
         "and if that locator breaks at run time the framework asks your AI provider "
         "to re-find the element on the live page, verifies it, uses it, and writes "
         "it back into a committed locators.json — so the AI is asked at most once "
         "per step and every later run (on any machine) reuses it.",
         "The generated project is environment-agnostic: the app URLs and "
         "credentials come from a git-ignored config file or environment variables "
         "(config.properties for Selenium, .env for Playwright / Cypress), never "
         "baked into the code — so the same project runs against dev, test, staging "
         "or prod with no regeneration. A manifest lets a stopped or re-run job "
         "resume instead of starting over, and any test file you hand-edit is kept "
         "(a fresh copy is saved beside it) rather than overwritten.",
         "Push (section D) always resyncs README.md/.gitignore to match the "
         "project's actual on-disk framework right before committing — including a "
         "folder you Browse to and push without regenerating — so a stale or "
         "hand-edited README never ships. The first time a brand-new output folder "
         "is pushed, QA Studio also creates the GitHub repo for you if it doesn't "
         "exist yet (GitHub only; your PAT needs repo-creation rights — see the "
         "PAT info icon next to Access token). A push rejected because the remote "
         "has newer commits offers a one-click Force-push retry instead of just "
         "showing raw git output.",
         "Stop aborts right away — it doesn't wait for the test case currently being "
         "compiled to finish. The Activity panel (right) shows every step live, in "
         "the order it actually happened; Copy and Clear (top of the log) are always "
         "pinned there regardless of how far you've scrolled.",
     ],
     "points": [
         "Own Source & stories picker (section A) — unlocks with just a "
         "connection + project; Generate needs a plan and a story picked here.",
         "Duplicate test cases are skipped before sequencing (skip-only, nothing "
         "deleted from Azure); the log names the current story and timing for "
         "each step so multi-story runs stay trackable.",
         "Optional report email (section F) — stories, test cases, skipped "
         "duplicates, self-healed locators, and elapsed time, sent on completion.",
         "Choose the target: Selenium (Java/TestNG), Playwright (JS), or Cypress "
         "(JS) — same steps + self-healing, different emitted project.",
         "Runtime self-healing: a broken locator is re-found by the AI on the live "
         "page and saved back to a committed locators.json for reuse.",
         "Environment-agnostic: URLs/creds come from config.properties or .env, so "
         "one project runs against any environment with no regeneration.",
         "Pushes to your Git repo to open in your IDE; resumes on re-run and never "
         "clobbers hand-edited tests. Stop aborts immediately (mid-case, not just "
         "between cases); Pause / Resume also available, with auto-pause if the AI "
         "runs out of credit.",
         "Push auto-syncs README/.gitignore to the real framework and auto-creates "
         "the GitHub repo the first time a new output folder is pushed.",
         "Browse (folder D) opens a native OS folder picker to point at an existing "
         "generated project — handy for pushing a forgotten run without "
         "regenerating.",
         "Copy / Clear log buttons are pinned at the top of the Activity panel, "
         "always visible above the scrolling log.",
     ]},
    {"key": "links", "icon": ft.Icons.BOOKMARK_BORDER, "title": "Useful Links",
     "blurb": "Save links to the boards and apps you use, and open them in one click.",
     "details": [
         "Useful Links is a small personal launcher: add any URL with a friendly "
         "name and open it in one click. Links open in your browser, brought in "
         "front of the app, and are saved privately to your account on this device.",
         "Every account also sees an \"Official\" QA Studio link at the top of the "
         "list — a shared entry pinned by the app itself so the project's site is "
         "always one click away, even on a brand-new account with no saved links yet. "
         "Everyone can open it; only Admins can edit its name/URL or remove it (via "
         "the same Edit/Delete icons your own custom links use). Removing it only "
         "hides it for that admin's own account — there's no shared/server list "
         "behind Useful Links, so it doesn't affect what other users see.",
     ],
     "points": [
         "Add any URL with a friendly name.",
         "Links open in your browser, in front of the app.",
         "Your links are private to your account.",
         "An \"Official\" QA Studio link is pinned for everyone; only Admins can "
         "edit or delete it.",
     ]},
    {"key": "users", "icon": ft.Icons.PEOPLE_OUTLINE, "title": "Users",
     "blurb": "Admins manage who can access QA Studio and what each person can do.",
     "details": [
         "Users is the admin-only access-control screen. Assign each person a role / "
         "set of capabilities, and revoke access when needed — a revocation takes "
         "effect on the signed-in user within about 25 seconds.",
         "Non-admins never see this screen, and Viewers see the rest of the app "
         "read-only.",
     ],
     "points": [
         "Assign a role / capabilities per user.",
         "Revoke access — it takes effect on the signed-in user within ~25s.",
         "Viewers see screens read-only; only Admins see this screen.",
     ]},
    {"key": "settings", "icon": ft.Icons.SETTINGS_OUTLINED, "title": "Settings",
     "blurb": "Preferences for this device — theme, defaults, caches, and (for "
              "Admins) the idle auto-logout policy.",
     "details": [
         "Settings holds this device's preferences: the light / dark theme, your "
         "default output language, and the default generator (Titles vs Steps) that "
         "Setup starts from.",
         "If stories or plans look out of date you can clear the cached Azure data "
         "to force a fresh pull. Admins additionally control the security policy: an "
         "idle auto-logout of Off / 5 / 15 / 30 / 60 minutes, with a 60-second "
         "warning that lets you stay signed in before it logs you out.",
     ],
     "points": [
         "Theme (light / dark), default language, and default generator (Titles vs Steps).",
         "Clear cached Azure data when stories or plans look stale.",
         "Admins: idle auto-logout (Off / 5 / 15 / 30 / 60 min) with a 60-second "
         "stay-signed-in warning.",
     ]},
    {"key": "providers", "icon": ft.Icons.BOLT, "title": "AI Providers & keys",
     "blurb": "How the AI connection works, which providers are supported, and why "
              "Automation's healing key lives in your IDE rather than the app.",
     "details": [
         "QA Studio works with many AI providers — Anthropic, OpenAI, Azure OpenAI, "
         "Google Gemini, NVIDIA, DeepSeek, Qwen, Groq, Cerebras, OpenRouter, "
         "Mistral, Ollama (local) and more. In the provider dropdown the free-tier "
         "providers are grouped above the paid ones, and each entry is marked "
         "● active / ○ inactive by whether you've saved a key for it.",
         "Each provider keeps its own saved key. Most use a single key for all their "
         "models; NVIDIA issues a distinct key per model, so its key is stored per "
         "model and the model dropdown marks which specific models have a key. Once a "
         "valid key is saved the model catalogue is fetched live from the provider.",
         "One important separation: Automation's runtime self-healing calls the AI "
         "from the generated tests, which run on their own (in your IDE / CI, not "
         "inside QA Studio). Those tests read their key from an environment variable "
         "(QA_AI_API_KEY) — so the healing key is set where the tests run, separate "
         "from the key you save in the app.",
     ],
     "points": [
         "Many providers supported; free tiers (Gemini, NVIDIA, Groq, Cerebras, "
         "OpenRouter, Mistral, Ollama) are grouped above paid ones.",
         "Providers and (for NVIDIA) individual models are marked ● active when they "
         "have a saved key.",
         "Each provider keeps its own key; the model list is fetched live once a "
         "valid key is saved.",
         "Automation's self-healing key is set in your IDE (QA_AI_API_KEY env var), "
         "separate from the app, because the generated tests run on their own.",
     ]},
]


def _content(feat):
    """Right-pane controls for one feature: title, blurb, bullet points."""
    rows = [
        ft.Row([
            ft.Container(ft.Icon(feat["icon"], size=20, color=T.VIOLET_INK),
                         width=40, height=40, bgcolor=T.VIOLET_SOFT, border_radius=10,
                         alignment=ft.Alignment.CENTER),
            ft.Text(feat["title"], size=19, weight=ft.FontWeight.BOLD, color=T.INK),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=10),
        ft.Text(feat["blurb"], size=13.5, color=T.INK_2, weight=ft.FontWeight.W_500),
        ft.Container(height=14),
    ]
    for para in feat.get("details", []):
        rows.append(ft.Text(para, size=13, color=T.INK_2, weight=ft.FontWeight.W_500))
        rows.append(ft.Container(height=10))
    if feat.get("points"):
        rows.append(ft.Text("KEY POINTS", size=10.5, weight=ft.FontWeight.W_800,
                            color=T.INK_3))
        rows.append(ft.Container(height=8))
    for p in feat["points"]:
        rows.append(ft.Row([
            ft.Container(width=7, height=7, border_radius=4, bgcolor=T.VIOLET,
                         margin=ft.Margin.only(top=6)),
            ft.Text(p, size=13, color=T.INK_2, weight=ft.FontWeight.W_500, expand=True),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START))
        rows.append(ft.Container(height=9))
    return rows


def show(app, initial=None):
    """Open the searchable feature guide as a modal."""
    app._helpg_sel = initial or FEATURES[0]["key"]
    app._helpg_query = ""

    nav_col = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO, expand=True)
    content_col = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    empty_hint = ft.Text("No features match your search.", size=12.5, color=T.INK_3)

    def _nav_item(feat):
        selected = (feat["key"] == app._helpg_sel)
        return ft.Container(
            ft.Row([
                ft.Icon(feat["icon"], size=15,
                        color=(T.VIOLET_INK if selected else T.INK_2)),
                ft.Text(feat["title"], size=12.5,
                        weight=ft.FontWeight.BOLD,
                        color=(T.VIOLET_INK if selected else T.INK_2)),
            ], spacing=9),
            on_click=lambda e, k=feat["key"]: _select(k),
            ink=True, border_radius=8,
            padding=ft.Padding.symmetric(vertical=9, horizontal=10),
            bgcolor=(T.VIOLET_SOFT if selected else None),
            border=ft.Border.all(1, T.VIOLET if selected else ft.Colors.TRANSPARENT))

    def _matches(feat, q):
        if not q:
            return True
        hay = (feat["title"] + " " + feat["blurb"] + " " + " ".join(feat["points"])).lower()
        return q in hay

    def _refresh():
        q = (app._helpg_query or "").strip().lower()
        shown = [f for f in FEATURES if _matches(f, q)]
        nav_col.controls = [_nav_item(f) for f in shown] or [empty_hint]
        cur = next((f for f in FEATURES if f["key"] == app._helpg_sel), FEATURES[0])
        content_col.controls = _content(cur)
        try:
            nav_col.update(); content_col.update()
        except Exception:
            pass

    def _select(key):
        app._helpg_sel = key
        _refresh()

    def _on_search(e):
        app._helpg_query = e.control.value or ""
        _refresh()

    search = ft.TextField(
        hint_text="Search features…", on_change=_on_search,
        prefix_icon=ft.Icons.SEARCH, dense=True, text_size=12.5,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
        bgcolor=T.CARD)

    _refresh()

    left = ft.Container(
        ft.Column([
            search,
            ft.Container(height=8),
            ft.Container(nav_col, expand=True),
        ], spacing=0, expand=True),
        width=232, padding=ft.Padding.only(right=14),
        border=ft.Border.only(right=ft.BorderSide(1, T.BORDER)))

    right = ft.Container(content_col, expand=True,
                         padding=ft.Padding.only(left=18, right=4))

    body = ft.Container(
        ft.Row([left, right], spacing=0, expand=True,
               vertical_alignment=ft.CrossAxisAlignment.STRETCH),
        width=880, height=560)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.MENU_BOOK_OUTLINED, size=18, color=T.VIOLET_INK),
                         width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text("Feature guide", size=16, weight=ft.FontWeight.W_800, color=T.INK, expand=True),
        ], spacing=10),
        content=body,
        actions=[ghost_btn("Close", on_click=lambda e: app._close_dialog())],
        actions_alignment=ft.MainAxisAlignment.END)
    app._show_dialog(dlg)
