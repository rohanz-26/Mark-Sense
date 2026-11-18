"""
Parent report: a 2-page visual PDF + a short HTML email, and Gmail sending.

    from report import build_pdf, build_email_html, send_report
"""
import datetime as dt
import html
import smtplib
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

import settings as S

BASE = Path(__file__).parent
W, H = A4
M = 40  # page margin

SECTIONS = [("RC", "Reading Comprehension", "Reading"),
            ("QR", "Quantitative Reasoning", "Maths"),
            ("AR", "Abstract Reasoning", "Abstract")]

BRAND = HexColor(S.BRAND_COLOUR)
INK = HexColor("#1F2330")
MUTED = HexColor("#6B7280")
LINE = HexColor("#E6E8EF")
SOFT = HexColor("#F5F6FA")
GOOD, MID, LOW = HexColor("#22A06B"), HexColor("#F2A93B"), HexColor("#E5534B")
GOOD_BG, MID_BG, LOW_BG = HexColor("#E3F5EC"), HexColor("#FDF1DC"), HexColor("#FCE6E4")
BLANK = HexColor("#C9CDD8")

# ---------- practice tips per skill tag (edit freely) ----------
TIPS = {
    "Main idea": "After each paragraph, write a 5-word summary. Pick the option that covers the whole passage, not just one part.",
    "Author's stance": "Underline opinion words (sadly, clearly, surprisingly). Ask: does the writer approve, disapprove or stay neutral?",
    "Inference": "Point to the line that proves the answer. If there is no evidence in the text, rule the option out.",
    "Vocabulary in context": "Cover the word, reread the sentence and predict your own word before looking at the options.",
    "Comparing responses": "Write each writer's main view in one line first, then compare those lines, not the whole texts.",
    "Poetry and figurative language": "For each image, ask: what is being compared, and what feeling does it create?",
    "Fractions and ratios": "Draw bar models for ratio questions and practise switching between fractions, decimals and percentages.",
    "Working backwards": "Start from the final result and undo each step in reverse. Check by running it forwards.",
    "Invented rules": "Try the new rule on a simple example first, then apply it one written step at a time.",
    "Cycles and remainders": "Find the length of the repeat, then use the remainder to find the position.",
    "Data and graphs": "Read the title, axes and units before the question. Circle the exact values needed.",
    "Reasoning about counts": "List cases in order (smallest to largest) so nothing is missed or counted twice.",
    "Spatial measurement": "Sketch and label the shape. Split awkward shapes into rectangles and triangles.",
    "Rotation": "Track one marked corner through each step and check clockwise versus anticlockwise.",
    "Reflection": "Pick one feature and check that it swaps sides across the mirror line.",
    "Grid completion": "Check rows first, then columns, then diagonals. The rule usually runs one way.",
    "Texture or fill as a value": "Treat each fill or shade as a number and look for adding or cancelling rules.",
    "Count up or down": "Count sides, dots or lines in each frame and write the numbers down to spot the pattern.",
    "Position permutation": "Number the positions and follow where each item moves from frame to frame.",
}
SECTION_TIPS = {
    "RC": "Read the question first, then find and underline the evidence in the passage.",
    "QR": "Write every step down and check the answer makes sense before choosing.",
    "AR": "Change one feature at a time: shape, number, position, colour, rotation.",
}


# ---------------- data ----------------

def level(pct):
    if pct >= 70:
        return "Strong", GOOD, GOOD_BG, "#22A06B", "#E3F5EC"
    if pct >= 45:
        return "Developing", MID, MID_BG, "#C27C0E", "#FDF1DC"
    return "Needs focus", LOW, LOW_BG, "#D93A31", "#FCE6E4"


