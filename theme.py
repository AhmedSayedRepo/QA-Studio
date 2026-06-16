"""theme.py — design tokens ported from the Claude Design system (styles.css)."""

# ── Colors ────────────────────────────────────────────────────────────────────
BG        = "#FBFBFD"
CARD      = "#FFFFFF"
CARD_2    = "#F6F5FA"
BORDER    = "#E8E7EE"
BORDER_2  = "#EEEDF3"
INK       = "#1B1A22"
INK_2     = "#74727E"
INK_3     = "#A3A1AD"

RAIL      = "#17151E"
RAIL_2    = "#201D2A"
RAIL_LINE = "#2A2733"
RAIL_INK  = "#ECEAF2"
RAIL_DIM  = "#8B8896"

VIOLET      = "#6A4DFF"
VIOLET_H    = "#5A3DEE"
VIOLET_SOFT = "#ECE8FF"
VIOLET_INK  = "#5234E0"
STORY       = "#7C5CFF"

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