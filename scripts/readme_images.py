"""Draw the README's images into docs/assets/ (light and dark versions).

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

    cards = [
      (c['n1'], "1", ["It notices changes"], ["A push or the poller says", "which repositories moved.", "It rereads only the folders", "that changed."]),
      (c['n2'], "2", ["It writes down", "what each part does"], ["What it's for, what its", "tests show, who built it", "and how ready it is, each", "linked to its source file."]),
      (c['n3'], "3", ["It answers,", "or says no"], ["It checks each likely match", "against your question, shows", "only what you may see, and", "says when nothing fits."]),
    ]
    parts = []
    for i, (col, n, title, body) in enumerate(cards):
        x = 80 + i * 360
        parts.append(f'<rect x="{x}" y="70" width="320" height="340" rx="20" fill="{c["surface"]}" stroke="{c["line"]}" stroke-width="2"/>')
        parts.append(f'<rect x="{x+32}" y="102" width="48" height="48" rx="14" fill="{col}"/>')
        parts.append(t(x+56, 135, n, 24, c['numink'], 700, "middle"))
        y = 196
        for line in title:
            parts.append(t(x+32, y, line, 24, c['text'], 700)); y += 30
        y += 12
        for line in body:
            parts.append(t(x+32, y, line, 18, c['muted'])); y += 27
    how = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="470" viewBox="0 0 1200 470">
  <rect width="1200" height="470" rx="24" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  {t(80, 46, "How it works", 20, c['muted'], 700)}
  {"".join(parts)}
  {t(600, 446, "Two agents share one database. The database itself decides who sees what.", 18, c['muted'], 400, "middle")}
</svg>
'''
    open(f"{out}/how-it-works-{name}.svg", "w", encoding="utf-8", newline="\n").write(how)

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


def card(x, y, w, h, c, fill=None):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill or c["surface"]}" stroke="{c["line"]}" stroke-width="2"/>'


def badge(x, y, n, col, c, size=44):
    return (f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="13" fill="{col}"/>'
            + t(x + size / 2, y + size / 2 + 8, n, 22, c["numink"], 700, "middle"))


for name, c in T.items():
    cols = [c["n1"], c["n2"], c["n3"], c["accent"], "#ff86b8"]

    # Try it: five steps
    steps = [(["Install"], ["Docker, Python and Git"]),
             (["Collect an", "organization"], ["any GitHub org"]),
             (["Ask a", "question"], ["in plain English"]),
             (["Add a model"], ["optional; four kinds"]),
             (["Open the", "dashboard"], ["the org map"])]
    w, gap = 200, 12
    x0 = (1200 - (5 * w + 4 * gap)) / 2
    parts = []
    for i, (title, sub) in enumerate(steps):
        x = x0 + i * (w + gap)
        parts.append(card(x, 64, w, 200, c))
        parts.append(badge(x + 20, 84, str(i + 1), cols[i], c))
        y = 166
        for ln in title:
            parts.append(t(x + 20, y, ln, 20, c["text"], 700)); y += 25
        parts.append(t(x + 20, 246, sub[0], 15, c["muted"]))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="290" viewBox="0 0 1200 290">
  <rect width="1200" height="290" rx="24" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  {t(x0, 44, "Try it in five steps", 20, c['muted'], 700)}
  {"".join(parts)}
</svg>
'''
    open(f"{out}/try-it-{name}.svg", "w", encoding="utf-8", newline="\n").write(svg)

    # Coding agent: the conversation
    def bubble(x, y, lines, fill, ink, w):
        h = 24 + 28 * len(lines)
        out_ = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}"/>'
        for i, ln in enumerate(lines):
            out_ += t(x + 22, y + 36 + 28 * i, ln, 19, ink, 500)
        return out_, h
    b1, h1 = bubble(1200 - 60 - 560, 40, ["Add Kafka produce and consume steps", "to our k6 tests."], c["text"], c["bg"], 560)
    ty = 40 + h1 + 20
    lock = c["accent_text"]
    tool = (f'<rect x="60" y="{ty}" width="1080" height="150" rx="16" fill="none" stroke="{c["line"]}" stroke-width="2" stroke-dasharray="7 6"/>'
            + t(84, ty + 34, "Agent calls find_existing", 16, c["muted"], 700)
            + t(84, ty + 64, "“Produce and consume Kafka messages from k6 load tests”", 18, c["text"])
            + f'<text x="84" y="{ty + 96}" font-family="{F}" font-size="18"><tspan fill="{c["text"]}" font-weight="700">xk6-kafka</tspan><tspan fill="{c["n2"] if name == "dark" else "#b5530c"}" font-weight="700">   beta</tspan><tspan fill="{c["muted"]}">   team-xk6-kafka · use as-is</tspan></text>'
            + f'<text x="84" y="{ty + 128}" font-family="{F}" font-size="18"><tspan fill="{lock}" font-weight="700">can_read_code: false</tspan><tspan fill="{c["muted"]}">   ask Lead, team-xk6-kafka for access</tspan></text>')
    b2, h2 = bubble(60, ty + 150 + 20, ["team-xk6-kafka already built this: xk6-kafka, in beta.", "Its code is private to you, so I won't write a new Kafka", "client yet. Shall I draft an access request to their lead?"], c["surface"], c["text"], 640)
    H = ty + 150 + 20 + h2 + 40
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{H}" viewBox="0 0 1200 {H}">
  <rect width="1200" height="{H}" rx="24" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  {b1}{tool}{b2}
</svg>
'''
    open(f"{out}/agent-{name}.svg", "w", encoding="utf-8", newline="\n").write(svg)

    # Results: four stat cards
    stats = [("94%", ["Right answer in the top 5,", "out of 109 components"]),
             ("26 of 26", ["Says so when nothing", "fits"]),
             ("0.1 s", ["Search across 300,000", "vectors"]),
             ("225 s", ["From a push to processed,", "95% of the time"])]
    w, gap = 252, 16
    x0 = (1200 - (4 * w + 3 * gap)) / 2
    parts = []
    for i, (big, label) in enumerate(stats):
        x = x0 + i * (w + gap)
        parts.append(card(x, 40, w, 170, c))
        parts.append(f'<circle cx="{x + 30}" cy="74" r="6" fill="{cols[i]}"/>')
        parts.append(t(x + 46, 80, label[0], 16, c["muted"], 500))
        parts.append(t(x + 46, 102, label[1], 16, c["muted"], 500))
        parts.append(t(x + 24, 178, big, 50, c["text"], 700))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="250" viewBox="0 0 1200 250">
  <rect width="1200" height="250" rx="24" fill="{c['bg']}" stroke="{c['line']}" stroke-width="2"/>
  {"".join(parts)}
</svg>
'''
    open(f"{out}/results-{name}.svg", "w", encoding="utf-8", newline="\n").write(svg)
print("ok")