def summarise(rec, key, peers=None):
    """Turn a stored result into everything the report needs."""
    marks = rec["marks"]
    secs, all_skills = [], []
    correct = wrong = blank = 0
    for code, name, short in SECTIONS:
        m = marks[code]
        skills = {}
        for qn, q in m["questions"].items():
            s = skills.setdefault(q["skill"], {"skill": q["skill"], "section": code, "right": 0, "total": 0})
            s["total"] += 1
            s["right"] += q["ok"]
            if q["ok"]:
                correct += 1
            elif q["given"] is None:
                blank += 1
            else:
                wrong += 1
        for s in skills.values():
            s["pct"] = 100 * s["right"] / s["total"]
            s["lost"] = s["total"] - s["right"]
        sk = sorted(skills.values(), key=lambda s: (-s["pct"], s["skill"]))
        all_skills += sk
        avg = None
        if peers and len(peers) >= 3:
            avg = sum(p["marks"][code]["score_100"] for p in peers) / len(peers)
        tiles = [(int(qn), q["ok"], q["given"]) for qn, q in m["questions"].items()]
        secs.append({"code": code, "name": name, "short": short, "score": m["score_100"],
                     "correct": m["correct"], "total": m["total"], "skills": sk,
                     "avg": avg, "tiles": sorted(tiles)})
    total = sum(s["score"] for s in secs)
    focus = sorted(all_skills, key=lambda s: (-s["lost"], s["pct"]))[:3]
    focus = [f for f in focus if f["lost"] > 0]
    strengths = [s for s in sorted(all_skills, key=lambda s: (-s["pct"], -s["total"])) if s["pct"] >= 70 and s["total"] >= 3][:3]
    return {
        "name": rec.get("student_name") or f"Student {rec['student_id']}",
        "first": (rec.get("student_name") or "Your child").split()[0],
        "sid": rec["student_id"], "paper": key.get("title", rec["paper"]),
        "date": dt.date.today().strftime("%-d %B %Y"),
        "sections": secs, "total": total, "max": 100 * len(secs),
        "correct": correct, "wrong": wrong, "blank": blank,
        "focus": focus, "strengths": strengths,
        "best": max(secs, key=lambda s: s["score"]), "worst": min(secs, key=lambda s: s["score"]),
    }


# ---------------- drawing helpers ----------------

def initials():
    words = [w for w in S.CENTRE_NAME.split() if w[0].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "C"


def logo(c, x, y, size, on_dark=True):
    if S.LOGO_FILE and (BASE / S.LOGO_FILE).exists():
        c.drawImage(ImageReader(str(BASE / S.LOGO_FILE)), x, y, size, size,
                    preserveAspectRatio=True, mask="auto")
        return
    c.setFillColor(white if on_dark else BRAND)
    c.roundRect(x, y, size, size, size * 0.24, stroke=0, fill=1)
    c.setFillColor(BRAND if on_dark else white)
    c.setFont("Helvetica-Bold", size * 0.42)
    c.drawCentredString(x + size / 2, y + size * 0.33, initials())
    c.setFillColor(HexColor("#F2A93B"))       # small accent dot
    c.circle(x + size * 0.80, y + size * 0.80, size * 0.08, stroke=0, fill=1)


def donut(c, cx, cy, r, width, frac, color, track=LINE):
    c.setLineWidth(width)
    c.setStrokeColor(track)
    c.circle(cx, cy, r, stroke=1, fill=0)
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        c.setStrokeColor(color)
        c.setLineCap(1)
        c.arc(cx - r, cy - r, cx + r, cy + r, startAng=90, extent=-360 * frac)
        c.setLineCap(0)


def card(c, x, y, w, h, fill=white, stroke=LINE, r=10):
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, r, stroke=1 if stroke else 0, fill=1)


def pill(c, x, y, text, fg, bg, size=7.5, right=False):
    c.setFont("Helvetica-Bold", size)
    tw = c.stringWidth(text, "Helvetica-Bold", size) + 12
    if right:
        x -= tw
    c.setFillColor(bg)
    c.roundRect(x, y, tw, size + 7, (size + 7) / 2, stroke=0, fill=1)
    c.setFillColor(fg)
    c.drawString(x + 6, y + 4, text)
    return tw


def bar(c, x, y, w, h, frac, color, track=SOFT):
    c.setFillColor(track)
    c.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        c.setFillColor(color)
        c.roundRect(x, y, max(h, w * frac), h, h / 2, stroke=0, fill=1)


def text(c, s, x, y, font="Helvetica", size=9, color=INK, align="l"):
    c.setFont(font, size)
    c.setFillColor(color)
    {"l": c.drawString, "c": c.drawCentredString, "r": c.drawRightString}[align](x, y, s)


