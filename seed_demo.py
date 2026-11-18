"""
Fill the platform with realistic demo data for a prototype video.

    python seed_demo.py

Creates 4 mock tests (3 marked, 1 upcoming), 24 students in 3 batches, and
about 70 marked answer sheets. Every sheet is a fake phone photo that goes
through the real reader, so the overlays and results are genuine.
Student 120001 (Aarav Mehta) is left out of Mock Test 3 on purpose: upload
your real sheet into Mock Test 3 during the video.

WARNING: this deletes everything in data/results first.
"""
import csv
import datetime as dt
import json
import random
import shutil
from pathlib import Path

import cv2

import layout as L
import simulate as SIM
from omr_reader import read_sheet

BASE = Path(__file__).parent
KEYS, DATA = BASE / "answer_keys", BASE / "data" / "results"

SKILLS = {
    "RC": ["Main idea", "Author's stance", "Inference", "Vocabulary in context",
           "Comparing responses", "Poetry and figurative language"],
    "QR": ["Fractions and ratios", "Working backwards", "Invented rules", "Cycles and remainders",
           "Data and graphs", "Reasoning about counts", "Spatial measurement"],
    "AR": ["Rotation", "Reflection", "Grid completion", "Texture or fill as a value",
           "Count up or down", "Position permutation"],
}
TESTS = [("mock_test_1", "Mock Test 1", "2026-09-13"),
         ("mock_test_2", "Mock Test 2", "2026-10-18"),
         ("mock_test_3", "Mock Test 3", "2026-11-15"),
         ("mock_test_4", "Mock Test 4", "2026-11-29")]

STUDENTS = [  # id, name, batch
    ("120001", "Aarav Mehta", "Year 6 · Saturday"), ("120002", "Olivia Chen", "Year 6 · Saturday"),
    ("120003", "Isla Thompson", "Year 6 · Saturday"), ("120004", "Vihaan Patel", "Year 6 · Saturday"),
    ("120005", "Mia Nguyen", "Year 6 · Saturday"), ("120006", "Liam O'Connor", "Year 6 · Saturday"),
    ("120007", "Anaya Sharma", "Year 6 · Saturday"), ("120008", "Noah Williams", "Year 6 · Saturday"),
    ("120009", "Zara Ali", "Year 6 · Sunday"), ("120010", "Ethan Park", "Year 6 · Sunday"),
    ("120011", "Saanvi Iyer", "Year 6 · Sunday"), ("120012", "Jack Robinson", "Year 6 · Sunday"),
    ("120013", "Chloe Martin", "Year 6 · Sunday"), ("120014", "Arjun Reddy", "Year 6 · Sunday"),
    ("120015", "Ruby Wilson", "Year 6 · Sunday"), ("120016", "Kabir Singh", "Year 6 · Sunday"),
    ("120017", "Ava Kowalski", "Year 5 · Wednesday"), ("120018", "Rohan Das", "Year 5 · Wednesday"),
    ("120019", "Sophie Taylor", "Year 5 · Wednesday"), ("120020", "Leo Fernandes", "Year 5 · Wednesday"),
    ("120021", "Diya Kapoor", "Year 5 · Wednesday"), ("120022", "Oscar Brown", "Year 5 · Wednesday"),
    ("120023", "Hana Suzuki", "Year 5 · Wednesday"), ("120024", "Ishaan Joshi", "Year 5 · Wednesday"),
]

# Aarav's real sheet (read from the photo). Mock Test 3's key is built around it
# so his live upload gives a believable score (~76%).
AARAV_SHEET = {
    "RC": "ACBDEEABCDBCBBBBCDDDCDCCCDCBBCBCBCC",
    "QR": "CBCABCDDECBBCBBCBCDCBBCBCDBCCCCDCBC",
    "AR": "ABCDBCDBBBCCDBCBCBCBBCBCBBCBCBCBCAD",
}


