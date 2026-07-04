"""help_guide.py — In-app feature guide.

A searchable Help overlay: a left-hand nav lists every feature; selecting one
shows a short briefing (what it does + a few key points). Opened from the nav
rail's "Help & guide" button via app._open_help_guide().

Self-contained: FEATURES holds the content; show(app) builds the modal and wires
search + selection with in-place control updates (no full re-render).
"""
import flet as ft
import theme as T


# Each feature: key, icon, title, one-line blurb, and 2-4 key points.
FEATURES = [
    {"key": "setup", "icon": ft.Icons.TUNE, "title": "Setup",
     "blurb": "Connect your AI provider and Azure DevOps, then pick a project, "
              "test plan, and user stories. Everything else runs on this selection.",
     "points": [
         "Choose an AI provider + model and save its API key — each provider "
         "stores its own key (NVIDIA stores one per model).",
         "Add your Azure DevOps PAT and organization to load projects, plans and stories.",
         "Pick a project → test plan → stories, then choose Titles or Steps and the "
         "output language (Arabic / English).",
     ]},
    {"key": "run", "icon": ft.Icons.PLAY_ARROW, "title": "Run",
     "blurb": "Generates test cases into your Azure test plan — either test-case "
              "titles or full step-by-step steps.",
     "points": [
         "Titles: reads each story + acceptance criteria and writes NEW test-case "
         "titles, skipping duplicates it finds in the suite.",
         "Steps: writes detailed steps (precondition / action / expected) into the "
         "story's test cases.",
         "Live progress with elapsed time and ETA, plus a Stop button you can hit "
         "any time — it dedupes against the cases already there.",
     ]},
    {"key": "report", "icon": ft.Icons.DESCRIPTION_OUTLINED, "title": "Report",
     "blurb": "The results of the last run: how many were created, skipped or "
              "failed, a per-story breakdown, and anything flagged for review.",
     "points": [
         "Created / Skipped / Failed counters and a per-story pass indicator.",
         "'Needs your review' lists anything the AI wasn't confident about.",
         "Email the report, or open the test plan directly in Azure DevOps.",
     ]},
    {"key": "regression", "icon": ft.Icons.FACT_CHECK_OUTLINED, "title": "Regression Plan",
     "blurb": "Build a regression plan from your existing test plans and their stories.",
     "points": [
         "Pick the source test plans — it pulls in their stories and test cases.",
         "Assemble the regression scope and estimate the test effort.",
         "Uses the same connection/selection you set up on the Setup screen.",
     ]},
    {"key": "testplan", "icon": ft.Icons.ASSIGNMENT_OUTLINED, "title": "Sprint Plan",
     "blurb": "Plan and estimate test effort across a sprint's stories.",
     "points": [
         "Pulls the sprint's user stories from Azure.",
         "Estimate hours per story and assign a tester to each.",
         "A quick way to size testing for the whole sprint in one place.",
     ]},
    {"key": "titles", "icon": ft.Icons.ARTICLE_OUTLINED, "title": "Sprint Report",
     "blurb": "A sprint-closure report: stories grouped by status plus a bug "
              "summary, in Arabic or English.",
     "points": [
         "Summarizes the sprint's stories by status and its bugs.",
         "Choose Arabic or English output.",
         "Export or email the finished report.",
     ]},
    {"key": "automation", "icon": ft.Icons.CODE, "title": "Automation",
     "blurb": "Turns your Azure test cases into a ready-to-run, self-healing "
              "Selenium (Java + TestNG) project and pushes it to Git.",
     "points": [
         "Compiles each test case's steps into intents, sequences them "
         "(logged-out → login → app), and generates a full Maven project.",
         "Every locator gets a runtime self-healer: if it breaks when you run "
         "the tests, the AI re-finds the element on the live page and caches it.",
         "Pushes the project to your Git repo so you can open and run it in IntelliJ. "
         "Pause / Resume / Stop any time (it auto-pauses if the AI runs out of credit).",
     ]},
    {"key": "links", "icon": ft.Icons.BOOKMARK_BORDER, "title": "Useful Links",
     "blurb": "Save links to the boards and apps you use, and open them in one click.",
     "points": [
         "Add any URL with a friendly name.",
         "Links open in your browser, in front of the app.",
         "Your links are private to your account.",
     ]},
    {"key": "users", "icon": ft.Icons.PEOPLE_OUTLINE, "title": "Users",
     "blurb": "Admins manage who can access QA Studio and what each person can do.",
     "points": [
         "Assign a role / capabilities per user.",
         "Revoke access — it takes effect on the signed-in user within ~25s.",
         "Viewers see screens read-only; only Admins see this screen.",
     ]},
    {"key": "settings", "icon": ft.Icons.SETTINGS_OUTLINED, "title": "Settings",
     "blurb": "Preferences for this device — theme, defaults, caches, and (for "
              "Admins) the idle auto-logout policy.",
     "points": [
         "Theme (light / dark), default output language, and default generator "
         "(Titles vs Steps).",
         "Clear cached Azure data if stories or plans look out of date.",
         "Admins: set idle auto-logout (Off / 5 / 15 / 30 / 60 min) — a 60-second "
         "warning lets you stay signed in before it logs you out.",
     ]},
    {"key": "providers", "icon": ft.Icons.BOLT, "title": "AI Providers & keys",
     "blurb": "How the AI connection works, and why you set a key here vs in the IDE.",
     "points": [
         "Pick any supported provider — free ones (Gemini, NVIDIA, Groq, Cerebras, "
         "OpenRouter, Mistral, Ollama) are grouped above the paid ones.",
         "Each provider keeps its own saved key; the model list is fetched live "
         "once a valid key is saved.",
         "Automation's runtime self-healing uses a key set in your IDE (env var) — "
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
        modal=True, bgcolor=T.CARD,
        shape=ft.RoundedRectangleBorder(radius=T.R_LG),
        title=ft.Row([
            ft.Container(ft.Icon(ft.Icons.MENU_BOOK_OUTLINED, size=18, color=T.VIOLET_INK),
                         width=34, height=34, bgcolor=T.VIOLET_SOFT, border_radius=9,
                         alignment=ft.Alignment.CENTER),
            ft.Text("Feature guide", size=15, weight=ft.FontWeight.BOLD, color=T.INK, expand=True),
        ], spacing=10),
        content=body,
        actions=[_close_btn(app)],
        actions_alignment=ft.MainAxisAlignment.END)
    app._show_dialog(dlg)


def _close_btn(app):
    return ft.Container(
        ft.Text("Close", size=13, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
        on_click=lambda e: app._close_dialog(),
        bgcolor=T.VIOLET, border_radius=T.R, height=40,
        padding=ft.Padding.symmetric(horizontal=22, vertical=0),
        alignment=ft.Alignment.CENTER, ink=True)