def para(c, s, x, y, width, font="Helvetica", size=8.5, color=INK, lead=None):
    lead = lead or size * 1.35
    c.setFont(font, size)
    c.setFillColor(color)
    for line in simpleSplit(s, font, size, width):
        c.drawString(x, y, line)
        y -= lead
    return y


def heading(c, s, y, sub=None):
    c.setFillColor(BRAND)
    c.roundRect(M, y - 1, 4, 14, 2, stroke=0, fill=1)
    text(c, s, M + 12, y + 1, "Helvetica-Bold", 13)
    if sub:
        text(c, sub, W - M, y + 2, size=8, color=MUTED, align="r")


def fmt(x):
    return f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def header_band(c, d, small=False):
    h = 58 if small else 92
    c.setFillColor(BRAND)
    c.rect(0, H - h, W, h, stroke=0, fill=1)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFillAlpha(0.07)
    c.circle(W - 60, H - 10, 90, stroke=0, fill=1)
    c.circle(W - 150, H - h - 10, 50, stroke=0, fill=1)
    c.setFillAlpha(1)
    ls = 30 if small else 46
    logo(c, M, H - h / 2 - ls / 2, ls)
    tx = M + ls + 12
    text(c, S.CENTRE_NAME, tx, H - h / 2 + (2 if small else 5), "Helvetica-Bold", 13 if small else 16, white)
    text(c, S.CENTRE_TAGLINE, tx, H - h / 2 - (10 if small else 12), size=8.5, color=HexColor("#D9DBF7"))
    text(c, "MOCK TEST REPORT", W - M, H - h / 2 + (2 if small else 6), "Helvetica-Bold", 9, white, "r")
    text(c, f"{d['paper']}  ·  {d['date']}", W - M, H - h / 2 - (10 if small else 9), size=8.5,
         color=HexColor("#D9DBF7"), align="r")
    return H - h


def footer(c, n):
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(M, 34, W - M, 34)
    text(c, f"{S.CENTRE_NAME}  ·  Writing is assessed and reported separately.", M, 22, size=7.5, color=MUTED)
    text(c, f"Page {n} of 2", W - M, 22, size=7.5, color=MUTED, align="r")


# ---------------- PDF ----------------

