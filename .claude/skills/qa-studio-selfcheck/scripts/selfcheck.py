#!/usr/bin/env python3
"""QA Studio explorer self-check — validate locator binding against saved page
HTML, using the REAL engine.py decision functions, with no browser/Keycloak/Azure.

Usage:
    python selfcheck.py --engine ../engine.py \
        --fixture login-error.html --case login-error.case.json [--ai]

See the qa-studio-selfcheck SKILL.md for details and limitations.
"""
import argparse, importlib.util, json, os, sys, types
from html.parser import HTMLParser


# ── load engine.py, stubbing heavy third-party deps so the import succeeds ──
def load_engine(path):
    # Most heavy imports in engine.py (selenium, azure.*) are lazy (inside
    # functions); stub the top-level ones so we can call the pure functions.
    for name in ("requests", "openai", "anthropic", "google", "dotenv", "PIL",
                 "selenium", "flet", "azure", "numpy", "pandas"):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("qa_engine", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        sys.exit(f"Could not import engine.py at {path}: {e}\n"
                 f"(If it's a missing top-level import, add it to the stub list.)")
    return mod


# ── minimal HTML harvester mirroring _HARVEST_JS / _ERROR_HARVEST_JS fields ──
_INTERACTIVE_TAGS = {"input", "button", "a", "select", "textarea"}
_INTERACTIVE_ROLES = {"button", "link", "tab", "menuitem", "option", "checkbox", "switch"}
_ERR_CLASS_HINTS = ("error", "invalid", "feedback", "danger", "alert", "toast",
                    "help-block", "kc-feedback")
_ERR_ROLES = {"alert", "status"}


class _Harvester(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []        # open element nodes
        self.nodes = []        # all element nodes (closed)
        self.labels = {}       # id -> label text (from <label for=id>)
        self._label_stack = [] # track open <label> for nested association

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        node = {"tag": tag.lower(), "attrs": a, "text": "", "label_text": "", "children": []}
        if self.stack:
            self.stack[-1]["children"].append(node)
        self.stack.append(node)
        if tag.lower() == "label":
            self._label_stack.append(node)

    def handle_data(self, data):
        d = data.strip()
        if not d:
            return
        for n in self.stack:                 # accumulate descendant text
            n["text"] = (n["text"] + " " + d).strip()[:200]
        for lab in self._label_stack:
            lab["label_text"] = (lab["label_text"] + " " + d).strip()[:120]

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                node = self.stack.pop(i)
                if tag == "label":
                    fr = node["attrs"].get("for")
                    if fr:
                        self.labels[fr] = node["label_text"] or node["text"]
                    if self._label_stack and self._label_stack[-1] is node:
                        self._label_stack.pop()
                    # associate label text with a contained control lacking its own
                    self.nodes.append(node)
                else:
                    self.nodes.append(node)
                break


def _aname(a, node, labels):
    return (a.get("aria-label") or a.get("title") or a.get("alt")
            or labels.get(a.get("id", ""), "") or node.get("label_text", "")).strip()[:80]


def _icon_cls(node):
    """First descendant icon's class (mirrors the engine's child-icon harvest)."""
    for ch in node.get("children", []):
        c = ch["attrs"].get("class", "")
        if ch["tag"] in ("i", "svg") or "icon" in c.lower() or "pi-" in c or "fa-" in c:
            return c
        deep = _icon_cls(ch)
        if deep:
            return deep
    return ""


_SVG_ATTRS = ("data-svgicon", "data-svg-icon", "ng-reflect-svg-icon", "data-icon")


def _svgicon(node):
    """data-svgicon / ng-reflect-svg-icon on the element or a descendant."""
    for k in _SVG_ATTRS:
        if node["attrs"].get(k):
            return node["attrs"][k]
    for ch in node.get("children", []):
        v = _svgicon(ch)
        if v:
            return v
    return ""


def _to_el(idx, node, labels, is_error=False):
    a = node["attrs"]
    return {
        "idx": idx, "tag": node["tag"], "type": a.get("type", ""),
        "role": a.get("role", ""), "id": a.get("id", ""), "name": a.get("name", ""),
        "testid": a.get("data-testid") or a.get("data-test") or a.get("data-cy") or "",
        "text": (node["text"] or a.get("value", "")).strip()[:120],
        "placeholder": a.get("placeholder", ""), "aria": a.get("aria-label", ""),
        "aname": _aname(a, node, labels),
        "cls": (a.get("class", "") + " " + _icon_cls(node)).strip()[:120],
        "svgicon": _svgicon(node)[:40],
        "disabled": "disabled" in a,
        "visible": True,  # static HTML can't tell layout; assume visible
        "css": ("#" + a["id"]) if a.get("id") else node["tag"],
        "xpath": ("//*[@id='%s']" % a["id"]) if a.get("id") else "/" + node["tag"],
        "is_error": is_error,
    }


def harvest(html):
    h = _Harvester()
    h.feed(html)
    interactive, errors = [], []
    for node in h.nodes:
        a = node["attrs"]
        tag, role = node["tag"], a.get("role", "").lower()
        cls = a.get("class", "").lower()
        is_interactive = (tag in _INTERACTIVE_TAGS or role in _INTERACTIVE_ROLES
                          or a.get("contenteditable") == "true")
        is_err = (role in _ERR_ROLES or "aria-live" in a
                  or any(hint in cls for hint in _ERR_CLASS_HINTS)
                  or "error" in a.get("id", "").lower())
        if is_interactive:
            interactive.append(node)
        if is_err and node["text"].strip():
            errors.append(node)
    els = [_to_el(i, n, h.labels) for i, n in enumerate(interactive)]
    errs = [_to_el(len(els) + i, n, h.labels, is_error=True) for i, n in enumerate(errors)]
    return els, errs


def _describe(el):
    if el.get("id"):
        return "#" + el["id"]
    if el.get("name"):
        return "[name=%s]" % el["name"]
    t = (el.get("text") or el.get("aname") or el.get("aria") or el.get("placeholder") or "").strip()
    return ('"%s"' % t[:30]) if t else (el.get("tag") or "?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="engine.py")
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--case", default=None)
    ap.add_argument("--ai", action="store_true", help="use real compile_test_case")
    args = ap.parse_args()

    E = load_engine(args.engine)
    html = open(args.fixture, encoding="utf-8", errors="ignore").read()
    els, errs = harvest(html)
    pool = els + errs
    print("== fixture: %s ==" % os.path.basename(args.fixture))
    print("harvested %d interactive + %d error/message element(s)\n" % (len(els), len(errs)))

    if not args.case:
        for e in els:
            print("  [%2d] %-8s %s" % (e["idx"], e["tag"], _describe(e)))
        return

    tc = json.load(open(args.case, encoding="utf-8"))
    ctype = E._classify_case(tc)
    print("test case : %s" % tc.get("title", ""))
    print("class     : %s\n" % ctype)

    intents = []
    if args.ai:
        try:
            intents = E.compile_test_case(tc, None, lambda *a, **k: None) or []
        except Exception as ex:
            print("  (compile_test_case failed: %s — using raw-step fallback)" % str(ex)[:80])
    if not intents:
        intents = E._intents_from_raw_steps(tc)

    guesses = 0
    for n, it in enumerate(intents, 1):
        role = it.get("role")
        head = "%d. [%s] %s" % (n, role, (it.get("target") or "")[:50])
        if role == "precondition":
            print(head + "   -> (no UI action)")
            continue
        ranked = E._rank_candidates(it, pool)
        if not ranked:
            print(head + "   -> GUESS (no candidate)")
            guesses += 1
            continue
        top = ranked[0]
        confident = (len(ranked) == 1 or
                     (top[0] >= 2 and top[0] - (ranked[1][0] if len(ranked) > 1 else 0) >= 1))
        mark = "LIVE" if confident else "tie -> AI tiebreak"
        print(head + "   -> %s  %s (score %.1f)" % (mark, _describe(top[1]), top[0]))
        for sc, el in ranked[1:3]:
            print("        alt: %s (score %.1f)" % (_describe(el), sc))

    print("\nsummary: %d intent(s), %d guess(es)%s" %
          (len(intents), guesses, "  <-- investigate" if guesses else "  (clean)"))


if __name__ == "__main__":
    main()
