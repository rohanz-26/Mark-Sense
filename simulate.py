"""
Test the reader without a printer: fill the sheet digitally, turn it into a
fake phone photo (tilt, perspective, shadow, blur, noise, JPEG), then read it
back and compare with what was filled.

    python simulate.py            # runs 20 random sheets, prints accuracy
    python simulate.py --save 3   # also saves 3 example photos + overlays in sim_out/
"""
import argparse
import os
import random
import subprocess

import cv2
import numpy as np

import layout as L
from omr_reader import read_sheet, SheetError

DPI = 200
HARD = False
PX = DPI / 25.4


def render_blank(pdf="omr_sheet.pdf", out="sim_blank.png"):
    if not os.path.exists(out):
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-singlefile", pdf,
                        out[:-4]], check=True)
    return cv2.imread(out)


def pencil(img, x_mm, y_mm, rng, shade=None, cover=1.0):
    shade = shade if shade is not None else rng.randint(35, 95)
    cx = (x_mm + rng.uniform(-0.35, 0.35)) * PX
    cy = (y_mm + rng.uniform(-0.35, 0.35)) * PX
    r = L.BUBBLE_R * PX * rng.uniform(0.85, 1.08) * cover
    layer = np.zeros(img.shape[:2], np.uint8)
    cv2.ellipse(layer, (int(cx), int(cy)), (int(r), int(r * rng.uniform(0.85, 1.0))),
                rng.uniform(0, 180), 0, 360, 255, -1)
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 18, img.shape[:2])
    m = layer > 0
    for ch in range(3):
        chan = img[:, :, ch].astype(np.float32)
        val = np.clip(shade + noise[m], 0, 255)
        chan[m] = np.minimum(chan[m], val)
        img[:, :, ch] = chan.astype(np.uint8)


def fill_sheet(img, rng):
    """Return (filled image, truth dict, student id)."""
    truth = {}
    sid = "".join(str(rng.randint(0, 9)) for _ in range(L.ID_DIGITS))
    for d, v, x, y in L.id_bubbles():
        if str(v) == sid[d - 1]:
            pencil(img, x, y, rng)
    rows = {}
    for sec, q, opt, x, y in L.question_bubbles():
        rows.setdefault((sec, q), []).append((opt, x, y))
    for (sec, q), opts in rows.items():
        p = rng.random()
        if p < 0.05:                       # left blank
            truth[(sec, q)] = ("blank", None)
        elif p < 0.08:                     # two answers
            a, b = rng.sample(opts, 2)
            pencil(img, a[1], a[2], rng)
            pencil(img, b[1], b[2], rng)
            truth[(sec, q)] = ("multiple", None)
        elif p < 0.11:                     # answer + erased leftover elsewhere
            a, b = rng.sample(opts, 2)
            pencil(img, a[1], a[2], rng)
            pencil(img, b[1], b[2], rng, shade=rng.randint(205, 225))
            truth[(sec, q)] = ("answer", a[0])
        elif HARD and p < 0.17:            # light pencil or half-filled bubble
            a = rng.choice(opts)
            if rng.random() < 0.5:
                pencil(img, a[1], a[2], rng, shade=rng.randint(120, 175))
            else:
                pencil(img, a[1], a[2], rng, cover=rng.uniform(0.5, 0.7))
            truth[(sec, q)] = ("answer", a[0])
        else:                              # normal single answer
            a = rng.choice(opts)
            pencil(img, a[1], a[2], rng)
            truth[(sec, q)] = ("answer", a[0])
    return img, truth, sid