def page_one(c, d):
    top = header_band(c, d)

    # Student line
    y = top - 38
    text(c, d["name"], M, y, "Helvetica-Bold", 22)
    text(c, f"Student ID {d['sid']}", M, y - 16, size=9, color=MUTED)

    # ---- Hero card: overall donut + quick facts
    hy, hh = y - 196, 166
    card(c, M, hy, W - 2 * M, hh, fill=SOFT, stroke=None, r=14)
    pct = d["total"] / d["max"]
    lab, col, bg, *_ = level(100 * pct)
    cx, cy = M + 92, hy + hh / 2
    donut(c, cx, cy, 56, 15, pct, col, track=HexColor("#E3E5EE"))
    text(c, fmt(d["total"]), cx, cy + 2, "Helvetica-Bold", 26, align="c")
    text(c, f"out of {d['max']}", cx, cy - 14, size=8.5, color=MUTED, align="c")
    text(c, "OVERALL", cx, cy - 76, "Helvetica-Bold", 7.5, MUTED, "c")

    # quick fact tiles
    fx = M + 182
    facts = [("Correct", str(d["correct"]), GOOD, GOOD_BG),
             ("Incorrect", str(d["wrong"]), LOW, LOW_BG),
             ("Left blank", str(d["blank"]), MUTED, HexColor("#ECEEF3"))]
    tw = (W - M - 18 - fx - 2 * 10) / 3
    for i, (lbl, val, fg, bgc) in enumerate(facts):
        x = fx + i * (tw + 10)
        card(c, x, hy + 86, tw, 62, fill=white, stroke=None, r=10)
        c.setFillColor(bgc)
        c.circle(x + 20, hy + 118, 9, stroke=0, fill=1)
        c.setFillColor(fg)
        c.circle(x + 20, hy + 118, 4, stroke=0, fill=1)
        text(c, val, x + 36, hy + 112, "Helvetica-Bold", 20)
        text(c, lbl, x + 12, hy + 95, size=8, color=MUTED)
    # best / focus
    bw = (W - M - 18 - fx - 10) / 2
    for i, (lbl, s, colr, bgc) in enumerate([("Strongest section", d["best"], GOOD, GOOD_BG),
                                               ("Biggest opportunity", d["worst"], LOW, LOW_BG)]):
        x = fx + i * (bw + 10)
        card(c, x, hy + 16, bw, 60, fill=white, stroke=None, r=10)
        c.setFillColor(colr)
        c.roundRect(x, hy + 16, 4, 60, 2, stroke=0, fill=1)
        text(c, lbl.upper(), x + 14, hy + 60, "Helvetica-Bold", 7, MUTED)
        text(c, s["name"], x + 14, hy + 43, "Helvetica-Bold", 10.5)
        text(c, f"{fmt(s['score'])} / 100", x + 14, hy + 27, size=9, color=colr)

    # ---- Section cards
    y = hy - 30
    heading(c, "Section scores", y, "each section is out of 100")
    sy, sh = y - 128, 116
    cw = (W - 2 * M - 2 * 12) / 3
    for i, s in enumerate(d["sections"]):
        x = M + i * (cw + 12)
        card(c, x, sy, cw, sh)
        lab, col, bg, *_ = level(s["score"])
        dx, dy = x + 44, sy + sh / 2 + 4
        donut(c, dx, dy, 28, 8, s["score"] / 100, col)
        text(c, fmt(s["score"]), dx, dy - 5, "Helvetica-Bold", 15, align="c")
        tx = x + 84
        text(c, s["short"], tx, sy + sh - 26, "Helvetica-Bold", 11)
        text(c, f"{s['correct']} of {s['total']} correct", tx, sy + sh - 40, size=8, color=MUTED)
        pill(c, tx, sy + sh - 62, lab, col, bg)
        if s["avg"] is not None:
            text(c, f"Group average {fmt(s['avg'])}", tx, sy + sh - 78, size=7.5, color=MUTED)
        text(c, s["name"], x + cw / 2, sy + 12, size=7, color=MUTED, align="c")

    # ---- Skill bars, one column per section
    y = sy - 30
    heading(c, "Skill by skill", y, "% of questions correct in each skill")
    ly = y - 22
    for j, (lbl, colr) in enumerate([("70%+ strong", GOOD), ("45-69% developing", MID), ("under 45% focus", LOW)]):
        lx = M + 12 + j * 105
        c.setFillColor(colr)
        c.circle(lx + 4, ly + 3, 3.5, stroke=0, fill=1)
        text(c, lbl, lx + 12, ly, size=7.5, color=MUTED)
    top_y = ly - 14
    card_h = top_y - 46
    for i, s in enumerate(d["sections"]):
        x = M + i * (cw + 12)
        card(c, x, 46, cw, card_h)
        text(c, s["short"].upper(), x + 12, top_y - 18, "Helvetica-Bold", 8, BRAND)
        rows = s["skills"]
        step = min(42, (card_h - 34) / max(1, len(rows)))
        ry = top_y - 40
        for sk in rows:
            lab, col, *_ = level(sk["pct"])
            name = sk["skill"]
            while c.stringWidth(name, "Helvetica", 8) > cw - 60:
                name = name[:-2]
            if name != sk["skill"]:
                name = name.rstrip() + "…"
            text(c, name, x + 12, ry, size=8)
            text(c, f"{sk['right']}/{sk['total']}", x + cw - 12, ry, "Helvetica-Bold", 8, col, "r")
            bar(c, x + 12, ry - 12, cw - 24, 6, sk["pct"] / 100, col)
            ry -= step
    footer(c, 1)