def make_key(rng, paper, title, date, base=None, flip=0):
    secs = {}
    for sec in ("RC", "QR", "AR"):
        qs = {}
        flips = set(rng.sample(range(1, 36), flip)) if base else set()
        for q in range(1, 36):
            if base:
                ans = base[sec][q - 1]
                if q in flips:
                    ans = rng.choice([o for o in L.OPTIONS if o != ans])
            else:
                ans = rng.choice(L.OPTIONS)
            qs[str(q)] = {"answer": ans, "skill": SKILLS[sec][((q - 1) // 2 + rng.randint(0, 2)) % len(SKILLS[sec])]}
        secs[sec] = {"questions": qs}
    key = {"paper": paper, "title": title, "date": date, "sections": secs}
    (KEYS / f"{paper}.json").write_text(json.dumps(key, indent=1))
    return key


def score(answers, key):
    import app
    return app.score(answers, key)


def main():
    rng = random.Random(7)
    for old in KEYS.glob("*.json"):
        old.unlink()
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True)

    with (BASE / "students.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["student_id", "name", "parent_email", "batch"])
        for sid, name, batch in STUDENTS:
            first = name.split()[0].lower().replace("'", "")
            w.writerow([sid, name, f"parent.{first}@example.com", batch])

    keys = []
    for i, (paper, title, date) in enumerate(TESTS):
        if paper == "mock_test_3":
            keys.append(make_key(rng, paper, title, date, base=AARAV_SHEET, flip=8))
        else:
            keys.append(make_key(rng, paper, title, date))

    # One "trap" question per test that most students get wrong the same way.
    traps = {k["paper"]: (rng.choice(["RC", "QR", "AR"]), str(rng.randint(5, 33))) for k in keys}

    # Student profiles: ability, section strengths, skill weaknesses.
    prof = {}
    for sid, name, batch in STUDENTS:
        base = rng.gauss(0.60, 0.12) - (0.05 if batch.startswith("Year 5") else 0)
        prof[sid] = {
            "base": min(0.9, max(0.3, base)),
            "sec": {s: rng.gauss(0, 0.07) for s in SKILLS},
            "skill": {sk: rng.gauss(0, 0.12) for s in SKILLS.values() for sk in s},
            "growth": rng.uniform(0.0, 0.06),
        }
    prof["120001"].update(base=0.52, growth=0.06)
    prof["120006"]["growth"] = -0.07       # one student slipping, for the "needs attention" list

    qdiff = {(k["paper"], sec, str(q)): rng.gauss(0, 0.16) for k in keys for sec in SKILLS for q in range(1, 36)}
    blank = SIM.render_blank()
    n = 0
    for ti, key in enumerate(keys[:3]):
        paper = key["paper"]
        (DATA / paper).mkdir(parents=True, exist_ok=True)
        for sid, name, batch in STUDENTS:
            if paper == "mock_test_3" and sid == "120001":
                continue                    # left for the live upload in the video
            if paper == "mock_test_1" and sid in ("120022", "120015"):
                continue                    # absent
            p = prof[sid]
            img = blank.copy()
            for d, v, x, y in L.id_bubbles():
                if str(v) == sid[d - 1]:
                    SIM.pencil(img, x, y, rng)
            rows = {}
            for sec, q, opt, x, y in L.question_bubbles():
                rows.setdefault((sec, str(q)), []).append((opt, x, y))
            double = paper == "mock_test_3" and sid in ("120009", "120018")
            dq = rng.randint(3, 30)
            for (sec, q), opts in rows.items():
                kq = key["sections"][sec]["questions"][q]
                pc = (p["base"] + ti * p["growth"] + p["sec"][sec] + p["skill"][kq["skill"]]
                      + (int(q) / 35 - 0.5) * -0.18 + qdiff[(paper, sec, q)])  # later questions are harder
                r = rng.random()
                if r < 0.02 + (0.03 if int(q) > 31 and p["base"] < 0.5 else 0):
                    continue                # left blank
                if (sec, q) == traps[paper]:
                    wrong = [o for o in L.OPTIONS if o != kq["answer"]][0]
                    ans = kq["answer"] if rng.random() < 0.15 else wrong
                elif r < pc:
                    ans = kq["answer"]
                else:
                    ans = rng.choice([o for o in L.OPTIONS if o != kq["answer"]])
                o = next(o for o in opts if o[0] == ans)
                SIM.pencil(img, o[1], o[2], rng)
                if double and sec == "QR" and int(q) == dq:
                    o2 = next(o for o in opts if o[0] != ans)
                    SIM.pencil(img, o2[1], o2[2], rng)
            photo = SIM.fake_phone_photo(img, rng)
            res = read_sheet(photo, debug=True)
            overlay = res.pop("_overlay")
            answers = {sec: {str(q): a for q, a in qa.items()} for sec, qa in res["answers"].items()}
            when = dt.datetime.fromisoformat(key["date"]) + dt.timedelta(hours=14, minutes=rng.randint(0, 50))
            rec = {"paper": paper, "student_id": sid, "source_file": f"IMG_{4400 + n}.jpg",
                   "student_name": name, "read": {**res, "answers": answers},
                   "marks": score(answers, key), "status": res["status"], "review": {},
                   "scanned_at": when.isoformat(timespec="minutes")}
            if paper != "mock_test_3":
                sent = when + dt.timedelta(days=1, hours=rng.randint(1, 4))
                rec["emailed"] = {"to": f"parent.{name.split()[0].lower()}@example.com",
                                  "at": sent.strftime("%d %b %Y, %I:%M %p")}
            (DATA / paper / f"{sid}.json").write_text(json.dumps(rec, indent=1))
            small = cv2.resize(overlay, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(DATA / paper / f"{sid}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            n += 1
            print(f"{paper} {sid} {name:18s} {res['status']:12s} "
                  + " ".join(f"{s}:{m['correct']}" for s, m in rec["marks"].items()))
    print(f"\nDone: {n} sheets. Start the app and open http://localhost:8000")


if __name__ == "__main__":
    main()
