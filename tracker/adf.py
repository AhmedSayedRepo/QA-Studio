"""tracker/adf.py — Atlassian Document Format → HTML / plain text.

WHY THIS IS ITS OWN FILE, AND WHY IT'S THE RISKIEST PART OF THE JIRA PORT
Azure DevOps stores descriptions and acceptance criteria as HTML strings. Jira
Cloud stores them as ADF: a nested JSON document model. Every AI prompt in this
app consumes that text — `generate_titles(story_title, criteria, …)`,
`evaluate_existing_steps`, `describe_story_ui`. If the renderer silently drops
a bullet list or flattens a table, nothing throws: generation quality just
quietly degrades, and it degrades in a way that looks like "the AI got worse",
which is close to undiagnosable from a bug report.

So this is deliberately standalone, dependency-free, and heavily tested rather
than being three helper lines inside the Jira adapter.

SECURITY: THIS IS AN UNTRUSTED-INPUT BOUNDARY
The output flows into generated HTML — sprint-closure emails and PDF reports
that get sent to stakeholders. Jira issue content is attacker-influenced in any
org where a customer, contractor, or external reporter can file a ticket. So:

  * every text node is HTML-escaped (no raw passthrough, ever);
  * link/image URLs are scheme-checked — `javascript:`, `data:`, `vbscript:`
    are dropped, which kills the standard stored-XSS vector;
  * we only ever EMIT a known-safe tag set. There is no sanitizer pass trying
    to filter dangerous markup out of arbitrary HTML, because allowlisting what
    we generate is sound where blocklisting is not.
"""
from __future__ import annotations

import html as _html
import json
from typing import Any, Dict, Iterable, List, Optional

#: URL schemes permitted in links/images. Anything else is dropped entirely.
_SAFE_SCHEMES = ("http://", "https://", "mailto://", "mailto:", "/")

#: Marks we translate, and the tag they become. Anything unknown is ignored
#: (text still renders) rather than passed through.
_MARK_TAGS = {
    "strong": "strong", "em": "em", "code": "code",
    "strike": "s", "underline": "u", "subsup": None, "textColor": None,
}


def _safe_href(url):
    """Return the URL if it uses a safe scheme, else "".

    Checked case-insensitively and after stripping whitespace/control chars,
    because `java\\tscript:alert(1)` and ` JavaScript:` are both real bypasses
    of a naive `startswith("javascript:")` test.
    """
    if not url:
        return ""
    cleaned = "".join(ch for ch in str(url) if ord(ch) > 32).strip()
    low = cleaned.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:", "about:")):
        return ""
    if low.startswith(_SAFE_SCHEMES) or low.startswith("#"):
        return cleaned
    # Relative paths without a scheme are fine; anything with an unknown
    # scheme (`foo:bar`) is not.
    if ":" in low.split("/")[0]:
        return ""
    return cleaned


def _esc(text):
    return _html.escape(str(text or ""), quote=True)


def _wrap_marks(text, marks):
    """Apply ADF marks to already-escaped text."""
    out = text
    for mark in marks or []:
        if not isinstance(mark, dict):
            continue
        mtype = mark.get("type")
        if mtype == "link":
            href = _safe_href((mark.get("attrs") or {}).get("href"))
            if href:
                # rel=noopener: these render in emails/PDFs and may open in a
                # browser; never hand the opener window to a third-party link.
                out = (f'<a href="{_esc(href)}" rel="noopener noreferrer">{out}</a>')
            continue
        tag = _MARK_TAGS.get(mtype)
        if tag:
            out = f"<{tag}>{out}</{tag}>"
    return out


def _is_adf(value):
    return isinstance(value, dict) and (
        value.get("type") == "doc" or "content" in value)