def page_two(c, d):
    top = header_band(c, d, small=True)
    y = top - 34
    text(c, f"What {d['first']} should work on next", M, y, "Helvetica-Bold", 17)
    text(c, "The three skills that cost the most marks in this test, and how to practise them.",
         M, y - 15, size=9, color=MUTED)

    # ---- focus cards
    y -= 32
    fh = 64
    focus = d["focus"] or []
    for i, f in enumerate(focus):
        fy = y - fh - i * (fh + 8)
        card(c, M, fy, W - 2 * M, fh)
        lab, col, bg, *_ = level(f["pct"])
        c.setFillColor(bg)
        c.circle(M + 30, fy + fh / 2, 16, stroke=0, fill=1)
        text(c, str(i + 1), M + 30, fy + fh / 2 - 6, "Helvetica-Bold", 16, col, "c")
        tx = M + 60
        sec = next(s["short"] for s in d["sections"] if s["code"] == f["section"])
        text(c, f["skill"], tx, fy + fh - 22, "Helvetica-Bold", 11.5)
        w1 = c.stringWidth(f["skill"], "Helvetica-Bold", 11.5)
        pill(c, tx + w1 + 8, fy + fh - 25, sec, BRAND, HexColor("#ECEBFB"), size=7)
        para(c, "Practise: " + TIPS.get(f["skill"], SECTION_TIPS[f["section"]]),
             tx, fy + fh - 37, W - 2 * M - 60 - 130, size=8.5, color=HexColor("#3B4050"))
        # mini meter
        mx = W - M - 120
        text(c, f"{f['lost']} mark{'s' if f['lost'] != 1 else ''} lost", mx, fy + fh - 22, "Helvetica-Bold", 9, col)
        text(c, f"{f['right']} of {f['total']} correct", mx, fy + fh - 36, size=8, color=MUTED)
        bar(c, mx, fy + 14, 104, 7, f["pct"] / 100, col)
    if not focus:
        para(c, "No marks lost. Keep practising timed papers to stay sharp.", M, y - 20, 400)
    y = y - max(1, len(focus)) * (fh + 8) - 8

    # ---- strengths strip
    if d["strengths"]:
        card(c, M, y - 34, W - 2 * M, 34, fill=GOOD_BG, stroke=None, r=10)
        text(c, "STRENGTHS", M + 14, y - 21, "Helvetica-Bold", 7.5, GOOD)
        x = M + 78
        for s in d["strengths"]:
            x += pill(c, x, y - 25, f"{s['skill']}  {s['right']}/{s['total']}", HexColor("#17744B"), white, 7.5) + 6
        y -= 62
    else:
        y -= 12

    # ---- answer map
    heading(c, "Answer map", y, "every question at a glance")
    y -= 22
    lx = M + 12
    for lbl, colr in [("Correct", GOOD), ("Incorrect", LOW), ("Left blank", BLANK)]:
        c.setFillColor(colr)
        c.roundRect(lx, y - 1, 9, 9, 2, stroke=0, fill=1)
        text(c, lbl, lx + 13, y, size=7.5, color=MUTED)
        lx += 70
    y -= 12
    tile, gap = 12.6, 1.9
    for s in d["sections"]:
        y -= 14
        text(c, s["short"], M, y, "Helvetica-Bold", 8.5)
        text(c, f"{s['correct']}/{s['total']}", W - M, y, "Helvetica-Bold", 8.5, MUTED, "r")
        y -= tile + 5
        for k, (qn, ok, given) in enumerate(s["tiles"]):
            x = M + k * (tile + gap)
            colr = GOOD if ok else (BLANK if given is None else LOW)
            c.setFillColor(colr)
            c.roundRect(x, y, tile, tile, 2.5, stroke=0, fill=1)
            text(c, str(qn), x + tile / 2, y + 4, "Helvetica-Bold", 5.5, white, "c")
    y -= 28

    # ---- two-week plan
    heading(c, "Two-week plan", y)
    y -= 14
    names = [f["skill"] for f in focus] or ["mixed practice"]
    week1 = [f"15 minutes a day on {names[0]}" + (f" and {names[1]}" if len(names) > 1 else ""),
             "Redo every incorrect question from this test, without time pressure",
             "Explain one solution out loud to a parent each day"]
    week2 = [f"Add {names[2]} practice" if len(names) > 2 else "Mixed practice across all three sections",
             "One timed section (35 questions) at the end of the week",
             "Leave no blanks: make a best guess when time runs short"]
    pw = (W - 2 * M - 12) / 2
    ph = 104
    for i, (title, items) in enumerate([("WEEK 1  ·  Fix the gaps", week1), ("WEEK 2  ·  Build speed", week2)]):
        x = M + i * (pw + 12)
        card(c, x, y - ph, pw, ph, fill=SOFT, stroke=None)
        text(c, title, x + 14, y - 20, "Helvetica-Bold", 8.5, BRAND)
        iy = y - 40
        for it in items:
            c.setStrokeColor(HexColor("#B9BDCB"))
            c.setLineWidth(0.9)
            c.roundRect(x + 14, iy - 2, 8, 8, 1.5, stroke=1, fill=0)
            iy = para(c, it, x + 30, iy, pw - 44, size=8.2) - 5
    y -= ph + 26

    # ---- parents can help
    heading(c, "How you can help at home", y)
    y -= 16
    tips = [("Little and often", "Short daily practice beats one long session at the weekend."),
            ("Ask, don't tell", "Ask 'how did you get that?' before checking the answer."),
            ("Praise the effort", "Notice careful working and checking, not just the score.")]
    tw = (W - 2 * M - 24) / 3
    for i, (t, s) in enumerate(tips):
        x = M + i * (tw + 12)
        card(c, x, y - 54, tw, 54)
        c.setFillColor(BRAND)
        c.circle(x + 18, y - 18, 6, stroke=0, fill=1)
        text(c, str(i + 1), x + 18, y - 21, "Helvetica-Bold", 7, white, "c")
        text(c, t, x + 30, y - 21, "Helvetica-Bold", 9)
        para(c, s, x + 12, y - 36, tw - 24, size=7.8, color=HexColor("#4B5060"))
    c._lowest = y - 54
    footer(c, 2)


