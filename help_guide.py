"""help_guide.py — In-app feature guide.

A searchable Help overlay: a left-hand nav lists every feature; selecting one
shows a short briefing (what it does + a few key points). Opened from the nav
rail's "Help & guide" button via app._open_help_guide().

Self-contained: FEATURES holds the content; show(app) builds the modal and wires
search + selection with in-place control updates (no full re-render).
"""
import flet as ft
import theme as T
import platform_caps
from ui import ghost_btn
import strings
import engine as E
import release_notes


# Each feature: key, icon, title, one-line blurb, `details` (fuller prose
# paragraphs), and `points` (key bullets).
FEATURES = [
    {"key": "whats_new", "nav_key": "help_whats_new_nav", "icon": ft.Icons.NEW_RELEASES, "title": "What's new",
     # Content is resolved from release_notes at render time. Keeping a second
     # version-specific copy here let this Help page become stale and, worse,
     # made the release checker falsely report that a complete popup note was
     # missing after release.bat had already stamped it.
     "blurb": "Highlights for the version installed on this device.",
     "details": [], "points": []},
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
         "QA Studio isn't Azure-only: a 'Test management backend' selector at the top "
         "of Setup lets you connect Azure DevOps, Jira + Xray, or a hybrid that reads "
         "stories from Azure or Jira and writes the test cases into TestRail "
         "(Azure → TestRail, Jira → TestRail). Each backend keeps its own credentials, "
         "scoped to your account, and switching backends swaps the credential fields "
         "below. Everything downstream — Run, Report, the plans, Task Manager — adapts "
         "to whichever backend is connected. (Jira + Zephyr Scale is temporarily hidden "
         "while its API access is finalized.)",
         "Finally choose what to generate — test-case Titles or full Steps — and the "
         "output language — any of seven (English, Arabic, French, Spanish, German, Turkish, Dutch). Changing provider, model, project or "
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
         "Pick your Test management backend first (Azure DevOps, Jira + Xray, or a "
         "Jira/Azure → TestRail hybrid); each keeps its own credentials.",
         "Azure DevOps PAT + organization load projects; pick project → test plan → "
         "stories.",
         "Choose Titles vs Steps and the output language (English, Arabic, French, Spanish, German, Turkish, Dutch) — the language picked on Setup applies to that run only, and the saved defaults also live on "
         "the Settings screen.",
         "Email sender (address / name / Gmail App Password): Admin-only, configured "
         "once and shared with every user — not a per-device setting.",
     ]},
    {"key": "run", "icon": ft.Icons.PLAY_ARROW, "title": "Run",
     "blurb": "Generates test cases into your connected backend's test plan — either "
              "test-case titles or full step-by-step steps.",
     "details": [
         "Run reads the stories you selected on Setup together with their acceptance "
         "criteria, and writes results straight back into your connected backend's "
         "test plan — Azure DevOps, Jira + Xray, or TestRail. It works in one of two "
         "modes depending on your Setup choice.",
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
         "Stop at any point; work already written to your backend is kept.",
         "This local Run screen is DESKTOP ONLY. On mobile the work runs on the "
         "server instead: flip Setup's \"Run remotely\" toggle and watch progress "
         "on the Remote Runs screen — the results land in your backend just the same.",
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
         "test plan in your tracker to see the generated cases in context.",
         "Report is DESKTOP ONLY — it summarizes a local Run. On mobile the "
         "equivalent live results appear on the Remote Runs screen as the server "
         "run progresses.",
     ],
     "points": [
         "Created / Skipped / Failed counters and a per-story pass indicator.",
         "'Needs your review' surfaces low-confidence items for a quick check.",
         "Email the report, or open the test plan directly in your tracker.",
     ]},
    {"key": "remote_runs", "icon": ft.Icons.CLOUD_QUEUE_OUTLINED, "title": "Remote Runs",
     "blurb": "Watch test-case generation that runs on the server (GitHub "
              "Actions) instead of your own machine — this is how runs work on mobile.",
     "details": [
         "Remote Runs is the live status and activity viewer for runs executed "
         "server-side. Instead of your own machine doing the work (that's the "
         "desktop's local Run screen), the job is dispatched to a GitHub Actions "
         "worker that executes with your synced credentials and writes the results "
         "straight into your connected backend — so it keeps going even after you "
         "close the app or lock your phone.",
         "On mobile this is the primary way to run: Setup's \"Run remotely\" toggle "
         "queues the job, and Remote Runs streams each test case as it's generated, "
         "with the same one-line-per-case log the desktop shows. On desktop it's "
         "optional — you can still run locally on the Run screen, or offload to the "
         "server from here.",
         "A remote run needs your credentials synced once first (Settings → Remote "
         "runs → Sync) so the worker can act as you: your Azure PAT, the AI provider "
         "and its key, and optionally the Gmail sender used to email the report.",
     ],
     "points": [
         "Server-side execution on GitHub Actions — runs continue with the app "
         "closed or the phone locked.",
         "The mobile equivalent of the desktop's local Run + Report, driven by "
         "Setup's \"Run remotely\" toggle.",
         "Live per-test-case log and status; also available (optional) on desktop.",
         "Requires a one-time credential sync (Settings → Remote runs) so the "
         "worker runs as you.",
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
              "summary, in any of the seven supported languages.",
     "details": [
         "Sprint Report produces an end-of-sprint summary: the sprint's stories "
         "grouped by their status, together with a bug summary, formatted for "
         "sharing. Pick any of the seven languages and the whole report — including "
         "right-to-left layout for Arabic — is generated accordingly.",
         "Export the finished report or email it directly to your stakeholders.",
     ],
     "points": [
         "Groups the sprint's stories by status and summarizes its bugs.",
         "Multi-language output (seven languages), with correct RTL layout for Arabic.",
         "Export or email the finished report.",
     ]},
    {"key": "task_manager", "icon": ft.Icons.TASK_ALT, "title": "Task Manager",
     "blurb": "Per-person task workload reports (by sprint or date range) and bulk "
              "child-task creation for one or more stories.",
     "details": [
         "Task Manager has two tools sharing one sprint picker: a per-person task "
         "workload report, and bulk creation of child Tasks under selected User "
         "Stories.",
         "The workload report is scoped by either a Sprint or a Date range — pick "
         "one with the toggle at the top of the report card, pick who it's for, "
         "then Calculate. It totals every 'Task' work item's Original Estimate and "
         "Completed Work for that person over the chosen period, with a per-task "
         "breakdown table and a completion-percentage bar.",
         "The finished report exports to Excel, PDF or JSON, or emails using the "
         "same report design as Regression Plan / Sprint Plan's emails — with the "
         "assignee shown by their display name (e.g. \"Ahmed Sayed\"), never their "
         "raw email, in the report itself, every export, and the email subject/"
         "filename.",
         "Child-task creation selects one or more User Stories from the same "
         "sprint, then adds a batch of up to 10 tasks per story, created in a "
         "single run. The fields differ by connected backend:",
         "On Azure DevOps each task row has a title, due date, original estimate "
         "and completed work, all assigned to one chosen person.",
         "On Jira / Xray each row instead builds a real Jira SUB-TASK inline: pick "
         "the sub-task Work type, type a Summary, then use 'Add a field' to attach "
         "any of the sub-task's own Jira Details fields — Assignee, Reporter, "
         "Labels, Story point estimate, End Date, Completed work, Description, "
         "Flagged, and any custom field your project defines. Each field renders "
         "the right control (people picker, dropdown, date picker, number, text) "
         "and is validated/shaped to Jira's format on create (dates and the ADF "
         "rich-text of Description are handled for you). The Azure-only estimate/"
         "completed-work fields are hidden on these backends, and 'Assign all to' "
         "is replaced by the per-sub-task Assignee field.",
         "The Sprint field is fully disabled while the toggle is set to Date range. "
         "Since child-task creation always needs a sprint (dates have no "
         "equivalent notion of \"which stories\"), the whole 'Create child tasks' "
         "section shows as locked — \"Pick a sprint to load stories\" — whenever "
         "Date range is active; switch the toggle back to Sprint to unlock it.",
     ],
     "points": [
         "Toggle between a Sprint or a Date range to scope the workload report; "
         "Calculate totals Original Estimate & Completed Work for the chosen person.",
         "Per-task breakdown table, completion bar, and export to Excel / PDF / "
         "JSON, or email.",
         "Reports, exports and emails show the assignee's display name, not their "
         "raw email address.",
         "Azure: bulk-create child Tasks (up to 10 per story, title/due/estimate/"
         "completed) assigned to one person in one run.",
         "Jira / Xray: build real sub-tasks inline — pick a Work type + Summary, "
         "then 'Add a field' to fill any Jira Details field (Assignee, Reporter, "
         "Labels, Story points, dates, Description, custom fields).",
         "The Sprint field disables while Date range mode is active — switch back "
         "to Sprint to change it.",
     ]},
    {"key": "automation", "icon": ft.Icons.CODE, "title": "Automation",
     "blurb": "Turns your Azure test cases into a ready-to-run, self-healing UI "
              "test project — Selenium, Playwright, or Cypress — and pushes it to Git.",
     "details": [
         "Automation is DESKTOP ONLY: it generates a real project into a local "
         "folder and pushes it with git, which needs a desktop filesystem and "
         "toolchain — so the screen isn't shown on mobile at all.",
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
    {"key": "organizations", "icon": ft.Icons.BUSINESS, "title": "Organizations",
     "blurb": "Set up each organization’s identity, shared projects, teams, and access scope.",
     "details": [
         "Organizations is the workspace administration screen. Super Admins can "
         "create and manage every organization; Organization Managers can manage "
         "only the organization assigned to their own account.",
         "For each organization, set its identity and defaults, then create manual "
         "projects or import discovered projects from an allowed test-management "
          "backend. Create teams to group people, and use the Users screen to assign "
          "project and team membership. Those assignments determine which organization "
          "projects are available to a user in Setup.",
         "To frame identity images, click your desktop-header profile picture, or open "
         "Organizations and click its logo (or Upload logo). Choose an image, zoom or "
         "rotate it, then drag after zooming to place the visible area before saving. "
         "QA Studio reprocesses the edited image before upload.",
     ],
     "points": [
         "Set allowed email domains, locale, time zone, support email, retention, and a logo.",
         "Add, edit, or remove organization projects; imported projects show their backend source.",
          "Create and edit teams, then assign users to projects and teams on the Users screen.",
          "Organization Managers are restricted to their assigned organization; Super Admins can manage all organizations.",
         "Profile pictures and organization logos use the same zoom, rotate, and drag-to-position editor.",
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
         "On mobile, Device Security adds \"Require biometric/PIN unlock\". With it "
         "on, reopening the app asks for your fingerprint / Face / PIN before it "
         "restores your session — and signing out keeps you signed out until you "
         "unlock again, rather than dropping you straight back in. Dismissing the "
         "prompt is always allowed: you simply sign in with your email and password "
         "instead, and the setting stays on for next time. Your credentials and "
         "session are held in the device's own secure keystore.",
         "Data & Diagnostics has a Share log button. QA Studio records unhandled "
         "errors locally — including failures that leave no visible message — and "
         "this hands that file to you so it can be attached to a bug report. It is "
         "never uploaded anywhere on its own.",
     ],
     "points": [
         "Theme (light / dark), default language, and default generator (Titles vs Steps).",
         "Clear cached Azure data when stories or plans look stale.",
         "Admins: idle auto-logout (Off / 5 / 15 / 30 / 60 min) with a 60-second "
         "stay-signed-in warning.",
         "Mobile: biometric / PIN unlock on reopen; credentials and session kept in "
         "the device keystore. Cancelling the prompt falls back to email + password.",
         "Share log — send the local diagnostics file when reporting a problem.",
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
         "Many providers supported; genuinely free tiers (Gemini, Groq, Cerebras, "
         "OpenRouter, Mistral, Ollama) are grouped above paid ones. Providers that "
         "only give expiring trial credits (NVIDIA, GLM, MiniMax, Qwen, DeepSeek) "
         "sit under Paid, since they stop working once the credit runs out.",
         "Providers and (for NVIDIA) individual models are marked ● active when they "
         "have a saved key.",
         "Each provider keeps its own key; the model list is fetched live once a "
         "valid key is saved.",
         "Automation's self-healing key is set in your IDE (QA_AI_API_KEY env var), "
         "separate from the app, because the generated tests run on their own.",
     ]},
    {"key": "performance", "icon": ft.Icons.SPEED, "title": "Performance",
     "blurb": "Turn real requests into an Apache JMeter load test, run it locally or "
              "across distributed engines, and read a plain-language report of the result.",
     "details": [
         "Performance load-tests your app with JMeter. Instead of guessing requests "
         "from prose, you feed it the REAL traffic of a user journey, two ways: import "
         "a HAR (in Chrome DevTools → Network, do the flow once, then 'Save all as HAR'), "
         "or paste one or more 'Copy as cURL' commands. Both become exact requests — "
         "method, URL, headers and body — so the test faithfully replays what the app "
         "actually does. 'Add to plan' lets you combine several captures into one plan.",
         "Set the load profile — virtual users, ramp-up, hold duration — and pass/fail "
         "budgets (p95 and error rate, optionally p99 and minimum throughput). Generate "
         "& Emit builds the JMeter project; Run JMeter executes it and streams live "
         "progress. For thousands of users, list distributed engines (hosts running "
         "jmeter-server) and the load is split across them, since one machine caps out "
         "at a few hundred threads.",
         "For authenticated tests, three tools cover per-user credentials: an Auth "
         "header (use 'Bearer {{token}}' with a Data CSV so each user carries its own "
         "token), an in-test Login step (each virtual user logs in at run time and reuses "
         "its own fresh token), and a Prepare-tokens helper that logs a whole CSV of "
         "users in up front — it can auto-detect your login API from a login HAR so you "
         "don't configure it by hand. Parameterize turns captured literals into per-user "
         "{{variables}}, and Correlation extracts a value from one response (a cart id, "
         "CSRF token) to reuse in later requests.",
         "After a run you get two reports: QA Studio's one-page summary — a PASS/FAIL "
         "verdict in plain English, a metric grid, a response-time spread, a per-request "
         "breakdown, a 'why requests failed' section (status codes + messages), and a "
         "glossary — which you can export or email (like the sprint/regression reports); "
         "and JMeter's own interactive dashboard. A run-history list tracks p95 and "
         "errors across runs in the session.",
     ],
     "points": [
         "Sources are real requests: Import HAR or Paste cURL — no prose guessing.",
         "Load profile: users, ramp, duration; budgets: p95, error rate, optional p99 "
         "and min throughput decide the PASS/FAIL gate.",
         "Per-user auth: 'Bearer {{token}}' + a Data CSV, an in-test Login step, or the "
         "Prepare-tokens helper (auto-detects your login API from a login HAR).",
         "Parameterize (literal => {{var}}) and Correlation (var = $.json.path @ /url) "
         "handle per-user and response-derived values.",
         "Run locally, or across distributed jmeter-server engines for large loads.",
         "Two reports — QA Studio's plain-language summary (export/email) and JMeter's "
         "interactive dashboard — plus a failure breakdown and run history.",
         "Needs Apache JMeter + a Java runtime installed; the preflight check confirms "
         "both before you run.",
     ]},
]


# Which platform each screen runs on. "both" = mobile & desktop; "desktop" =
# desktop-only (local Run/Report and Automation need a real filesystem/toolchain
# — see platform_caps.has_automation() and _nav_items_visible() which hide Run/
# Report on mobile). Kept as a lookup so the FEATURES entries above stay purely
# about content; the guide reads this to draw a "Desktop only" / "Mobile &
# desktop" badge on every feature so it's obvious where each screen is available.
PLATFORM_OF = {
    "setup": "both", "run": "desktop", "report": "desktop",
    "remote_runs": "both", "regression": "both", "testplan": "both",
    "titles": "both", "task_manager": "both", "automation": "desktop",
    "links": "both", "organizations": "both", "users": "both", "settings": "both", "providers": "both",
}

PLATFORM_META = {
    "both": ft.Icons.DEVICES,
    "desktop": ft.Icons.DESKTOP_WINDOWS_OUTLINED,
    "mobile": ft.Icons.PHONE_IPHONE_OUTLINED,
}


def _platform_of(feat):
    return PLATFORM_OF.get(feat.get("key"), "both")


def _platform_badge(plat, compact=False):
    """A small themed pill: 'Mobile & desktop' (green) or 'Desktop only'
    (amber). Amber = the not-everywhere case, so a desktop-only screen visibly
    stands out from the ones that work everywhere."""
    icon = PLATFORM_META.get(plat, PLATFORM_META["both"])
    label = strings.t("help_plat_" + plat)
    both = (plat == "both")
    ink = T.GREEN if both else T.AMBER
    bg = T.GREEN_SOFT if both else T.AMBER_SOFT
    return ft.Container(
        content=ft.Row([
            ft.Icon(icon, size=(11 if compact else 13), color=ink),
            ft.Text(label, size=(9.5 if compact else 11),
                    weight=ft.FontWeight.W_800, color=ink),
        ], spacing=4, tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=bg, border_radius=6,
        padding=ft.Padding.symmetric(
            horizontal=(6 if compact else 8), vertical=(2 if compact else 3)))


def _content(feat):
    """Right-pane controls for one feature: title, blurb, bullet points."""
    rows = [
        ft.Row([
            ft.Container(ft.Icon(feat["icon"], size=20, color=T.VIOLET_INK),
                         width=40, height=40, bgcolor=T.VIOLET_SOFT, border_radius=10,
                         alignment=ft.Alignment.CENTER),
            ft.Text(strings.t("help_" + feat["key"] + "_title",
                              **({"version": E.local_version()} if feat["key"] == "whats_new" else {})),
                    size=19, weight=ft.FontWeight.BOLD, color=T.INK),
            ft.Container(expand=True),
            _platform_badge(_platform_of(feat)),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=10),
        ft.Text(strings.t("help_" + feat["key"] + "_blurb"), size=13.5, color=T.INK_2, weight=ft.FontWeight.W_500),
        ft.Container(height=14),
    ]
    if feat["key"] == "whats_new":
        note_keys = (release_notes.exact_keys_for(E.local_version())
                     or release_notes.FALLBACK_RELEASE_NOTE_KEYS)
        rows += [
            ft.Text(strings.t("help_key_points"), size=10.5,
                    weight=ft.FontWeight.W_800, color=T.INK_3),
            ft.Container(height=8),
        ]
        for note_key in note_keys:
            rows.append(ft.Row([
                ft.Container(width=7, height=7, border_radius=4, bgcolor=T.VIOLET,
                             margin=ft.Margin.only(top=6)),
                ft.Text(strings.t(note_key), size=13, color=T.INK_2,
                        weight=ft.FontWeight.W_500, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START))
            rows.append(ft.Container(height=9))
        return rows
    for _i, para in enumerate(feat.get("details", [])):
        rows.append(ft.Text(strings.t("help_" + feat["key"] + "_d" + str(_i)), size=13, color=T.INK_2, weight=ft.FontWeight.W_500))
        rows.append(ft.Container(height=10))
    if feat.get("points"):
        rows.append(ft.Text(strings.t("help_key_points"), size=10.5, weight=ft.FontWeight.W_800,
                            color=T.INK_3))
        rows.append(ft.Container(height=8))
    for _j, p in enumerate(feat["points"]):
        rows.append(ft.Row([
            ft.Container(width=7, height=7, border_radius=4, bgcolor=T.VIOLET,
                         margin=ft.Margin.only(top=6)),
            ft.Text(strings.t("help_" + feat["key"] + "_p" + str(_j)), size=13, color=T.INK_2, weight=ft.FontWeight.W_500, expand=True),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START))
        rows.append(ft.Container(height=9))
    return rows


def show(app, initial=None):
    """Open the searchable feature guide as a modal."""
    available_features = [f for f in FEATURES
                          if f["key"] != "whats_new" or release_notes.keys_for(E.local_version())]
    app._helpg_sel = initial or available_features[0]["key"]
    app._helpg_query = ""
    # MOBILE uses a master→detail drill-in instead of the desktop two-pane
    # layout. The old layout was a fixed 880px-wide Row (232px nav + content);
    # on a ~390px phone the content pane was pushed off-screen to the right, so
    # tapping a topic updated a pane you couldn't see — "help doesn't open
    # anything but its navs". On mobile we show the nav list, and selecting a
    # topic swaps the whole body to that topic's content with a back link.
    _mobile = platform_caps.is_mobile()
    app._helpg_mobile_detail = bool(initial) and _mobile  # deep-linked → open detail

    nav_col = ft.Column(spacing=3, scroll=ft.ScrollMode.AUTO, expand=True)
    content_col = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    empty_hint = ft.Text(strings.t("help_no_match"), size=12.5, color=T.INK_3)
    # Mobile single-pane holder — _refresh swaps its content between the topic
    # LIST and the selected topic's DETAIL. Unused on desktop.
    mobile_holder = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    def _nav_item(feat):
        selected = (feat["key"] == app._helpg_sel)
        # Desktop-only screens get a tiny amber monitor icon on the right of
        # their nav row, so the "not everywhere" ones are scannable in the
        # list without opening each — the full badge shows in the detail pane.
        plat = _platform_of(feat)
        tail = ([ft.Container(expand=True),
                 ft.Icon(ft.Icons.DESKTOP_WINDOWS_OUTLINED, size=13, color=T.AMBER)]
                if plat == "desktop" else [])
        nav_label_key = feat.get("nav_key") or ("help_" + feat["key"] + "_title")
        return ft.Container(
            ft.Row([
                ft.Icon(feat["icon"], size=15,
                        color=(T.VIOLET_INK if selected else T.INK_2)),
                ft.Text(strings.t(nav_label_key), size=12.5,
                        weight=ft.FontWeight.BOLD,
                        color=(T.VIOLET_INK if selected else T.INK_2)),
                *tail,
            ], spacing=9),
            on_click=lambda e, k=feat["key"]: _select(k),
            ink=True, border_radius=8,
            padding=ft.Padding.symmetric(vertical=9, horizontal=10),
            bgcolor=(T.VIOLET_SOFT if selected else None),
            border=ft.Border.all(1, T.VIOLET if selected else ft.Colors.TRANSPARENT))

    def _matches(feat, q):
        if not q:
            return True
        hay = (strings.t("help_"+feat["key"]+"_title") + " " + strings.t("help_"+feat["key"]+"_blurb") + " " + " ".join(strings.t("help_"+feat["key"]+"_p"+str(_k)) for _k in range(len(feat["points"])))).lower()
        return q in hay

    def _refresh():
        q = (app._helpg_query or "").strip().lower()
        shown = [f for f in available_features if _matches(f, q)]
        nav_col.controls = [_nav_item(f) for f in shown] or [empty_hint]
        cur = next((f for f in available_features if f["key"] == app._helpg_sel), available_features[0])
        content_col.controls = _content(cur)
        if _mobile:
            if getattr(app, "_helpg_mobile_detail", False):
                mobile_holder.controls = [
                    ft.Container(
                        ft.Row([ft.Icon(ft.Icons.ARROW_BACK, size=16, color=T.VIOLET_INK),
                                ft.Text(strings.t("help_all_topics"), size=12.5,
                                        weight=ft.FontWeight.BOLD, color=T.VIOLET_INK)],
                               spacing=6, tight=True),
                        on_click=_back_to_list, ink=True, border_radius=8,
                        padding=ft.Padding.symmetric(vertical=8, horizontal=6),
                        margin=ft.Margin.only(bottom=6)),
                    content_col,
                ]
            else:
                mobile_holder.controls = [search, ft.Container(height=8), nav_col]
        try:
            if _mobile:
                mobile_holder.update()
            else:
                nav_col.update(); content_col.update()
        except Exception:
            pass

    def _select(key):
        app._helpg_sel = key
        if _mobile:
            app._helpg_mobile_detail = True   # drill into the topic's content
        _refresh()

    def _back_to_list(_e=None):
        app._helpg_mobile_detail = False
        _refresh()

    def _on_search(e):
        app._helpg_query = e.control.value or ""
        _refresh()

    search = ft.TextField(
        hint_text=strings.t("help_search_ph"), on_change=_on_search,
        prefix_icon=ft.Icons.SEARCH, dense=True, text_size=12.5,
        border_color=T.BORDER, focused_border_color=T.VIOLET, border_radius=T.R,
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
        bgcolor=T.CARD)

    _refresh()

    if _mobile:
        # Single pane sized to the (narrow) viewport — width=None lets the
        # dialog fit the phone instead of a fixed 880px that ran off-screen.
        body = ft.Container(mobile_holder, width=None, height=520)
    else:
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
            ft.Text(strings.t("help_feature_guide"), size=16, weight=ft.FontWeight.W_800, color=T.INK, expand=True),
        ], spacing=10),
        content=body,
        actions=[ghost_btn(strings.t("help_close"), on_click=lambda e: app._close_dialog())],
        actions_alignment=ft.MainAxisAlignment.END)
    app._show_dialog(dlg)