def _node_text(node):
    """Plain text of a node subtree — used for the text renderer and for
    table cells where nested block markup would be noise."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_node_text(n) for n in node)
    if not isinstance(node, dict):
        return str(node)
    ntype = node.get("type")
    if ntype == "text":
        return str(node.get("text") or "")
    if ntype == "hardBreak":
        return "\n"
    if ntype == "mention":
        return str((node.get("attrs") or {}).get("text") or "")
    if ntype == "emoji":
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if ntype == "inlineCard":
        return str((node.get("attrs") or {}).get("url") or "")
    inner = "".join(_node_text(c) for c in (node.get("content") or []))
    if ntype in ("paragraph", "heading", "listItem", "blockquote",
                 "codeBlock", "tableRow", "panel"):
        return inner + "\n"
    return inner


def _render_nodes(nodes):
    return "".join(_render(n) for n in (nodes or []))


def _render(node):
    """Render one ADF node to safe HTML."""
    if node is None:
        return ""
    if isinstance(node, str):
        return _esc(node)
    if isinstance(node, list):
        return _render_nodes(node)
    if not isinstance(node, dict):
        return _esc(node)

    ntype = node.get("type")
    content = node.get("content") or []
    attrs = node.get("attrs") or {}

    if ntype == "text":
        return _wrap_marks(_esc(node.get("text")), node.get("marks"))

    if ntype == "hardBreak":
        return "<br>"

    if ntype == "paragraph":
        inner = _render_nodes(content)
        return f"<p>{inner}</p>" if inner.strip() else ""

    if ntype == "heading":
        level = attrs.get("level")
        try:
            level = max(1, min(6, int(level)))
        except (TypeError, ValueError):
            level = 3
        return f"<h{level}>{_render_nodes(content)}</h{level}>"

    if ntype == "bulletList":
        return f"<ul>{_render_nodes(content)}</ul>"

    if ntype == "orderedList":
        return f"<ol>{_render_nodes(content)}</ol>"

    if ntype == "listItem":
        inner = _render_nodes(content)
        # Unwrap a lone <p> so list items read tightly in email clients.
        if inner.startswith("<p>") and inner.endswith("</p>") and inner.count("<p>") == 1:
            inner = inner[3:-4]
        return f"<li>{inner}</li>"

    if ntype == "codeBlock":
        return f"<pre><code>{_esc(_node_text(content).rstrip())}</code></pre>"

    if ntype == "blockquote":
        return f"<blockquote>{_render_nodes(content)}</blockquote>"

    if ntype == "rule":
        return "<hr>"

    if ntype == "panel":
        return f"<div class=\"adf-panel\">{_render_nodes(content)}</div>"

    if ntype == "table":
        return f"<table>{_render_nodes(content)}</table>"

    if ntype == "tableRow":
        return f"<tr>{_render_nodes(content)}</tr>"

    if ntype in ("tableCell", "tableHeader"):
        tag = "th" if ntype == "tableHeader" else "td"
        return f"<{tag}>{_render_nodes(content)}</{tag}>"

    if ntype == "mention":
        return _esc((attrs.get("text") or "").lstrip("@") or attrs.get("id") or "")

    if ntype == "emoji":
        return _esc(attrs.get("text") or attrs.get("shortName") or "")

    if ntype == "inlineCard":
        href = _safe_href(attrs.get("url"))
        return (f'<a href="{_esc(href)}" rel="noopener noreferrer">{_esc(href)}</a>'
                if href else "")

    if ntype in ("mediaSingle", "mediaGroup", "media"):
        # Media is referenced by opaque id and needs an authenticated fetch, so
        # there is nothing safe or useful to inline. Screenshots reach the
        # vision pass through fetch_story_screenshots() instead.
        alt = _esc(attrs.get("alt") or "attachment")
        return f'<p class="adf-media">[{alt}]</p>' if ntype == "mediaSingle" else ""

    if ntype in ("doc", "expand", "nestedExpand", "layoutSection", "layoutColumn"):
        return _render_nodes(content)

    # Unknown node: render children rather than dropping the subtree. Losing
    # acceptance-criteria text to an ADF version bump would silently degrade
    # generation, which is the exact failure this module exists to prevent.
    return _render_nodes(content)


def to_html(value):
    """ADF (dict or JSON string) → safe HTML. Passes plain strings through
    escaped, so a caller can hand it either representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                value = json.loads(stripped)
            except ValueError:
                return value            # already HTML/plain text
        else:
            return value
    if not isinstance(value, dict):
        return _esc(value)
    return _render(value).strip()


def to_text(value):
    """ADF → plain text. Used where a prompt wants prose without markup."""
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                value = json.loads(stripped)
            except ValueError:
                return value
        else:
            return value
    if not isinstance(value, dict):
        return str(value)
    lines = [ln.rstrip() for ln in _node_text(value).splitlines()]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


def is_adf(value):
    """True if this looks like an ADF document rather than HTML/text."""
    if isinstance(value, dict):
        return _is_adf(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            return _is_adf(json.loads(value))
        except ValueError:
            return False
    return False


__all__ = ["to_html", "to_text", "is_adf"]
