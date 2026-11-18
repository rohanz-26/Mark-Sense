"""
MarkSense prototype: photo of an answer sheet -> read -> mark -> analyse -> email parents.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload --host 0.0.0.0 --port 8000
Then open http://localhost:8000
"""
import csv
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import quote

import cv2
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import layout as L
import settings as S
from omr_reader import SheetError, read_sheet
from report import SECTION_TIPS, TIPS, EmailSetupError, build_pdf, send_report

BASE = Path(__file__).parent
KEYS = BASE / "answer_keys"
DATA = BASE / "data" / "results"
DATA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="MarkSense")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

e = html.escape
SECS = [("RC", "Reading Comprehension", "Reading"),
        ("QR", "Quantitative Reasoning", "Maths"),
        ("AR", "Abstract Reasoning", "Abstract")]
SECTION_NAMES = {c: n for c, n, _ in SECS}
SHORT = {c: s for c, _, s in SECS}
COL = {"good": "#22A06B", "mid": "#F2A93B", "low": "#E5534B"}

# ======================================================================
# data
# ======================================================================

def load_key(paper):
    return json.loads((KEYS / f"{paper}.json").read_text())


def papers():
    return [k["paper"] for k in tests()]


def tests():
    out = []
    for p in KEYS.glob("*.json"):
        k = json.loads(p.read_text())
        k.setdefault("paper", p.stem)
        k.setdefault("title", p.stem.replace("_", " ").title())
        k.setdefault("date", "2026-01-01")
        out.append(k)
    return sorted(out, key=lambda k: (k["date"], k["paper"]))


def results(paper):
    d = DATA / paper
    if not d.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def students():
    path = BASE / "students.csv"
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["student_id"]: r for r in csv.DictReader(f)}


def score(answers, key):
    """Mark answers against the key. Returns per-section results and skills lost."""
    out = {}
    for sec, sdata in key["sections"].items():
        qs = sdata["questions"]
        correct, lost, detail = 0, {}, {}
        for qn, q in qs.items():
            given = answers.get(sec, {}).get(qn)
            ok = given == q["answer"]
            correct += ok
            if not ok:
                lost[q["skill"]] = lost.get(q["skill"], 0) + 1
            detail[qn] = {"given": given, "correct": q["answer"], "ok": ok, "skill": q["skill"]}
        out[sec] = {"correct": correct, "total": len(qs),
                    "score_100": round(100 * correct / len(qs), 2),
                    "skills_lost": sorted(lost.items(), key=lambda kv: -kv[1]),
                    "questions": detail}
    return out


def total(rec):
    return sum(m["score_100"] for m in rec["marks"].values())


def record_path(paper, sid):
    d = DATA / paper
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sid}.json", d / f"{sid}.jpg"


def ranks(recs):
    order = sorted(recs, key=lambda r: -total(r))
    return {r["student_id"]: i + 1 for i, r in enumerate(order)}


def status_of(rec):
    if rec is None:
        return "absent"
    if rec["status"] != "ok":
        return "review"
    return "sent" if rec.get("emailed") else "ready"


def current_test(request):
    ts = tests()
    ids = [t["paper"] for t in ts]
    c = request.cookies.get("test")
    if c in ids:
        return next(t for t in ts if t["paper"] == c)
    with_results = [t for t in ts if results(t["paper"])]
    return (with_results or ts or [{"paper": "", "title": "No tests", "date": "2026-01-01", "sections": {}}])[-1]


def previous_test(paper):
    ts = [t for t in tests() if results(t["paper"])]
    ids = [t["paper"] for t in ts]
    if paper in ids and ids.index(paper) > 0:
        return ts[ids.index(paper) - 1]
    return None


def skill_stats(recs):
    agg = {}
    for r in recs:
        for sec, m in r["marks"].items():
            for q in m["questions"].values():
                a = agg.setdefault(q["skill"], {"skill": q["skill"], "sec": sec, "right": 0, "n": 0})
                a["n"] += 1
                a["right"] += q["ok"]
    for a in agg.values():
        a["pct"] = 100 * a["right"] / a["n"]
    return sorted(agg.values(), key=lambda a: a["pct"])


# ======================================================================
# small formatting helpers
# ======================================================================

def fmt(x, d=1):
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.{d}f}"


def level(pct):
    return "good" if pct >= 70 else "mid" if pct >= 45 else "low"


def ddate(s, long=False):
    d = dt.date.fromisoformat(s[:10])
    return d.strftime("%a, %-d %b %Y" if long else "%-d %b %Y")


AV_COLS = [("#EEF0FF", "#4338CA"), ("#E3F5EC", "#17744B"), ("#FDF1DC", "#A8640F"), ("#FCE6E4", "#B3342D"),
           ("#E6F4FB", "#1F6F98"), ("#F3E8FF", "#7C3AED"), ("#FFE9F3", "#BE185D"), ("#E8F7F5", "#0F766E")]


def avatar(name, sid="", size=""):
    initials = "".join(w[0] for w in (name or "?").split()[:2]).upper()
    bg, fg = AV_COLS[int(hashlib.md5((name or sid).encode()).hexdigest(), 16) % len(AV_COLS)]
    st = f"background:{bg};color:{fg}" + (f";width:{size}px;height:{size}px" if size else "")
    return f'<div class="av" style="{st}">{e(initials)}</div>'


def who(name, sub):
    return f'<div class="who">{avatar(name)}<div><b>{e(name)}</b><span>{e(sub)}</span></div></div>'


def minibar(pct, label=None):
    c = COL[level(pct)]
    label = fmt(pct) if label is None else label
    return (f'<div class="minibar"><div class="trk"><div class="fil" style="width:{max(2, pct):.1f}%;'
            f'background:{c}"></div></div>{f"<b>{label}</b>" if label != "" else ""}</div>')


STATUS_CHIP = {"sent": '<span class="chip good">Report sent</span>',
               "ready": '<span class="chip brand">Ready to send</span>',
               "review": '<span class="chip mid">Needs review</span>',
               "absent": '<span class="chip grey">Not sat</span>'}

ICONS = {
    "dash": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "scan": '<path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/><path d="M7 12h10"/>',
    "users": '<circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6"/><path d="M16 4a4 4 0 0 1 0 8"/><path d="M22 21c0-3-2-5-4-6"/>',
    "tests": '<rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 4V2h6v2"/><path d="M9 11h6M9 15h4"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "set": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    "up": '<path d="m3 17 6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    "down": '<path d="m3 7 6 6 4-4 8 8"/><path d="M14 17h7v-7"/>',
    "alert": '<path d="M12 3 2 21h20z"/><path d="M12 10v4M12 17.5v.01"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "trophy": '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3a3 3 0 0 1-3 4M7 5H4a3 3 0 0 0 3 4"/>',
    "upload": '<path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/>',
    "file": '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>',
    "send": '<path d="m22 2-11 11"/><path d="M22 2 15 22l-4-9-9-4z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M7 15v3M12 10v8M17 6v12"/>',
    "camera": '<path d="M3 7h3l2-3h8l2 3h3v13H3z"/><circle cx="12" cy="13" r="4"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


def ic(name):
    return f'<svg viewBox="0 0 24 24">{ICONS[name]}</svg>'


# ======================================================================
# SVG charts (drawn on the server: no internet or JS needed)
# ======================================================================

def histogram(values, maxv=300, bins=10, w=560, h=210):
    step = maxv / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int(v // step))] += 1
    top = max(counts + [1])
    pl, pb, pt = 8, 26, 18
    bw = (w - 2 * pl) / bins
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block">']
    for i in range(4):
        y = pt + (h - pt - pb) * i / 3
        out.append(f'<line x1="{pl}" x2="{w - pl}" y1="{y:.1f}" y2="{y:.1f}" stroke="#EEF0F5"/>')
    for i, c in enumerate(counts):
        bh = (h - pt - pb) * c / top
        x = pl + i * bw + 5
        col = COL[level(100 * (i + 0.5) / bins)]
        if c:
            out.append(f'<rect x="{x:.1f}" y="{h - pb - bh:.1f}" width="{bw - 10:.1f}" height="{bh:.1f}" rx="6" fill="{col}" opacity=".9"/>')
            out.append(f'<text x="{x + (bw - 10) / 2:.1f}" y="{h - pb - bh - 5:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#3B4050">{c}</text>')
        out.append(f'<text x="{x + (bw - 10) / 2:.1f}" y="{h - 8}" text-anchor="middle" font-size="10" fill="#9AA0AE">{int(i * step)}{"" if w < 500 else "–" + str(int((i + 1) * step))}</text>')
    out.append("</svg>")
    return "".join(out)


def line_chart(xlabels, series, w=640, h=230, ymin=None, ymax=None, suffix=""):
    vals = [v for _, _, s, _ in series for v in s if v is not None]
    if not vals:
        return '<div class="empty">No data yet</div>'
    lo = ymin if ymin is not None else max(0, min(vals) - 25)
    hi = ymax if ymax is not None else min(300, max(vals) + 25)
    if hi - lo < 10:
        hi = lo + 10
    pl, pr, pt, pb = 40, 24, 16, 30
    n = len(xlabels)
    X = lambda i: pl + (w - pl - pr) * (i / max(1, n - 1) if n > 1 else 0.5)
    Y = lambda v: pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="display:block;overflow:visible">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#EEF0F5"/>'
                   f'<text x="{pl - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" font-size="10" fill="#9AA0AE">{v:.0f}</text>')
    for i, lab in enumerate(xlabels):
        out.append(f'<text x="{X(i):.1f}" y="{h - 8}" text-anchor="middle" font-size="11" fill="#6B7280">{e(lab)}</text>')
    for name, color, s, dashed in series:
        pts = [(X(i), Y(v)) for i, v in enumerate(s) if v is not None]
        if not pts:
            continue
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if not dashed and len(pts) > 1:
            area = f"{pts[0][0]:.1f},{h - pb} " + d + f" {pts[-1][0]:.1f},{h - pb}"
            gid = "g" + hashlib.md5(name.encode()).hexdigest()[:6]
            out.append(f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".18"/>'
                       f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs><polygon points="{area}" fill="url(#{gid})"/>')
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{2 if dashed else 3}" '
                   f'stroke-linejoin="round" stroke-linecap="round" {DASH if dashed else ""}/>')
        for (x, y), v in zip(pts, [v for v in s if v is not None]):
            if dashed:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#fff" stroke="{color}" stroke-width="2"/>')
            else:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#fff" stroke="{color}" stroke-width="3"/>'
                           f'<text x="{x:.1f}" y="{y - 12:.1f}" text-anchor="middle" font-size="11.5" font-weight="700" fill="{color}">{fmt(v)}{suffix}</text>')
    out.append("</svg>")
    leg = "".join(f'<span><i style="background:{c}{";opacity:.6" if dsh else ""}"></i>{e(nm)}</span>' for nm, c, _, dsh in series)
    return "".join(out) + f'<div class="legend" style="margin-top:6px">{leg}</div>'