def fake_phone_photo(page, rng):
    h, w = page.shape[:2]
    # Put the page on a darker table and warp it with perspective + rotation.
    W, H = int(w * 1.35), int(h * 1.3)
    table = np.full((H, W, 3), (rng.randint(60, 120), rng.randint(70, 130),
                                rng.randint(80, 150)), np.uint8)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    j = lambda s: rng.uniform(-s, s)
    ox, oy = (W - w) / 2, (H - h) / 2
    dst = np.float32([[ox + j(110), oy + j(110)], [ox + w + j(110), oy + j(110)],
                      [ox + w + j(110), oy + h + j(110)], [ox + j(110), oy + h + j(110)]])
    ang = rng.uniform(-6, 6)
    R = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
    dst = cv2.transform(dst[None], R)[0].astype(np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(page, M, (W, H), dst=table.copy(),
                                borderMode=cv2.BORDER_TRANSPARENT)
    # Uneven light: a soft shadow across part of the page.
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    gx, gy = rng.uniform(-1, 1), rng.uniform(-1, 1)
    grad = (xx / W - 0.5) * gx + (yy / H - 0.5) * gy
    light = 1.0 - rng.uniform(0.15, 0.4) * (grad - grad.min()) / (np.ptp(grad) + 1e-6)
    photo = (photo.astype(np.float32) * light[..., None])
    # Warm colour cast, sensor noise, slight blur.
    photo *= np.float32([rng.uniform(0.85, 1.0), rng.uniform(0.92, 1.0), 1.0])
    photo += np.random.default_rng(rng.randint(0, 10**6)).normal(0, 4, photo.shape)
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    k = rng.choice([1, 3, 3, 5])
    if k > 1:
        photo = cv2.GaussianBlur(photo, (k, k), 0)
    # Downscale to a typical phone resolution and JPEG compress.
    target = rng.choice([3000, 3500, 4000])
    s = target / max(photo.shape[:2])
    photo = cv2.resize(photo, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(70, 92)])
    return jpg.tobytes()


def evaluate(result, truth, sid):
    stats = {"correct": 0, "silent_wrong": 0, "flagged": 0, "unneeded_flags": 0,
             "multiple_caught": 0, "multiple_total": 0, "blank_ok": 0, "blank_total": 0}
    flagged = {(f["section"], f["question"]): f["issue"] for f in result["flags"]}
    blanks = set(tuple(b) for b in result["blank_questions"])
    for (sec, q), (kind, ans) in truth.items():
        got = result["answers"][sec][q]
        if kind == "answer":
            if got == ans and (sec, q) not in flagged:
                stats["correct"] += 1
            elif got == ans:
                stats["correct"] += 1
                stats["unneeded_flags"] += 1
            elif (sec, q) in flagged:
                stats["flagged"] += 1
            else:
                stats["silent_wrong"] += 1
        elif kind == "multiple":
            stats["multiple_total"] += 1
            if (sec, q) in flagged and flagged[(sec, q)].startswith("multiple"):
                stats["multiple_caught"] += 1
            elif got is not None:
                stats["silent_wrong"] += 1
        else:
            stats["blank_total"] += 1
            if (sec, q) in blanks:
                stats["blank_ok"] += 1
            elif got is not None:
                stats["silent_wrong"] += 1
    stats["id_ok"] = int(result["student_id"] == sid)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--save", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hard", action="store_true",
                    help="also add light-pencil and half-filled answers")
    args = ap.parse_args()
    global HARD
    HARD = args.hard
    rng = random.Random(args.seed)
    blank = render_blank()
    os.makedirs("sim_out", exist_ok=True)
    total = {}
    retakes = 0
    for i in range(args.n):
        page, truth, sid = fill_sheet(blank.copy(), rng)
        jpg = fake_phone_photo(page, rng)
        try:
            res = read_sheet(jpg, debug=i < args.save)
        except SheetError as e:
            retakes += 1
            print(f"sheet {i}: RETAKE ({e})")
            continue
        if i < args.save:
            open(f"sim_out/photo_{i}.jpg", "wb").write(jpg)
            cv2.imwrite(f"sim_out/overlay_{i}.jpg", res.pop("_overlay"),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
        st = evaluate(res, truth, sid)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
        print(f"sheet {i}: id {'ok' if st['id_ok'] else 'WRONG'}  "
              f"silent_wrong={st['silent_wrong']}  flagged={st['flagged']}  "
              f"unneeded_flags={st['unneeded_flags']}  warnings={res['warnings']}")
    print("\nTOTAL", total, "retakes:", retakes)


if __name__ == "__main__":
    main()
