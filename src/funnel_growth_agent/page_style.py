"""One look for every HTML page this package writes (tile sheet, proposal page, demo console):
the growth-loop dark palette. Lime marks exactly one thing per page: the winner or the change."""

from __future__ import annotations

import html
import os
from pathlib import Path

PALETTE = {
    "bg": "#0A0A0A",
    "fg": "#F5F0E8",
    "card": "#161616",
    "line": "#2A2A2A",
    "muted": "#9A9488",
    "lime": "#C6F135",
    "soft": "#C9C4B8",
    "well": "#0F0F0F",
}

CSS = """
:root{--bg:#0A0A0A;--fg:#F5F0E8;--card:#161616;--line:#2A2A2A;--muted:#9A9488;--lime:#C6F135;--soft:#C9C4B8;--well:#0F0F0F}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Inter,sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
h3{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 10px;font-weight:600}
a{color:var(--fg)}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}
pre{white-space:pre-wrap;color:var(--soft);background:var(--well);padding:10px;border-radius:8px;font-size:12px}
.row{display:flex;gap:12px;flex-wrap:wrap}.cand{width:300px}
.cand img,.ref img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px;border:2px solid var(--line);display:block;background:#222}
.cand.chosen img{border-color:var(--lime)}
.badge{display:inline-block;background:var(--lime);color:var(--bg);font-weight:600;padding:2px 8px;border-radius:999px;font-size:12px}
.warn{display:inline-block;background:var(--fg);color:var(--bg);padding:2px 8px;border-radius:999px;font-size:12px}
.pill{display:inline-block;border:1px solid var(--line);color:var(--muted);padding:2px 8px;border-radius:999px;font-size:12px}
table{border-collapse:collapse;margin-top:6px}td{padding:1px 8px 1px 0;font-size:12px}.ref{width:160px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.kpi{font-size:44px;font-weight:700;letter-spacing:-.02em;line-height:1;font-variant-numeric:tabular-nums}
.kpi small{font-size:14px;font-weight:400;color:var(--muted);margin-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.field{display:grid;grid-template-columns:110px 1fr;gap:6px 12px;padding:6px 0;border-top:1px solid var(--line)}
.field:first-of-type{border-top:0}
.field .k{color:var(--muted);font-size:12px;padding-top:2px}
.before{color:var(--muted);text-decoration:line-through;text-decoration-color:#5A564F}
.after{color:var(--fg)}.after.lit{color:var(--lime)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{border:1px solid var(--line);border-radius:8px;padding:3px 8px;font-size:12px}
.chip.omit{text-decoration:line-through;color:var(--muted)}
.chip.moved{border-color:var(--lime)}
.swatch{display:inline-block;width:14px;height:14px;border-radius:4px;vertical-align:-2px;margin-right:4px;border:1px solid var(--line)}
.thumb{width:120px;aspect-ratio:1;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#222}
.poster{width:100%;max-width:640px;aspect-ratio:16/9;object-fit:cover;border-radius:10px;border:1px solid var(--line);display:block;background:#222}
.creative{display:flex;gap:12px;align-items:flex-start}
.creative .body{flex:1;min-width:0}
details summary{cursor:pointer;color:var(--muted)}
ol,ul{margin:6px 0;padding-left:20px}li{margin:4px 0}
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def rel_link(path: Path, base_dir: Path) -> str:
    """Relative link when the file lives near the page (../tiles/...), file:// otherwise."""
    rel = os.path.relpath(path.resolve(), base_dir.resolve())
    if rel.startswith("../.."):
        return path.resolve().as_uri()
    return rel.replace(os.sep, "/")


def swatch(colour: str) -> str:
    """A colour chip; named colours pass through to CSS, unknown names show as text only."""
    text = esc(colour)
    return f"<span class='swatch' style='background:{text}'></span>{text}"


def document(title: str, body: str, *, extra_css: str = "") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}{extra_css}</style></head>"
        f"<body>{body}</body></html>"
    )