DASH = 'stroke-dasharray="5 5"'


def sparkline(vals, w=96, h=30):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return '<span class="tag">1 test</span>' if v else ""
    lo, hi = min(v), max(v)
    hi = hi if hi > lo else lo + 1
    pts = [(4 + (w - 8) * i / (len(v) - 1), 4 + (h - 8) * (1 - (x - lo) / (hi - lo))) for i, x in enumerate(v)]
    col = "#22A06B" if v[-1] >= v[0] else "#E5534B"
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    lx, ly = pts[-1]
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline points="{d}" fill="none" stroke="{col}" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{col}"/></svg>')


def donut(pct, size=76, stroke=9, label=None, color=None):
    import math
    r = (size - stroke) / 2
    c = 2 * math.pi * r
    col = color or COL[level(pct)]
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="flex:none">'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke="#EEF0F5" stroke-width="{stroke}"/>'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none" stroke="{col}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-dasharray="{c * max(0, min(pct, 100)) / 100:.1f} {c:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/>'
            f'<text x="50%" y="50%" dy=".35em" text-anchor="middle" font-size="{size * 0.24:.0f}" font-weight="750" fill="#1A1D2B">'
            f'{label if label is not None else fmt(pct)}</text></svg>')


def delta(cur, prev, unit=""):
    if prev is None:
        return ""
    dlt = cur - prev
    if abs(dlt) < 0.05:
        return '<span class="delta up" style="background:#EEF0F4;color:#6B7280">0</span>'
    cls = "up" if dlt > 0 else "down"
    return f'<span class="delta {cls}">{"▲" if dlt > 0 else "▼"} {fmt(abs(dlt))}{unit}</span>'


# ======================================================================
# page shell
# ======================================================================

TABS = [("dash", "Dashboard", "/"), ("scan", "Scan sheets", "/scan"), ("users", "Students", "/students"),
        ("tests", "Tests", "/tests"), ("mail", "Reports", "/reports"), ("set", "Settings", "/settings")]


def shell(request, active, title, body, picker=True, head="", sub="", crumbs="", toast=""):
    cur = current_test(request)
    pending = sum(1 for r in results(cur["paper"]) if r["status"] != "ok") if cur["paper"] else 0
    tabs = ""
    for icon, label, href in TABS:
        badge = f'<span class="count">{pending}</span>' if label == "Scan sheets" and pending else ""
        tabs += f'<a class="tab {"on" if label == active else ""}" href="{href}">{ic(icon)}<span class="tl">{label}</span>{badge}</a>'
    logo = ('<img class="logo-img" src="/logo">' if S.LOGO_FILE and (BASE / S.LOGO_FILE).exists()
            else '<div class="logo-ph">LOGO</div>')
    pick = ""
    if picker:
        opts = "".join(f'<option value="{e(t["paper"])}" {"selected" if t["paper"] == cur["paper"] else ""}>'
                       f'{e(t["title"])} · {ddate(t["date"])}</option>' for t in reversed(tests()))
        pick = (f'<div class="testpick"><span>Test</span><select onchange="location=\'/select?paper=\'+this.value+'
                f'\'&next=\'+encodeURIComponent(location.pathname+location.search)">{opts}</select></div>')
    toast_html = f'<div class="toast" id="toast">{e(toast)}</div><script>setTimeout(()=>document.getElementById("toast").remove(),3500)</script>' if toast else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)} · {e(S.CENTRE_NAME)}</title>
<link rel="stylesheet" href="/static/app.css"></head><body>
<header class="top"><div class="top-in">
 <a class="brandbox" href="/">{logo}<div><div class="cname">{e(S.CENTRE_NAME)}</div><div class="ctag">Assessment &amp; Reports</div></div></a>
 <nav class="tabs">{tabs}</nav>
 <div class="top-right">
  <form class="search" action="/students">{ic("search")}<input name="q" placeholder="Search students…" autocomplete="off"></form>
  <a class="bell" href="/scan">{ic("bell")}{"<i></i>" if pending else ""}</a>
  <div class="avatar" title="Teacher">T</div>
 </div></div></header>
<main class="wrap">
 <div class="pagehead"><div>{crumbs}<h1>{head or e(title)}</h1>{f"<p>{sub}</p>" if sub else ""}</div>{pick}</div>
 {body}
</main>
<div class="overlay-busy" id="busy"><div class="ring"></div><b id="busy-t">Working…</b><span id="busy-s"></span></div>
<script>
function busy(t,s){{document.getElementById('busy-t').innerText=t;document.getElementById('busy-s').innerText=s||'';document.getElementById('busy').classList.add('on')}}
document.querySelectorAll('tr[data-href]').forEach(r=>r.addEventListener('click',()=>location=r.dataset.href));
</script>{toast_html}</body></html>""")


@app.get("/logo")
def logo_file():
    return FileResponse(BASE / S.LOGO_FILE)


@app.get("/select")
def select(paper: str, next: str = "/"):
    r = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
    r.set_cookie("test", paper, max_age=60 * 60 * 24 * 365)
    return r


# ======================================================================
# Dashboard
# ======================================================================

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    t = current_test(request)
    recs = results(t["paper"])
    roster = students()
    hour = dt.datetime.now().hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    if not recs:
        body = f"""<div class="card hero"><h2>{greet} 👋</h2><p>No sheets have been scanned for {e(t['title'])} yet.
Photograph the answer sheets and upload them to get started.</p>
<div class="hero-actions"><a class="btn btn-white" href="/scan">{ic('camera')} Scan answer sheets</a></div></div>"""
        return shell(request, "Dashboard", "Dashboard", body, sub=f"{e(t['title'])} · {ddate(t['date'], True)}")

    totals = [total(r) for r in recs]
    avg = sum(totals) / len(totals)
    prev = previous_test(t["paper"])
    prev_recs = results(prev["paper"]) if prev else []
    prev_avg = sum(total(r) for r in prev_recs) / len(prev_recs) if prev_recs else None
    review = [r for r in recs if r["status"] != "ok"]
    sent = [r for r in recs if r.get("emailed")]
    ready = [r for r in recs if r["status"] == "ok" and not r.get("emailed")]
    best = max(recs, key=total)

    hero = f"""<div class="card hero"><h2>{greet} 👋</h2>