def build_pdf(rec, key, peers=None):
    d = summarise(rec, key, peers)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{d['name']} - {d['paper']} report")
    c.setAuthor(S.CENTRE_NAME)
    page_one(c, d)
    c.showPage()
    page_two(c, d)
    c.save()
    return buf.getvalue(), d


# ---------------- email ----------------

def build_email_html(d):
    e = html.escape
    brand = S.BRAND_COLOUR
    if S.LOGO_FILE and (BASE / S.LOGO_FILE).exists():
        logo_html = '<img src="cid:logo" width="44" height="44" style="display:block;border-radius:10px">'
    else:
        logo_html = (f'<div style="width:44px;height:44px;border-radius:11px;background:#fff;color:{brand};'
                     f'font:bold 19px/44px Helvetica,Arial,sans-serif;text-align:center">{e(initials())}</div>')
    pct = round(100 * d["total"] / d["max"])
    lab, *_, fg, bg = level(pct)
    rows = ""
    for s in d["sections"]:
        sl, *_, sfg, sbg = level(s["score"])
        w = max(2, round(s["score"]))
        rows += f"""
<tr><td style="padding:14px 0 4px;font:600 14px Helvetica,Arial,sans-serif;color:#1F2330">{e(s['name'])}</td>
<td align="right" style="padding:14px 0 4px;font:700 14px Helvetica,Arial,sans-serif;color:{sfg}">{fmt(s['score'])}<span style="color:#9AA0AE;font-weight:400">/100</span></td></tr>
<tr><td colspan="2"><table width="100%" cellpadding="0" cellspacing="0" style="background:#EEF0F5;border-radius:6px"><tr>
<td width="{w}%" style="background:{sfg};height:10px;border-radius:6px;font-size:0">&nbsp;</td><td style="font-size:0">&nbsp;</td></tr></table></td></tr>"""
    focus = ""
    for i, f in enumerate(d["focus"]):
        focus += f"""<tr><td valign="top" style="padding:8px 10px 8px 0;width:28px">
<div style="width:26px;height:26px;border-radius:13px;background:#FCE6E4;color:#D93A31;font:700 13px/26px Helvetica,Arial,sans-serif;text-align:center">{i + 1}</div></td>
<td style="padding:8px 0;font:14px/1.45 Helvetica,Arial,sans-serif;color:#3B4050"><b style="color:#1F2330">{e(f['skill'])}</b>
<span style="color:#9AA0AE">· {f['right']}/{f['total']} correct</span><br>{e(TIPS.get(f['skill'], SECTION_TIPS[f['section']]))}</td></tr>"""
    return f"""<!doctype html><html><body style="margin:0;background:#F2F3F7">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F2F3F7;padding:24px 0"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#fff;border-radius:16px;overflow:hidden">
<tr><td style="background:{brand};padding:22px 26px">
 <table cellpadding="0" cellspacing="0"><tr><td>{logo_html}</td>
 <td style="padding-left:12px;font:700 17px Helvetica,Arial,sans-serif;color:#fff">{e(S.CENTRE_NAME)}<br>
 <span style="font:400 12px Helvetica,Arial,sans-serif;color:#D9DBF7">{e(S.CENTRE_TAGLINE)}</span></td></tr></table></td></tr>
<tr><td style="padding:26px 26px 6px;font:15px/1.5 Helvetica,Arial,sans-serif;color:#3B4050">
 Dear Parent,<br><br>{e(d['first'])}'s results from <b>{e(d['paper'])}</b> are ready. Here is a quick summary.
 The full report with charts, an answer map and a two-week plan is attached as a PDF.</td></tr>
<tr><td style="padding:14px 26px">
 <table width="100%" cellpadding="0" cellspacing="0" style="background:#F5F6FA;border-radius:14px"><tr>
 <td style="padding:20px 22px;font:12px Helvetica,Arial,sans-serif;color:#6B7280">OVERALL SCORE<br>
 <span style="font:700 40px Helvetica,Arial,sans-serif;color:#1F2330">{fmt(d['total'])}</span>
 <span style="font:16px Helvetica,Arial,sans-serif;color:#9AA0AE">/ {d['max']}</span></td>
 <td align="right" style="padding:20px 22px"><span style="background:{bg};color:{fg};border-radius:20px;padding:6px 12px;font:700 12px Helvetica,Arial,sans-serif">{lab} · {pct}%</span></td>
 </tr></table></td></tr>
<tr><td style="padding:6px 26px 4px"><table width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>
<tr><td style="padding:22px 26px 4px;font:700 16px Helvetica,Arial,sans-serif;color:#1F2330">Top things to work on</td></tr>
<tr><td style="padding:0 26px"><table width="100%" cellpadding="0" cellspacing="0">{focus}</table></td></tr>
<tr><td style="padding:18px 26px 26px;font:14px/1.5 Helvetica,Arial,sans-serif;color:#3B4050">
 Please reply to this email if you'd like to talk through the results.<br><br>Warm regards,<br><b>{e(S.CENTRE_NAME)}</b></td></tr>
<tr><td style="background:#F5F6FA;padding:12px 26px;font:11px Helvetica,Arial,sans-serif;color:#9AA0AE">
 Writing is assessed and reported separately. Student ID {e(d['sid'])}.</td></tr>
</table></td></tr></table></body></html>"""


