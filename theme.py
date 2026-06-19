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

# Dark sidebar — deep indigo-navy (echoes the blue end of the logo gradient)
RAIL      = "#121529"
RAIL_2    = "#1B1F3A"
RAIL_LINE = "#272C49"
RAIL_INK  = "#EAECF7"
RAIL_DIM  = "#878BA8"

# Brand — indigo primary (logo gradient midpoint) + its tints
VIOLET      = "#3A57D6"   # primary (buttons, nav active, accents)
VIOLET_H    = "#2C44BE"   # hover / pressed
VIOLET_SOFT = "#E7ECFF"   # soft fill behind icons / badges
VIOLET_INK  = "#2940C2"   # text on soft fills
STORY       = "#6A52F0"   # story / log accent (leans to the purple end)

# Logo gradient endpoints — use for brand fills (tile fallbacks, hero accents)
BRAND_GRAD_1 = "#1C80E0"  # azure
BRAND_GRAD_2 = "#6A33A8"  # royal purple

GREEN       = "#1F9D57"; GREEN_SOFT = "#E5F6EC"
RED         = "#E0474D"; RED_SOFT   = "#FCEBEC"
AMBER       = "#C2860C"; AMBER_SOFT = "#FAF1DD"

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