<p><b>{e(t['title'])}</b> has {len(recs)} of {len(roster)} sheets marked.
{f"{len(review)} need a quick check, " if review else ""}{len(ready)} report{"s" if len(ready) != 1 else ""} ready to send to parents.</p>
<div class="hero-actions"><a class="btn btn-white" href="/scan">{ic('camera')} Scan more sheets</a>
<a class="btn btn-ghost" href="/reports">{ic('send')} Email reports</a>
<a class="btn btn-ghost" href="/tests/{e(t['paper'])}">{ic('chart')} Test analysis</a></div></div>"""

    kpis = f"""<div class="grid g5 mt">
<div class="card kpi"><div class="lbl"><div class="ico i-brand">{ic('users')}</div>Students sat</div>
 <div class="val">{len(recs)}<small> / {len(roster)}</small></div><div class="foot">{len(roster) - len(recs)} not scanned yet</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-good">{ic('chart')}</div>Average score</div>
 <div class="val">{fmt(avg)}<small> / 300</small></div><div class="foot">{delta(avg, prev_avg)} {"vs " + e(prev['title']) if prev else ""}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-mid">{ic('trophy')}</div>Top score</div>
 <div class="val">{fmt(total(best))}<small> / 300</small></div><div class="foot">{e(best.get('student_name') or best['student_id'])}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-low">{ic('alert')}</div>Needs review</div>
 <div class="val">{len(review)}</div><div class="foot">{"Unclear marks to confirm" if review else "All sheets clear"}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-brand">{ic('mail')}</div>Reports sent</div>
 <div class="val">{len(sent)}<small> / {len(recs)}</small></div><div class="foot">{len(ready)} ready to send</div></div></div>"""

    # section averages
    sec_html = ""
    for code, name, short in SECS:
        a = sum(r["marks"][code]["score_100"] for r in recs) / len(recs)
        pa = (sum(r["marks"][code]["score_100"] for r in prev_recs) / len(prev_recs)) if prev_recs else None
        sec_html += f"""<div class="sec" style="padding:10px 0">{donut(a, 68, 8)}<div class="meta"><b>{name}</b>
<span>Group average out of 100</span><div style="margin-top:6px">{delta(a, pa)}</div></div></div>"""

    charts = f"""<div class="grid g32 mt">
<div class="card"><div class="card-head"><div><h3>Score distribution</h3><div class="sub">How many students scored in each band (out of 300)</div></div>
<span class="chip brand">Avg {fmt(avg)}</span></div>{histogram(totals)}</div>
<div class="card"><div class="card-head"><div><h3>Section averages</h3><div class="sub">Change compared with the previous mock</div></div></div>{sec_html}</div></div>"""

    # weak skills
    sk = skill_stats(recs)
    weak = "".join(f"""<div class="skill"><div class="row"><b>{e(s['skill'])} <span class="tag">{SHORT[s['sec']]}</span></b>
<span>{fmt(s['pct'])}% correct</span></div>{minibar(s['pct'], '')}</div>""" for s in sk[:6])

    # needs attention
    att = []
    for r in review:
        n = len(r["read"]["flags"]) + len(r["read"].get("student_id_flags", []))
        att.append(("low", "alert", r.get("student_name") or r["student_id"], f"{n} unclear mark{'s' if n != 1 else ''} on the sheet",
                    f"/result/{t['paper']}/{r['student_id']}", "Review"))
    if prev_recs:
        pm = {r["student_id"]: total(r) for r in prev_recs}
        for r in recs:
            if r["student_id"] in pm and total(r) - pm[r["student_id"]] <= -12:
                att.append(("mid", "down", r.get("student_name"), f"Dropped {fmt(pm[r['student_id']] - total(r))} marks since {prev['title']}",
                            f"/students/{r['student_id']}", "Profile"))
            elif r["student_id"] in pm and total(r) - pm[r["student_id"]] >= 25:
                att.append(("good", "up", r.get("student_name"), f"Up {fmt(total(r) - pm[r['student_id']])} marks since {prev['title']}",
                            f"/students/{r['student_id']}", "Profile"))
    missing = [s for sid, s in roster.items() if sid not in {r["student_id"] for r in recs}]
    if missing:
        att.append(("grey", "clock", f"{len(missing)} sheet{'s' if len(missing) != 1 else ''} not scanned",
                    ", ".join(m["name"] for m in missing[:3]) + ("…" if len(missing) > 3 else ""), "/scan", "Scan"))
    bg = {"low": "i-low", "mid": "i-mid", "good": "i-good", "grey": "i-brand"}
    att_html = "".join(f"""<div class="li"><div class="dot {bg[k]}">{ic(i)}</div><div class="grow"><b>{e(a or '')}</b><span>{e(b)}</span></div>
<a class="btn btn-light btn-sm" href="{href}">{lab}</a></div>""" for k, i, a, b, href, lab in att[:7]) or '<div class="empty">Nothing needs attention 🎉</div>'

    attention = f"""<div class="grid g2 mt">
<div class="card"><div class="card-head"><div><h3>Group weak spots</h3><div class="sub">Skills the whole group found hardest in this test</div></div>
<a class="btn btn-light btn-sm" href="/tests/{e(t['paper'])}">Full analysis</a></div>{weak}</div>
<div class="card"><div class="card-head"><div><h3>Needs attention</h3><div class="sub">Sheets to check and students to watch</div></div></div>
<div class="list">{att_html}</div></div></div>"""

    # leaderboard + activity
    rk = sorted(recs, key=lambda r: -total(r))[:6]
    lb = "".join(f"""<tr class="click" data-href="/students/{e(r['student_id'])}"><td><div class="rank r{i + 1}">{i + 1}</div></td>
<td>{who(r.get('student_name') or r['student_id'], roster.get(r['student_id'], {}).get('batch', ''))}</td>
<td style="width:40%">{minibar(total(r) / 3, fmt(total(r)))}</td></tr>""" for i, r in enumerate(rk))
    events = []
    for r in recs:
        nm = r.get("student_name") or r["student_id"]
        if r.get("scanned_at"):
            events.append((r["scanned_at"], "scan", f"Sheet scanned and marked", nm))
        if r.get("emailed"):
            try:
                when = dt.datetime.strptime(r["emailed"]["at"], "%d %b %Y, %I:%M %p").isoformat()
            except ValueError:
                when = ""
            events.append((when, "send", "Report emailed to parent", nm))
    events.sort(reverse=True)
    act = "".join(f"""<div class="li"><div class="dot {'i-good' if k == 'send' else 'i-brand'}">{ic('send' if k == 'send' else 'scan')}</div>
<div class="grow"><b>{e(nm)}</b><span>{e(txt)}</span></div><span class="tag">{e(when_txt(w))}</span></div>""" for w, k, txt, nm in events[:6])
    bottom = f"""<div class="grid g2 mt">
<div class="card"><div class="card-head"><div><h3>Top performers</h3><div class="sub">{e(t['title'])} leaderboard</div></div>
<a class="btn btn-light btn-sm" href="/tests/{e(t['paper'])}">See all</a></div><table class="t"><tbody>{lb}</tbody></table></div>
<div class="card"><div class="card-head"><div><h3>Recent activity</h3><div class="sub">Latest scans and emails</div></div></div><div class="list">{act or '<div class="empty">No activity yet</div>'}</div></div></div>"""

    return shell(request, "Dashboard", "Dashboard", hero + kpis + charts + attention + bottom,
                 sub=f"{e(t['title'])} · {ddate(t['date'], True)}")


def when_txt(iso):
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    days = (dt.datetime.now() - d).days
    if days <= 0:
        return d.strftime("Today %-I:%M %p")
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return d.strftime("%-d %b")


# ======================================================================
# Scan sheets
# ======================================================================

def steps_bar(active):
    names = [("Choose test", "Pick the paper"), ("Upload photos", "One per sheet"),
             ("Review", "Confirm unclear marks"), ("Email reports", "Parents get a PDF")]
    out = ""
    for i, (a, b) in enumerate(names):
        cls = "done" if i < active else "on" if i == active else ""
        out += f'<div class="step {cls}"><i>{"✓" if i < active else i + 1}</i><div><b>{a}</b><span>{b}</span></div></div>'
    return f'<div class="steps">{out}</div>'


@app.get("/scan", response_class=HTMLResponse)
def scan(request: Request):
    t = current_test(request)
    opts = "".join(f'<option value="{e(k["paper"])}" {"selected" if k["paper"] == t["paper"] else ""}>{e(k["title"])} · {ddate(k["date"])}</option>'
                   for k in reversed(tests()))
    recs = results(t["paper"])
    review = [r for r in recs if r["status"] != "ok"]
    rev_html = "".join(f"""<div class="li"><div class="dot i-mid">{ic('alert')}</div><div class="grow"><b>{e(r.get('student_name') or r['student_id'])}</b>