def build_message(pdf, d, to_addr):
    msg = EmailMessage()
    msg["Subject"] = f"{d['first']}'s mock test report: {fmt(d['total'])}/{d['max']} · {d['paper']}"
    msg["From"] = f"{S.CENTRE_NAME} <{S.GMAIL_ADDRESS}>"
    msg["To"] = to_addr
    plain = (f"Dear Parent,\n\n{d['first']}'s results from {d['paper']}: {fmt(d['total'])} out of {d['max']}.\n"
             + "".join(f"- {s['name']}: {fmt(s['score'])}/100\n" for s in d["sections"])
             + "\nThe full report is attached as a PDF.\n\n" + S.CENTRE_NAME)
    msg.set_content(plain)
    msg.add_alternative(build_email_html(d), subtype="html")
    if S.LOGO_FILE and (BASE / S.LOGO_FILE).exists():
        p = BASE / S.LOGO_FILE
        sub = "png" if p.suffix.lower() == ".png" else "jpeg"
        msg.get_payload()[1].add_related(p.read_bytes(), "image", sub, cid="<logo>")
    safe = "".join(ch for ch in d["name"] if ch.isalnum() or ch in " -").strip().replace(" ", "_")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"{safe}_report.pdf")
    return msg


class EmailSetupError(Exception):
    pass


def send_report(pdf, d, parent_email):
    to_addr = S.SEND_ALL_TO or parent_email
    if not to_addr:
        raise EmailSetupError("No parent email for this student. Add it to students.csv.")
    if not S.GMAIL_APP_PASSWORD.strip():
        raise EmailSetupError("Gmail app password is not set. Open settings.py and paste it into GMAIL_APP_PASSWORD.")
    msg = build_message(pdf, d, to_addr)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(S.GMAIL_ADDRESS, S.GMAIL_APP_PASSWORD.replace(" ", ""))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise EmailSetupError("Gmail rejected the login. Check GMAIL_ADDRESS and the app password in settings.py.")
    return to_addr
