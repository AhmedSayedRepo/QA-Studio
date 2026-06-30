"""theme.py — design tokens ported from the Claude Design system (styles.css).

Brand palette re-tuned to the QA Studio logo: an azure→royal-purple gradient.
The single primary is an indigo that bridges both ends of that gradient, so the
UI reads as one family with the mark. Variable names are kept (VIOLET*, etc.)
so call sites elsewhere don't change — only the values moved to indigo-blue.
"""

# ── Colors ────────────────────────────────────────────────────────────────────
BG        = "#FAFBFE"
CARD      = "#FFFFFF"
CARD_2    = "#F4F6FB"
BORDER    = "#E6E8F1"
BORDER_2  = "#EDEFF6"
INK       = "#181A24"
INK_2     = "#6E7180"
INK_3     = "#9FA2B2"

# Dark sidebar — deep teal-navy (so cyan accents read as neon on it)
RAIL      = "#0C1330"
RAIL_2    = "#142044"
RAIL_LINE = "#21314F"
RAIL_INK  = "#E8F3F8"
RAIL_DIM  = "#8198AC"

# Brand — CYAN primary (neon-cyan design language) + its tints. (Variable names
# kept as VIOLET* so call sites don't change — only the values are cyan now.)
VIOLET      = "#0E9CC0"   # primary (buttons, nav active, accents) — readable cyan
VIOLET_H    = "#0B7E9B"   # hover / pressed (darker cyan)
VIOLET_SOFT = "#D6F4FB"   # soft fill behind icons / badges (light cyan)
VIOLET_INK  = "#0B6E86"   # text on soft fills (deep cyan)
STORY       = "#22D3EE"   # story / log accent (bright neon cyan)

# Brand gradient endpoints — use for brand fills (tile fallbacks, hero accents)
BRAND_GRAD_1 = "#22D3EE"  # bright cyan
BRAND_GRAD_2 = "#0E7490"  # deep cyan

# ── Gradient stops (built into ft.LinearGradient by main.grad) ────────────────
GRAD_BRAND   = ["#22D3EE", "#0E7490"]   # bright → deep cyan (hero/brand bands)
GRAD_PRIMARY = ["#19BDDC", "#0E9CC0"]   # primary buttons / main CTAs (cyan)
GRAD_GREEN   = ["#27A866", "#178A4B"]   # export / confirm buttons — tints of GREEN (#1F9D57) so gradient + solid green buttons read as one tone
GRAD_RAIL    = ["#0E1430", "#080C1E"]   # sidebar (top → bottom) — deep teal-navy
GRAD_LOGO    = ["#22D3EE", "#0891B2"]   # logo tile + small brand chips (cyan)
GRAD_PAGE    = ["#F7F8FE", "#EDF0F8"]   # content background wash
GRAD_NAV_ACT = ["#0B89A8", "#0E9CC0"]   # active nav item highlight (cyan)

GREEN       = "#1F9D57"; GREEN_SOFT = "#E5F6EC"
RED         = "#E0474D"; RED_SOFT   = "#FCEBEC"
AMBER       = "#C2860C"; AMBER_SOFT = "#FAF1DD"

# ── Theme switching (light = default, dark = secondary) ───────────────────────
# Surfaces/inks live in a swappable palette so the whole app can flip at runtime.
# Brand colors (VIOLET*, GREEN/RED/AMBER, gradients, the dark sidebar) are shared.
MODE = "light"

_PALETTES = {
    "light": dict(
        BG="#FAFBFE", CARD="#FFFFFF", CARD_2="#F4F6FB", BORDER="#E6E8F1",
        BORDER_2="#EDEFF6", INK="#181A24", INK_2="#6E7180", INK_3="#9FA2B2",
        VIOLET_SOFT="#D6F4FB", VIOLET_INK="#0B6E86",
        GREEN_SOFT="#E5F6EC", RED_SOFT="#FCEBEC", AMBER_SOFT="#FAF1DD",
        GRAD_PAGE=["#F7F8FE", "#EDF0F8"]),
    "dark": dict(
        BG="#0B1024", CARD="#121A38", CARD_2="#1A2344", BORDER="#26365F",
        BORDER_2="#1F2A4A", INK="#EAF6FB", INK_2="#A9C0D2", INK_3="#7C90A8",
        VIOLET_SOFT="#0C3A47", VIOLET_INK="#67E8F9",
        GREEN_SOFT="#15331E", RED_SOFT="#3A1E20", AMBER_SOFT="#33280F",
        GRAD_PAGE=["#0B1024", "#070B1A"]),
}


def apply_theme(mode):
    """Swap the active surface/ink palette (light|dark). Brand tokens are kept.
    Re-render after calling so controls pick up the new values."""
    global MODE
    MODE = mode if mode in _PALETTES else "light"
    g = globals()
    for k, v in _PALETTES[MODE].items():
        g[k] = v
    return MODE

# ── Radii ─────────────────────────────────────────────────────────────────────
R_WIN = 16
R_LG  = 14
R     = 10
R_SM  = 7

# ── Fonts (loaded in main via fonts=) ─────────────────────────────────────────
F_UI   = "Manrope"
F_MONO = "JetBrains Mono"
F_AR   = "IBM Plex Sans Arabic"

# ── Navigation steps ──────────────────────────────────────────────────────────
NAV = [
    {"id": "setup",  "label": "Setup",  "ix": "01", "icon": "TUNE"},
    {"id": "run",    "label": "Run",    "ix": "02", "icon": "MONITOR_HEART"},
    {"id": "report", "label": "Report", "ix": "03", "icon": "DESCRIPTION_OUTLINED"},
    {"id": "automation", "label": "Automation", "ix": "04", "icon": "CODE"},
]

# Provider smart-casing
PROVIDER_DISPLAY = {
    "openai": "OpenAI", "nvidia": "NVIDIA", "anthropic": "Anthropic",
    "gemini": "Gemini", "azure_openai": "Azure OpenAI", "ollama": "Ollama",
}
def disp_name(name):
    return PROVIDER_DISPLAY.get(name, name.replace("_", " ").title())