<span>{len(r['read']['flags'])} unclear mark(s)</span></div><a class="btn btn-primary btn-sm" href="/result/{e(t['paper'])}/{e(r['student_id'])}">Review</a></div>""" for r in review)
    recent = sorted(recs, key=lambda r: r.get("scanned_at", ""), reverse=True)[:5]
    rec_html = "".join(f"""<div class="li">{avatar(r.get('student_name') or r['student_id'])}<div class="grow"><b>{e(r.get('student_name') or r['student_id'])}</b>
<span>{fmt(total(r))} / 300 · {e(when_txt(r.get('scanned_at', '')))}</span></div>{STATUS_CHIP[status_of(r)]}</div>""" for r in recent)
    body = f"""{steps_bar(1)}
<div class="grid g21" style="align-items:start">
<div class="card"><form action="/upload" method="post" enctype="multipart/form-data" onsubmit="busy('Reading answer sheets…','Straightening photos, reading bubbles and marking')">
<div class="field"><label>TEST</label><select name="paper">{opts}</select></div>
<label class="drop" id="drop"><div class="big">{ic('upload')}</div><b>Drop answer-sheet photos here</b>
<span>or click to choose from your computer or phone · JPG or PNG · as many as you like</span>
<input type="file" name="files" id="files" accept="image/*" multiple required></label>
<div class="files" id="flist"></div>
<div style="display:flex;justify-content:space-between;align-items:center;margin-top:18px">
<span class="sub" style="color:var(--muted)">Each photo is read in about a second.</span>
<button class="btn btn-primary" type="submit">{ic('scan')} Read &amp; mark sheets</button></div></form></div>
<div>
<div class="card"><h3>Photo tips</h3>
<div class="tip"><div class="n">1</div><div><b>All four corner squares</b><span>They let the system straighten the page.</span></div></div>
<div class="tip"><div class="n">2</div><div><b>Flat, bright, no shadow</b><span>Daylight or room light works well.</span></div></div>
<div class="tip"><div class="n">3</div><div><b>Phone straight above</b><span>A slight tilt is fine.</span></div></div></div>
{f'<div class="card mt"><h3>Waiting for review <small>{len(review)}</small></h3><div class="list">{rev_html}</div></div>' if review else ''}
<div class="card mt"><h3>Recently scanned</h3><div class="list">{rec_html or '<div class="empty">Nothing yet</div>'}</div></div>
</div></div>
<script>
const inp=document.getElementById('files'),dz=document.getElementById('drop'),fl=document.getElementById('flist');
function show(){{fl.innerHTML=[...inp.files].map(f=>'<span class="f">📄 '+f.name+'</span>').join('')}}
inp.addEventListener('change',show);
['dragover','dragenter'].forEach(ev=>dz.addEventListener(ev,x=>{{x.preventDefault();dz.classList.add('over')}}));
['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,x=>{{x.preventDefault();dz.classList.remove('over')}}));
dz.addEventListener('drop',x=>{{inp.files=x.dataTransfer.files;show()}});
</script>"""
    return shell(request, "Scan sheets", "Scan answer sheets", body, picker=False,
                 sub="Upload a photo of each student's answer sheet. Everything after that is automatic.")


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, paper: str = Form(...), files: list[UploadFile] = File(...)):
    key = load_key(paper)
    roster = students()
    rows, ok_n, rev_n, bad_n = [], 0, 0, 0
    for i, f in enumerate(files):
        data = await f.read()
        try:
            res = read_sheet(data, debug=True)
        except (SheetError, Exception) as err:
            bad_n += 1
            rows.append(f"""<tr><td><b>{e(f.filename)}</b></td><td>-</td><td><span class="chip low">Retake photo</span></td>
<td colspan="2" class="sub" style="color:var(--muted)">{e(str(err))}</td><td></td></tr>""")
            continue
        overlay = res.pop("_overlay")
        sid = res["student_id"]
        if "?" in sid:
            sid = f"UNKNOWN-{Path(f.filename).stem}-{i}"
        answers = {sec: {str(q): a for q, a in qa.items()} for sec, qa in res["answers"].items()}
        marks = score(answers, key)
        rec = {"paper": paper, "student_id": sid, "source_file": f.filename,
               "student_name": roster.get(sid, {}).get("name"), "read": {**res, "answers": answers},
               "marks": marks, "status": res["status"], "review": {},
               "scanned_at": dt.datetime.now().isoformat(timespec="minutes")}
        jpath, ipath = record_path(paper, sid)
        jpath.write_text(json.dumps(rec, indent=1))
        small = cv2.resize(overlay, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(ipath), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if res["status"] == "ok":
            ok_n += 1
        else:
            rev_n += 1
        tot = total(rec)
        secs = " ".join(f'<span class="tag">{SHORT[c]} {m["correct"]}/{m["total"]}</span>' for c, m in marks.items())
        rows.append(f"""<tr class="click" data-href="/result/{e(paper)}/{e(sid)}">
<td>{who(rec['student_name'] or 'Unknown student', 'ID ' + sid)}</td><td class="sub" style="color:var(--muted)">{e(f.filename)}</td>
<td>{STATUS_CHIP['ready' if res['status'] == 'ok' else 'review']}</td><td>{secs}</td>
<td style="width:180px">{minibar(tot / 3, fmt(tot))}</td><td><a class="btn btn-light btn-sm" href="/result/{e(paper)}/{e(sid)}">Open</a></td></tr>""")
    resp = shell(request, "Scan sheets", "Sheets marked", f"""{steps_bar(2)}
<div class="grid g3"><div class="card kpi"><div class="lbl"><div class="ico i-good">{ic('check')}</div>Marked</div><div class="val">{ok_n}</div><div class="foot">Ready for reports</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-mid">{ic('alert')}</div>Needs review</div><div class="val">{rev_n}</div><div class="foot">Unclear marks to confirm</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-low">{ic('camera')}</div>Retake photo</div><div class="val">{bad_n}</div><div class="foot">Corners not found or too blurry</div></div></div>
<div class="card mt"><div class="card-head"><div><h3>Results</h3><div class="sub">Click a student to see their marked sheet</div></div>
<div style="display:flex;gap:8px"><a class="btn btn-light" href="/scan">{ic('plus')} Scan more</a><a class="btn btn-primary" href="/reports">{ic('send')} Go to reports</a></div></div>
<table class="t"><thead><tr><th>Student</th><th>Photo</th><th>Status</th><th>Sections</th><th>Total /300</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>""",
                 picker=False, sub=f"{len(files)} photo{'s' if len(files) != 1 else ''} read for {e(key.get('title', paper))}")
    resp.set_cookie("test", paper, max_age=60 * 60 * 24 * 365)
    return resp


# ======================================================================
# Students
# ======================================================================

def history():
    """{sid: {paper: rec}} plus the ordered list of tests that have results."""
    ts = [t for t in tests() if results(t["paper"])]
    h = {}
    for t in ts:
        for r in results(t["paper"]):
            h.setdefault(r["student_id"], {})[t["paper"]] = r
    return h, ts


@app.get("/students", response_class=HTMLResponse)
def students_page(request: Request, q: str = "", batch: str = ""):
    roster = students()
    hist, ts = history()
    cur = current_test(request)
    cur_ranks = ranks(results(cur["paper"]))
    batches = {}
    for s in roster.values():
        batches[s.get("batch", "")] = batches.get(s.get("batch", ""), 0) + 1
    chips = f'<a class="fchip {"on" if not batch else ""}" href="/students?q={quote(q)}">All<em>{len(roster)}</em></a>' + "".join(
        f'<a class="fchip {"on" if b == batch else ""}" href="/students?batch={quote(b)}&q={quote(q)}">{e(b)}<em>{n}</em></a>'
        for b, n in sorted(batches.items()) if b)
    rows = ""
    shown = 0
    for sid, s in sorted(roster.items(), key=lambda kv: kv[1]["name"]):
        if batch and s.get("batch") != batch:
            continue
        if q and q.lower() not in (s["name"] + sid).lower():
            continue
        shown += 1
        mine = hist.get(sid, {})
        series = [total(mine[t["paper"]]) if t["paper"] in mine else None for t in ts]
        latest = next((mine[t["paper"]] for t in reversed(ts) if t["paper"] in mine), None)
        cur_rec = mine.get(cur["paper"])
        lt = total(latest) if latest else None
        rows += f"""<tr class="click" data-href="/students/{e(sid)}"><td>{who(s['name'], 'ID ' + sid)}</td>
<td><span class="tag">{e(s.get('batch', ''))}</span></td><td class="num">{len(mine)}</td>
<td style="width:190px">{minibar(lt / 3, fmt(lt)) if lt is not None else '<span class="tag">—</span>'}</td>
<td>{sparkline(series)}</td>
<td class="num">{('#' + str(cur_ranks[sid])) if sid in cur_ranks else '—'}</td>
<td>{STATUS_CHIP[status_of(cur_rec)]}</td></tr>"""
    body = f"""<div class="card"><div class="card-head"><div class="filters">{chips}</div>
<form class="search" action="/students">{ic('search')}<input name="q" value="{e(q)}" placeholder="Name or ID"><input type="hidden" name="batch" value="{e(batch)}"></form></div>
<table class="t"><thead><tr><th>Student</th><th>Batch</th><th class="num">Tests</th><th>Latest score /300</th><th>Trend</th>
<th class="num">Rank · {e(cur['title'])}</th><th>{e(cur['title'])}</th></tr></thead><tbody>{rows or '<tr><td colspan="7" class="empty">No students match.</td></tr>'}</tbody></table></div>"""
    return shell(request, "Students", "Students", body, sub=f"{shown} of {len(roster)} students · results from {len(ts)} mock tests")


@app.get("/students/{sid}", response_class=HTMLResponse)
def student_profile(request: Request, sid: str):
    roster = students()
    s = roster.get(sid, {"name": f"Student {sid}", "batch": "", "parent_email": ""})
    hist, ts = history()
    mine = hist.get(sid, {})
    taken = [t for t in ts if t["paper"] in mine]
    crumbs = '<div class="crumbs"><a href="/students">Students</a> / Profile</div>'
    head = f'<div class="profile">{avatar(s["name"], sid, 64)}<div><h1>{e(s["name"])}</h1><div class="meta-row"><span>ID {e(sid)}</span><span>{e(s.get("batch", ""))}</span><span>✉ {e(s.get("parent_email", ""))}</span></div></div></div>'
    if not taken:
        return shell(request, "Students", s["name"], '<div class="card empty">No tests scanned yet for this student.</div>', picker=False, head=head, crumbs=crumbs)
    last = mine[taken[-1]["paper"]]
    first = mine[taken[0]["paper"]]
    rk = ranks(results(taken[-1]["paper"]))
    n_sat = len(results(taken[-1]["paper"]))
    pctile = round(100 * (n_sat - rk[sid]) / max(1, n_sat - 1)) if n_sat > 1 else 100
    group_avg = []
    for t in ts:
        rs = results(t["paper"])
        group_avg.append(sum(total(r) for r in rs) / len(rs))
    kpis = f"""<div class="grid g4">
<div class="card kpi"><div class="lbl"><div class="ico i-brand">{ic('chart')}</div>Latest score</div><div class="val">{fmt(total(last))}<small> / 300</small></div>
<div class="foot">{e(taken[-1]['title'])}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-good">{ic('up')}</div>Progress</div><div class="val">{delta(total(last), total(first)) if len(taken) > 1 else '—'}</div>
<div class="foot">Since {e(taken[0]['title'])}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-mid">{ic('trophy')}</div>Rank</div><div class="val">#{rk[sid]}<small> of {n_sat}</small></div>
<div class="foot">Better than {pctile}% of the group</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-brand">{ic('tests')}</div>Tests taken</div><div class="val">{len(taken)}<small> / {len(ts)}</small></div>
<div class="foot">{STATUS_CHIP[status_of(last)]}</div></div></div>"""
    xl = [t["title"].replace("Mock Test", "Mock") for t in ts]
    chart = line_chart(xl, [(s["name"].split()[0], "#4338CA", [total(mine[t["paper"]]) if t["paper"] in mine else None for t in ts], False),
                            ("Group average", "#9AA0AE", group_avg, True)])
    sec_rows = ""
    for code, name, short in SECS:
        vals = [mine[t["paper"]]["marks"][code]["score_100"] if t["paper"] in mine else None for t in ts]
        lv = last["marks"][code]["score_100"]
        sec_rows += f"""<div class="sec" style="padding:9px 0">{donut(lv, 58, 7)}<div class="meta" style="flex:1"><b>{name}</b>
<span>{last['marks'][code]['correct']} of 35 correct</span></div>{sparkline(vals)}</div>"""
    sk = skill_stats([mine[t["paper"]] for t in taken])
    weak = [x for x in sk if x["pct"] < 60][:5]
    strong = [x for x in reversed(sk) if x["pct"] >= 70][:5]
    sk_html = lambda lst: "".join(f"""<div class="skill"><div class="row"><b>{e(x['skill'])} <span class="tag">{SHORT[x['sec']]}</span></b>
<span>{x['right']}/{x['n']}</span></div>{minibar(x['pct'], fmt(x['pct']) + '%')}</div>""" for x in lst) or '<div class="empty">—</div>'
    focus = "".join(f"""<div class="tip"><div class="n">{i + 1}</div><div><b>{e(x['skill'])}</b><span>{e(TIPS.get(x['skill'], SECTION_TIPS[x['sec']]))}</span></div></div>""" for i, x in enumerate(weak[:3]))
    rows = ""
    for t in reversed(taken):
        r = mine[t["paper"]]
        rr = ranks(results(t["paper"]))
        cells = "".join(f'<td class="num">{fmt(r["marks"][c]["score_100"])}</td>' for c, _, _ in SECS)
        rows += f"""<tr><td><b>{e(t['title'])}</b><div class="sub" style="color:var(--muted);font-size:12px">{ddate(t['date'])}</div></td>{cells}
<td style="width:170px">{minibar(total(r) / 3, fmt(total(r)))}</td><td class="num">#{rr[sid]}</td><td>{STATUS_CHIP[status_of(r)]}</td>
<td style="text-align:right;white-space:nowrap"><a class="btn btn-light btn-sm" href="/result/{e(t['paper'])}/{e(sid)}">{ic('eye')} Sheet</a>
<a class="btn btn-light btn-sm" href="/report/{e(t['paper'])}/{e(sid)}.pdf" target="_blank">{ic('file')} Report</a></td></tr>"""
    body = f"""{kpis}
<div class="grid g21 mt"><div class="card"><div class="card-head"><div><h3>Progress across mock tests</h3><div class="sub">Total score out of 300</div></div></div>{chart}</div>
<div class="card"><div class="card-head"><div><h3>Sections</h3><div class="sub">Latest test, with trend</div></div></div>{sec_rows}</div></div>
<div class="grid g3 mt"><div class="card"><h3>Strengths</h3><div class="sub">Across all tests</div>{sk_html(strong)}</div>
<div class="card"><h3>Skills to build</h3><div class="sub">Across all tests</div>{sk_html(weak)}</div>
<div class="card"><h3>Coaching plan</h3><div class="sub">What to practise next</div>{focus or '<div class="empty">No weak skills 🎉</div>'}</div></div>
<div class="card mt"><div class="card-head"><div><h3>Test history</h3><div class="sub">Every mock test, its marked sheet and the parent report</div></div></div>
<table class="t"><thead><tr><th>Test</th><th class="num">Reading</th><th class="num">Maths</th><th class="num">Abstract</th><th>Total /300</th><th class="num">Rank</th><th>Status</th><th></th></tr></thead>
<tbody>{rows}</tbody></table></div>"""
    return shell(request, "Students", s["name"], body, picker=False, head=head, crumbs=crumbs)


# ======================================================================
# Tests
# ======================================================================

@app.get("/tests", response_class=HTMLResponse)
def tests_page(request: Request):
    cards = ""
    n_students = len(students())
    for t in reversed(tests()):
        rs = results(t["paper"])
        d = dt.date.fromisoformat(t["date"])
        if rs:
            avg = sum(total(r) for r in rs) / len(rs)
            hi = max(total(r) for r in rs)
            done = sum(1 for r in rs if r.get("emailed"))
            state = '<span class="chip good">Reports sent</span>' if done == len(rs) else '<span class="chip brand">Marking</span>'
            stats = f'<div class="stats3"><div><b>{len(rs)}</b><span>Sheets</span></div><div><b>{fmt(avg)}</b><span>Average</span></div><div><b>{fmt(hi)}</b><span>Highest</span></div></div>'
            prog = f'<div style="margin-top:14px">{minibar(100 * done / len(rs), f"{done}/{len(rs)}")}<div class="sub" style="margin-top:4px;color:var(--muted);font-size:12px">Reports emailed</div></div>'
        else:
            state = '<span class="chip grey">Upcoming</span>'
            stats = f'<div class="stats3"><div><b>0</b><span>Sheets</span></div><div><b>{n_students}</b><span>Enrolled</span></div><div><b>105</b><span>Questions</span></div></div>'
            prog = '<div style="margin-top:14px" class="sub"><span style="color:var(--muted);font-size:12px">Answer key loaded · ready to scan on the day</span></div>'
        cards += f"""<a class="card test-card" href="/tests/{e(t['paper'])}"><div style="display:flex;gap:14px;align-items:center">
<div class="date"><b>{d.day}</b><span>{d.strftime('%b')}</span></div><div style="flex:1"><h3>{e(t['title'])}</h3>
<div class="sub">Reading · Maths · Abstract · {d.strftime('%A')}</div></div>{state}</div>{stats}{prog}</a>"""
    return shell(request, "Tests", "Tests", f'<div class="grid g3">{cards}</div>', picker=False,
                 sub="Every mock test, its answer key and how the group performed")


@app.get("/tests/{paper}", response_class=HTMLResponse)
def test_detail(request: Request, paper: str):
    key = load_key(paper)
    recs = results(paper)
    roster = students()
    crumbs = '<div class="crumbs"><a href="/tests">Tests</a> / Analysis</div>'
    resp_sub = f"{ddate(key['date'], True)} · {len(recs)} sheets marked"
    if not recs:
        r = shell(request, "Tests", key["title"], f"""<div class="card hero"><h2>Not sat yet</h2><p>The answer key for {e(key['title'])} is loaded.
Scan the sheets after the test and the analysis will appear here.</p><div class="hero-actions"><a class="btn btn-white" href="/scan">{ic('camera')} Scan sheets</a></div></div>""",
                  picker=False, crumbs=crumbs, sub=resp_sub)
        return r
    totals = sorted(recs, key=lambda r: -total(r))
    avg = sum(total(r) for r in recs) / len(recs)
    med = sorted(total(r) for r in recs)[len(recs) // 2]

    # question analysis
    qa_html, alerts = "", []
    for code, name, short in SECS:
        bars, labs = "", ""
        for qn, q in key["sections"][code]["questions"].items():
            given = [r["marks"][code]["questions"][qn]["given"] for r in recs]
            right = sum(1 for g in given if g == q["answer"])
            pct = 100 * right / len(recs)
            wrong = {}
            for g in given:
                if g and g != q["answer"]:
                    wrong[g] = wrong.get(g, 0) + 1
            top_wrong = max(wrong.items(), key=lambda kv: kv[1]) if wrong else None
            tip = f"Q{qn} · {q['skill']} · {fmt(pct)}% correct (answer {q['answer']})"
            if top_wrong:
                tip += f" · most common wrong answer {top_wrong[0]} ({top_wrong[1]} students)"
            bars += f'<div class="qbar" title="{e(tip)}" style="height:{max(4, pct):.0f}%;background:{COL[level(pct)]}"></div>'
            labs += f"<span>{qn}</span>"
            if top_wrong and pct < 30 and top_wrong[1] >= 0.5 * len(recs):
                alerts.append(f"<b>{short} Q{qn}</b>: only {fmt(pct)}% got it right, and {top_wrong[1]} of {len(recs)} students chose "
                              f"<b>{top_wrong[0]}</b> instead of <b>{q['answer']}</b>. Check the answer key, or reteach <b>{e(q['skill'])}</b>.")
        avg_s = sum(r["marks"][code]["score_100"] for r in recs) / len(recs)
        qa_html += f"""<div style="margin:6px 0 22px"><div style="display:flex;justify-content:space-between;margin-bottom:4px"><b>{name}</b>
<span class="chip {level(avg_s)}">Average {fmt(avg_s)}</span></div><div class="qstrip">{bars}</div><div class="qlab">{labs}</div></div>"""
    alert_html = "".join(f'<div class="alert" style="margin-bottom:10px">{ic("alert")}<div>{a}</div></div>' for a in alerts)

    # leaderboard
    rk = ranks(recs)
    lb = ""
    for r in totals:
        sid = r["student_id"]
        nm = r.get("student_name") or sid
        cells = "".join(f'<td class="num">{fmt(r["marks"][c]["score_100"])}</td>' for c, _, _ in SECS)
        lb += f"""<tr class="click" data-href="/result/{e(paper)}/{e(sid)}"><td><div class="rank {'r' + str(rk[sid]) if rk[sid] <= 3 else ''}">{rk[sid]}</div></td>
<td>{who(nm, roster.get(sid, {}).get('batch', ''))}</td>{cells}<td style="width:200px">{minibar(total(r) / 3, fmt(total(r)))}</td><td>{STATUS_CHIP[status_of(r)]}</td></tr>"""

    # heatmap students x skills
    skills = []
    for code, _, _ in SECS:
        for q in key["sections"][code]["questions"].values():
            if (code, q["skill"]) not in skills:
                skills.append((code, q["skill"]))
    head = "".join(f'<th class="v"><div>{e(sk)}</div></th>' for _, sk in skills)
    hm = ""
    for r in totals:
        cells = ""
        for code, sk in skills:
            qs = [q for q in r["marks"][code]["questions"].values() if q["skill"] == sk]
            p = 100 * sum(q["ok"] for q in qs) / len(qs)
            bgc = {"good": "#BFE8D3", "mid": "#FBE1B3", "low": "#F7C2BE"}[level(p)]
            cells += f'<td class="c" style="background:{bgc}" title="{e(sk)}: {sum(q["ok"] for q in qs)}/{len(qs)}">{p:.0f}</td>'
        hm += f'<tr><td class="n">{e((r.get("student_name") or r["student_id"]))}</td>{cells}</tr>'
    sec_head = "".join(f'<th colspan="{sum(1 for c, _ in skills if c == code)}" style="text-align:center;color:var(--brand);font-size:11px">{short.upper()}</th>' for code, _, short in SECS)

    body = f"""<div class="grid g4">
<div class="card kpi"><div class="lbl"><div class="ico i-brand">{ic('users')}</div>Sheets marked</div><div class="val">{len(recs)}</div><div class="foot">of {len(roster)} students</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-good">{ic('chart')}</div>Average</div><div class="val">{fmt(avg)}<small> / 300</small></div><div class="foot">Median {fmt(med)}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-mid">{ic('trophy')}</div>Highest</div><div class="val">{fmt(total(totals[0]))}<small> / 300</small></div><div class="foot">{e(totals[0].get('student_name') or '')}</div></div>
<div class="card kpi"><div class="lbl"><div class="ico i-low">{ic('target')}</div>Hardest skill</div><div class="val" style="font-size:19px;margin-top:16px">{e(skill_stats(recs)[0]['skill'])}</div>
<div class="foot">{fmt(skill_stats(recs)[0]['pct'])}% correct across the group</div></div></div>
<div class="grid g32 mt"><div class="card"><div class="card-head"><div><h3>Question analysis</h3><div class="sub">% of students who got each question right · hover a bar for details</div></div>
<div class="legend"><span><i style="background:#22A06B"></i>70%+</span><span><i style="background:#F2A93B"></i>45–69%</span><span><i style="background:#E5534B"></i>under 45%</span></div></div>{qa_html}</div>
<div><div class="card"><h3>Smart alerts</h3><div class="sub" style="margin-bottom:14px">Questions where most students chose the same wrong answer</div>
{alert_html or '<div class="okmsg">' + ic('check') + ' No suspicious questions found</div>'}</div>
<div class="card mt"><h3>Score distribution</h3><div class="sub" style="margin-bottom:8px">Totals out of 300</div>{histogram([total(r) for r in recs], w=440, h=190)}</div></div></div>
<div class="card mt"><div class="card-head"><div><h3>Leaderboard</h3><div class="sub">Click a student to open their marked sheet</div></div></div>
<table class="t"><thead><tr><th>#</th><th>Student</th><th class="num">Reading</th><th class="num">Maths</th><th class="num">Abstract</th><th>Total /300</th><th>Status</th></tr></thead><tbody>{lb}</tbody></table></div>
<div class="card mt"><div class="card-head"><div><h3>Skill heatmap</h3><div class="sub">% correct for every student in every skill: spot who needs help with what</div></div>
<div class="legend"><span><i style="background:#BFE8D3"></i>Strong</span><span><i style="background:#FBE1B3"></i>Developing</span><span><i style="background:#F7C2BE"></i>Needs focus</span></div></div>
<div style="overflow-x:auto"><table class="heat"><thead><tr><th></th>{sec_head}</tr><tr><th></th>{head}</tr></thead><tbody>{hm}</tbody></table></div></div>"""
    return shell(request, "Tests", key["title"], body, picker=False, crumbs=crumbs, sub=resp_sub)


# ======================================================================
# Reports
# ======================================================================

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, sent: int = -1, err: str = ""):
    t = current_test(request)
    recs = sorted(results(t["paper"]), key=lambda r: (status_of(r) != "ready", status_of(r) != "review", r.get("student_name") or ""))
    roster = students()
    n_sent = sum(1 for r in recs if r.get("emailed"))
    n_ready = sum(1 for r in recs if status_of(r) == "ready")
    n_rev = sum(1 for r in recs if status_of(r) == "review")
    rows = ""
    for r in recs:
        sid, st = r["student_id"], status_of(r)
        nm = r.get("student_name") or sid
        email = (S.SEND_ALL_TO and f"{S.SEND_ALL_TO} (test)") or roster.get(sid, {}).get("parent_email", "—")
        act = (f'<a class="btn btn-light btn-sm" href="/result/{e(t["paper"])}/{e(sid)}">Review</a>' if st == "review" else
               f'<form method="post" action="/send-report/{e(t["paper"])}/{e(sid)}?back=reports" style="display:inline" onsubmit="busy(\'Sending report…\',\'{e(nm)}\')">'
               f'<button class="btn {"btn-light" if st == "sent" else "btn-primary"} btn-sm">{ic("send")} {"Resend" if st == "sent" else "Send"}</button></form>')
        rows += f"""<tr><td>{who(nm, roster.get(sid, {}).get('parent_email', ''))}</td><td style="width:170px">{minibar(total(r) / 3, fmt(total(r)))}</td>
<td>{STATUS_CHIP[st]}</td><td class="sub" style="color:var(--muted);font-size:12.5px">{e(r['emailed']['at']) if r.get('emailed') else '—'}</td>
<td style="text-align:right;white-space:nowrap"><a class="btn btn-light btn-sm" href="/report/{e(t['paper'])}/{e(sid)}.pdf" target="_blank">{ic('eye')} Preview</a> {act}</td></tr>"""
    mode = (f'<div class="alert" style="margin-bottom:18px">{ic("alert")}<div><b>Test mode is on.</b> Every report goes to {e(S.SEND_ALL_TO)} instead of the parent. '
            f'Turn it off in settings.py when you are ready.</div></div>' if S.SEND_ALL_TO else "")
    msg = ""
    if sent >= 0:
        msg = f'<div class="okmsg" style="margin-bottom:18px">{ic("check")} {sent} report{"s" if sent != 1 else ""} emailed.</div>'
    if err:
        msg += f'<div class="errmsg" style="margin-bottom:18px"><b>Email not sent.</b> {e(err)}</div>'
    pct = 100 * n_sent / len(recs) if recs else 0
    body = f"""{mode}{msg}<div class="grid g12">
<div class="card" style="display:flex;align-items:center;gap:20px">{donut(pct, 110, 12, f"{n_sent}/{len(recs)}", "#22A06B")}
<div><h3>Reports emailed</h3><div class="sub">{n_ready} ready · {n_rev} waiting for review</div>
<form method="post" action="/reports/send-all" style="margin-top:14px" onsubmit="busy('Emailing {n_ready} reports…','Building each PDF and sending it')">
<button class="btn btn-primary" {'disabled' if not n_ready else ''}>{ic('send')} Email all ready ({n_ready})</button></form></div></div>
<div class="card"><h3>What parents receive</h3><div class="sub">A short visual email with the full 2-page PDF report attached</div>
<div style="display:flex;gap:12px;margin-top:14px;flex-wrap:wrap">
<span class="chip brand">Overall score ring</span><span class="chip brand">Section scores</span><span class="chip brand">Skill-by-skill chart</span>
<span class="chip brand">Top 3 focus areas</span><span class="chip brand">Answer map</span><span class="chip brand">Two-week plan</span><span class="chip brand">Tips for parents</span></div></div></div>
<div class="card mt"><table class="t"><thead><tr><th>Student · parent email</th><th>Total /300</th><th>Status</th><th>Sent</th><th></th></tr></thead>
<tbody>{rows or '<tr><td colspan="5" class="empty">No sheets scanned for this test yet.</td></tr>'}</tbody></table></div>"""
    return shell(request, "Reports", "Parent reports", body, sub=f"{e(t['title'])} · emails are sent from {e(S.GMAIL_ADDRESS)}")


@app.post("/reports/send-all")
def send_all(request: Request):
    t = current_test(request)
    roster = students()
    n, err = 0, ""
    recs = results(t["paper"])
    for r in recs:
        if status_of(r) != "ready":
            continue
        try:
            pdf, d = build_pdf(r, load_key(t["paper"]), recs)
            to = send_report(pdf, d, roster.get(r["student_id"], {}).get("parent_email"))
        except EmailSetupError as x:
            err = str(x)
            break
        except Exception as x:
            err = f"{type(x).__name__}: {x}"
            break
        r["emailed"] = {"to": to, "at": dt.datetime.now().strftime("%d %b %Y, %I:%M %p")}
        record_path(t["paper"], r["student_id"])[0].write_text(json.dumps(r, indent=1))
        n += 1
    return RedirectResponse(f"/reports?sent={n}&err={quote(err)}", status_code=303)


# ======================================================================
# Single result (marked sheet + review)
# ======================================================================

@app.get("/result/{paper}/{sid}", response_class=HTMLResponse)
def result(request: Request, paper: str, sid: str, msg: str = ""):
    jpath, _ = record_path(paper, sid)
    if not jpath.exists():
        return shell(request, "Scan sheets", "Not found", '<div class="card empty">No such result.</div>', picker=False)
    rec = json.loads(jpath.read_text())
    key = load_key(paper)
    r = rec["read"]
    roster = students()
    nm = rec.get("student_name") or f"Student {sid}"
    recs = results(paper)
    rk = ranks(recs)
    st = status_of(rec)

    review_rows = ""
    for fl in r["flags"]:
        sec, q = fl["section"], str(fl["question"])
        cur = rec["review"].get(f"{sec}-{q}", r["answers"][sec].get(q))
        opts = "".join(f"<option value='{o}' {'selected' if cur == o else ''}>{o or 'No answer'}</option>" for o in ["", *L.OPTIONS])
        review_rows += (f"<tr><td><b>{SHORT[sec]} Q{q}</b></td><td><span class='chip mid'>{e(fl['issue'])}</span></td>"
                        f"<td><div class='field' style='margin:0'><select name='{sec}-{q}'>{opts}</select></div></td></tr>")
    id_fix = ""
    if not r["student_id_ok"] or sid.startswith("UNKNOWN"):
        id_fix = (f"<div class='alert low' style='margin-bottom:12px'>{ic('alert')}<div>Student ID unclear: {e('; '.join(r['student_id_flags']))}</div></div>"
                  f"<div class='field'><label>CORRECT STUDENT ID</label><input type='text' name='fix_id' value='{e(r['student_id'])}' maxlength='6'></div>")
    review = ""
    if review_rows or id_fix:
        review = f"""<div class="card" style="border-color:#F5D59B;margin-bottom:18px"><div class="card-head"><div><h3>Check these marks</h3>
<div class="sub">Look at the sheet below (orange and red circles) and pick what the student meant.</div></div></div>
<form action="/review/{e(paper)}/{e(sid)}" method="post">{id_fix}
<table class="t"><thead><tr><th>Question</th><th>Issue</th><th>Answer</th></tr></thead><tbody>{review_rows}</tbody></table>
<div style="margin-top:14px"><button class="btn btn-primary" type="submit">{ic('check')} Confirm answers</button></div></form></div>"""

    sec_cards = ""
    for code, name, short in SECS:
        m = rec["marks"][code]
        lost = ", ".join(f"{e(s)} ({n})" for s, n in m["skills_lost"][:3]) or "—"
        sec_cards += f"""<div class="card"><div class="sec">{donut(m['score_100'], 70, 8)}<div class="meta"><b>{name}</b>
<span>{m['correct']} of {m['total']} correct</span></div></div><div class="sub" style="margin-top:12px;font-size:12px;color:var(--muted)"><b style="color:var(--ink-2)">Lost marks in:</b> {lost}</div></div>"""

    sent = rec.get("emailed")
    to_note = (f"Test mode: sends to {e(S.SEND_ALL_TO)}" if S.SEND_ALL_TO else
               f"Sends to {e(roster.get(sid, {}).get('parent_email') or 'no email on file')}")
    actions = f"""<div class="card"><h3>Parent report</h3><div class="sub">{to_note}</div>
<div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap"><a class="btn btn-light" href="/report/{e(paper)}/{e(sid)}.pdf" target="_blank">{ic('eye')} Preview report</a>
<form method="post" action="/send-report/{e(paper)}/{e(sid)}" style="margin:0" onsubmit="busy('Generating &amp; emailing report…','{e(nm)}')">
<button class="btn btn-primary" {'disabled' if st == 'review' else ''}>{ic('send')} {'Send again' if sent else 'Generate &amp; email report'}</button></form></div>
{f'<div class="okmsg" style="margin-top:14px">{ic("check")} Emailed to {e(sent["to"])} · {e(sent["at"])}</div>' if sent else ''}
{'<div class="sub" style="margin-top:10px;color:#B7741A">Confirm the unclear marks first.</div>' if st == 'review' else ''}
{f'<div class="errmsg" style="margin-top:14px">{e(msg)}</div>' if msg else ''}</div>"""

    tot = total(rec)
    crumbs = f'<div class="crumbs"><a href="/tests/{e(paper)}">{e(key["title"])}</a> / Marked sheet</div>'
    head = f'<div class="profile">{avatar(nm, sid, 56)}<div><h1>{e(nm)}</h1><div class="meta-row"><span>ID {e(sid)}</span><span>{e(key["title"])}</span><span>{STATUS_CHIP[st]}</span></div></div></div>'
    body = f"""{review}<div class="grid g4">
<div class="card" style="display:flex;align-items:center;gap:16px">{donut(tot / 3, 92, 10, fmt(tot))}<div><div class="sub" style="color:var(--muted)">Total out of 300</div>
<div style="font-size:22px;font-weight:750;margin-top:4px">#{rk.get(sid, '—')} <small style="font-size:13px;color:var(--faint);font-weight:500">of {len(recs)}</small></div>
<div class="sub" style="color:var(--muted)">{len(r['blank_questions'])} left blank</div></div></div>{sec_cards}</div>
<div class="grid g12 mt"><div>{actions}
<div class="card mt"><h3>Answer map</h3><div class="sub" style="margin-bottom:12px">Every question at a glance</div>{answer_map(rec)}</div></div>
<div class="card"><div class="card-head"><div><h3>Marked sheet</h3><div class="sub">What the system read from the photo</div></div>
<div class="legend"><span><i style="background:#16A34A"></i>Read as filled</span><span><i style="background:#F59E0B"></i>Faint</span><span><i style="background:#DC2626"></i>Flagged</span></div></div>
<img class="sheet-img" src="/image/{e(paper)}/{e(sid)}"></div></div>"""
    return shell(request, "Students", nm, body, picker=False, head=head, crumbs=crumbs)


def answer_map(rec):
    out = ""
    for code, name, short in SECS:
        tiles = ""
        for qn, q in sorted(rec["marks"][code]["questions"].items(), key=lambda kv: int(kv[0])):
            c = "#22A06B" if q["ok"] else ("#C9CDD8" if q["given"] is None else "#E5534B")
            t = f"Q{qn}: answered {q['given'] or 'blank'}, correct {q['correct']} · {q['skill']}"
            tiles += f'<div title="{e(t)}" style="width:22px;height:22px;border-radius:5px;background:{c};color:#fff;font-size:9px;font-weight:700;display:grid;place-items:center">{qn}</div>'
        out += f'<div style="margin-bottom:12px"><div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:6px"><b>{short}</b><span class="tag">{rec["marks"][code]["correct"]}/35</span></div><div style="display:flex;flex-wrap:wrap;gap:3px">{tiles}</div></div>'
    return out + '<div class="legend"><span><i style="background:#22A06B"></i>Correct</span><span><i style="background:#E5534B"></i>Incorrect</span><span><i style="background:#C9CDD8"></i>Blank</span></div>'


@app.post("/review/{paper}/{sid}")
async def review(paper: str, sid: str, request: Request):
    form = await request.form()
    jpath, ipath = record_path(paper, sid)
    rec = json.loads(jpath.read_text())
    r = rec["read"]
    for fl in r["flags"]:
        field = f"{fl['section']}-{fl['question']}"
        if field in form:
            choice = form[field] or None
            rec["review"][field] = choice
            r["answers"][fl["section"]][str(fl["question"])] = choice
    r["flags"] = []
    new_id = (form.get("fix_id") or "").strip()
    if new_id:
        if not (new_id.isdigit() and len(new_id) == L.ID_DIGITS):
            return RedirectResponse(f"/result/{paper}/{sid}?msg=" + quote(f"Student ID must be {L.ID_DIGITS} digits."), status_code=303)
        r["student_id"], r["student_id_ok"], r["student_id_flags"] = new_id, True, []
    rec["marks"] = score(r["answers"], load_key(paper))
    rec["status"] = "ok"
    final_id = r["student_id"] if r["student_id_ok"] else sid
    rec["student_id"] = final_id
    rec["student_name"] = students().get(final_id, {}).get("name")
    new_json, new_img = record_path(paper, final_id)
    new_json.write_text(json.dumps(rec, indent=1))
    if final_id != sid:
        ipath.replace(new_img)
        jpath.unlink()
    return RedirectResponse(f"/result/{paper}/{final_id}", status_code=303)


@app.get("/image/{paper}/{sid}")
def image(paper: str, sid: str):
    return FileResponse(record_path(paper, sid)[1])


@app.get("/api/result/{paper}/{sid}")
def api_result(paper: str, sid: str):
    return json.loads(record_path(paper, sid)[0].read_text())


@app.get("/report/{paper}/{sid}.pdf")
def report_pdf(paper: str, sid: str):
    rec = json.loads(record_path(paper, sid)[0].read_text())
    pdf, _ = build_pdf(rec, load_key(paper), results(paper))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{sid}_report.pdf"'})


@app.post("/send-report/{paper}/{sid}")
def send(paper: str, sid: str, back: str = ""):
    jpath, _ = record_path(paper, sid)
    rec = json.loads(jpath.read_text())
    dest = "/reports" if back == "reports" else f"/result/{paper}/{sid}"
    sep = "&" if "?" in dest else "?"
    if rec["status"] != "ok":
        return RedirectResponse(f"{dest}{sep}{'err' if back else 'msg'}=" + quote("Confirm the flagged questions first."), status_code=303)
    try:
        pdf, d = build_pdf(rec, load_key(paper), results(paper))
        to = send_report(pdf, d, students().get(sid, {}).get("parent_email"))
    except Exception as err:
        text = str(err) if isinstance(err, EmailSetupError) else f"{type(err).__name__}: {err}. Check the internet connection."
        return RedirectResponse(f"{dest}{sep}{'err' if back else 'msg'}=" + quote(text), status_code=303)
    rec["emailed"] = {"to": to, "at": dt.datetime.now().strftime("%d %b %Y, %I:%M %p")}
    jpath.write_text(json.dumps(rec, indent=1))
    return RedirectResponse(f"{dest}{sep + 'sent=1' if back else ''}", status_code=303)


# ======================================================================
# Settings
# ======================================================================

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    pw = bool(S.GMAIL_APP_PASSWORD.strip())
    ts = tests()
    rows = "".join(f'<div class="set-row"><b>{e(t["title"])}</b><span>{ddate(t["date"])} · answer_keys/{e(t["paper"])}.json · {len(results(t["paper"]))} sheets</span></div>' for t in reversed(ts))
    body = f"""<div class="grid g2">
<div class="card"><h3>Centre</h3><div class="sub">Shown on the dashboard, reports and emails</div>
<div style="display:flex;gap:16px;align-items:center;margin:18px 0">{'<img src="/logo" style="width:64px;height:64px;border-radius:14px;object-fit:contain">' if S.LOGO_FILE and (BASE / S.LOGO_FILE).exists() else '<div class="logo-ph" style="width:64px;height:64px;font-size:11px">LOGO</div>'}
<div><b style="font-size:16px">{e(S.CENTRE_NAME)}</b><div class="sub">{e(S.CENTRE_TAGLINE)}</div></div></div>
<div class="set-row"><span>Brand colour</span><b style="display:flex;gap:8px;align-items:center"><i style="width:18px;height:18px;border-radius:5px;background:{e(S.BRAND_COLOUR)};display:inline-block"></i>{e(S.BRAND_COLOUR)}</b></div>
<div class="set-row"><span>Logo file</span><b>{e(S.LOGO_FILE or 'Placeholder')}</b></div>
<div class="set-row"><span>Students enrolled</span><b>{len(students())}</b></div>
<div class="set-row"><span>Answer sheet</span><a class="btn btn-light btn-sm" href="/static/omr_sheet.pdf" target="_blank">{ic('file')} Download printable sheet</a></div></div>
<div class="card"><h3>Email</h3><div class="sub">Reports are sent through Gmail</div>
<div class="set-row" style="margin-top:10px"><span>Sender</span><b>{e(S.GMAIL_ADDRESS)}</b></div>
<div class="set-row"><span>App password</span>{'<span class="chip good">Connected</span>' if pw else '<span class="chip low">Not set</span>'}</div>
<div class="set-row"><span>Test mode</span>{f'<span class="chip mid">On · all mail to {e(S.SEND_ALL_TO)}</span>' if S.SEND_ALL_TO else '<span class="chip good">Off · real parents</span>'}</div>
<div class="set-row"><span>Attachment</span><b>2-page PDF report</b></div></div></div>
<div class="card mt"><h3>Answer keys</h3><div class="sub">One file per test in the answer_keys folder</div><div style="margin-top:8px">{rows}</div></div>"""
    return shell(request, "Settings", "Settings", body, picker=False, sub="Change these values in settings.py")
