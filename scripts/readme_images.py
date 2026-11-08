"""Draw the README's images into docs/assets/: the hero (light and dark) and the example answer.

Run: python scripts/readme_images.py docs/assets
GitHub strips CSS from READMEs, so the README's visual style lives in these SVGs. The
palette matches the landing page (site/index.html). Text that appears in an image also
appears in the README as text.
"""
# ruff: noqa: E501, E701, E702, E731, E741
import html
import sys

out = sys.argv[1]
F = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
M = "ui-monospace, SFMono-Regular, 'Cascadia Mono', Menlo, Consolas, monospace"
T = {
 "dark": dict(bg="#141210", surface="#1c1a17", line="#36322d", text="#f4f0e9", muted="#b9b2a7", accent="#ff7a45", accent_text="#ff8c5c", ink="#1a0f08", n1="#8fbcff", n2="#ffb27a", n3="#f5e17a", numink="#1a1410"),
 "light": dict(bg="#faf6f0", surface="#ffffff", line="#e4dccf", text="#1f1c18", muted="#57514a", accent="#f0652b", accent_text="#bf4612", ink="#ffffff", n1="#8fbcff", n2="#ffb27a", n3="#f5e17a", numink="#1a1410"),
}
def t(x, y, s, size, fill, weight=400, anchor="start", family=F):
    return f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">{html.escape(s)}</text>'
sprout = lambda c: f'<g fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21v-9"/><path d="M12 12c0-4 3-7 8-7 0 4-3 7-8 7z"/><path d="M12 14c0-3-2.5-5.5-7-5.5 0 3 2.5 5.5 7 5.5z"/></g>'

for name, c in T.items():
    hero = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="340" viewBox="0 0 1200 340">
  <rect width="1200" height="340" rx="24" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  <rect x="80" y="64" width="52" height="52" rx="15" fill="{c['accent']}"/>
  <g transform="translate(86,70) scale(1.67)">{sprout(c['ink'])}</g>
  {t(148, 106, "HomeGrown", 40, c['text'], 700)}
  <text x="80" y="196" font-family="{F}" font-size="58" font-weight="700" letter-spacing="-1.5"><tspan fill="{c['text']}">Has anyone here </tspan><tspan fill="{c['accent_text']}">already built it?</tspan></text>
  {t(82, 250, "Ask before you build. HomeGrown tells you whether another team made it, who looks", 22, c['muted'])}
  {t(82, 282, "after it, how ready it is and how to get the code, even code you can't open.", 22, c['muted'])}
</svg>
'''
    open(f"{out}/hero-{name}.svg", "w", encoding="utf-8", newline="\n").write(hero)


# The answer: real output of `homegrown ask` from the Grafana test run, trimmed.
c = T["dark"]; g = c['muted']; w = c['text']; P = "#5fd38a"; B = "#ffb27a"; O = c['accent_text']; Q = c['n1']
lines = [
  [(g, "$ ", 400), (w, "homegrown ask ", 700), (Q, '"has anyone built a Kafka load tester for k6?"', 400)],
  None,
  [(P, "Yes", 700), (w, " - this has been done here.", 400)],
  None,
  [(g, "Use as-is:", 400)],
  [(P, "* ", 400), (w, "xk6-kafka", 700), (B, "   beta ", 400), (g, "(inferred, 60% confidence, unconfirmed)", 400)],
  [(g, "  team ", 400), (w, "team-xk6-kafka", 400), (g, "  ·  LOB ", 400), (w, "Developer Tools & Testing", 400)],
  [(g, "  manager ", 400), (w, "Lead, team-xk6-kafka", 400)],
  [(g, "  why ", 400), (w, "an official k6 extension that provides Kafka load testing", 400)],
  [(g, "  - ", 400), (w, "Asserted by its own tests: Timing Metrics Are Untagged.", 400)],
  [(g, "  access ", 400), (O, "discoverable only: request access from Lead, team-xk6-kafka", 400)],
  [(g, "  stage basis ", 400), (w, "released, but still 0.x (v0.1.0)", 400)],
]
y, rows = 64, []
for ln in lines:
    if ln is None: y += 14; continue
    spans = "".join(f'<tspan fill="{f}" font-weight="{wt}">{html.escape(s)}</tspan>' for f, s, wt in ln)
    rows.append(f'<text x="40" y="{y}" font-family="{M}" font-size="19" xml:space="preserve">{spans}</text>')
    y += 32
ans = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{y+12}" viewBox="0 0 1200 {y+12}">
  <rect width="1200" height="{y+12}" rx="20" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  {"".join(rows)}
</svg>
'''
open(f"{out}/answer.svg", "w", encoding="utf-8", newline="\n").write(ans)

print("ok")
