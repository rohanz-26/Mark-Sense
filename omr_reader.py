"""
Read a photo (or scan) of the custom OMR answer sheet.

    from omr_reader import read_sheet
    result = read_sheet(open("photo.jpg", "rb").read())

Steps:
 1. Find the 4 ArUco corner markers.
 2. Straighten the page (perspective warp) onto a fixed-size canvas.
 3. Even out lighting and shadows.
 4. Measure how dark each bubble is, at the positions in layout.py.
 5. Decide each answer, and flag anything blank, double-marked or unclear.

CLI:  python omr_reader.py photo.jpg [--debug out.png]
"""
import json
import sys

import cv2
import numpy as np

import layout as L

PX_PER_MM = 10                       # canonical resolution after straightening
CANVAS_W = int(L.PAGE_W * PX_PER_MM)
CANVAS_H = int(L.PAGE_H * PX_PER_MM)

# Darkness score thresholds (0 = white paper, 1 = solid black)
FILLED = 0.45        # at or above: counted as filled
UNCLEAR_LOW = 0.25   # between UNCLEAR_LOW and FILLED: flagged for review
MIN_SHARPNESS = 25   # Laplacian variance below this: photo probably blurry


class SheetError(Exception):
    """Raised when the photo cannot be read at all (ask for a retake)."""


def _decode(image_bytes):
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise SheetError("File is not a readable image.")
    return img


def _find_markers(gray):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(dictionary, params)

    # Large phone photos: detect on a downscaled copy, then scale back up.
    scale = 1.0
    h, w = gray.shape
    if max(h, w) > 2600:
        scale = 2600 / max(h, w)
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = gray
    corners, ids, _ = detector.detectMarkers(small)
    found = {}
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            if int(i) in L.MARKER_CENTERS:
                found[int(i)] = c.reshape(4, 2) / scale
    return found


def _straighten(img, markers):
    missing = [i for i in L.MARKER_CENTERS if i not in markers]
    if missing:
        names = {0: "top-left", 1: "top-right", 2: "bottom-left", 3: "bottom-right"}
        raise SheetError("Corner marker not found: " + ", ".join(names[i] for i in missing)
                         + ". Retake the photo with all four corner squares in view.")
    # Use all 16 marker corners for a robust fit.
    src, dst = [], []
    half = L.MARKER_SIZE / 2
    offsets = [(-half, -half), (half, -half), (half, half), (-half, half)]  # TL,TR,BR,BL
    for mid, (cx, cy) in L.MARKER_CENTERS.items():
        for (dx, dy), pt in zip(offsets, markers[mid]):
            src.append(pt)
            dst.append(((cx + dx) * PX_PER_MM, (cy + dy) * PX_PER_MM))
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 5.0)
    if H is None:
        raise SheetError("Could not straighten the page. Retake the photo flatter.")
    return cv2.warpPerspective(img, H, (CANVAS_W, CANVAS_H), flags=cv2.INTER_LINEAR,
                               borderValue=(255, 255, 255))


def _even_lighting(gray):
    # Estimate the paper brightness everywhere (removes marks), then divide it out.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)
    background = cv2.GaussianBlur(background, (0, 0), 15)
    norm = cv2.divide(gray, background, scale=255)
    return norm


_DISK_CACHE = {}


def _disk_offsets(radius_px):
    if radius_px not in _DISK_CACHE:
        r = radius_px
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        mask = (xx ** 2 + yy ** 2) <= r ** 2
        _DISK_CACHE[r] = (yy[mask], xx[mask])
    return _DISK_CACHE[radius_px]


def _darkness(norm, x_mm, y_mm, search_px=4):
    """Darkness of the bubble interior (0..1). Searches a few px around the
    expected spot to absorb small alignment errors; keeps the darkest."""
    r_in = int(L.BUBBLE_R * PX_PER_MM * 0.72)  # inside the printed outline
    dy, dx = _disk_offsets(r_in)
    cx, cy = int(round(x_mm * PX_PER_MM)), int(round(y_mm * PX_PER_MM))
    best = 0.0
    for oy in range(-search_px, search_px + 1, 2):
        for ox in range(-search_px, search_px + 1, 2):
            vals = norm[cy + oy + dy, cx + ox + dx]
            best = max(best, 1.0 - float(vals.mean()) / 255.0)
    return best


def _decide(scores, labels):
    """scores: list of darkness values for one row. Returns (answer, flag)."""
    filled = [lab for s, lab in zip(scores, labels) if s >= FILLED]
    unclear = [lab for s, lab in zip(scores, labels) if UNCLEAR_LOW <= s < FILLED]
    if len(filled) > 1:
        return None, "multiple: " + ",".join(filled)
    if len(filled) == 1 and not unclear:
        return filled[0], None
    if len(filled) == 1 and unclear:
        # A clear mark plus a faint one (often an eraser leftover).
        return filled[0], "check: faint mark on " + ",".join(unclear)
    if unclear:
        return None, "unclear: " + ",".join(unclear)
    return None, "blank"


def read_sheet(image_bytes, debug=False):
    img = _decode(image_bytes)
    gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    warnings = []

    markers = _find_markers(gray_full)
    page = _straighten(img, markers)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
    if sharp < MIN_SHARPNESS:
        warnings.append(f"Photo may be blurry (sharpness {sharp:.0f}). Consider a retake.")
    norm = _even_lighting(gray)

    overlay = page.copy() if debug else None

    def mark(x, y, color):
        if overlay is not None:
            cv2.circle(overlay, (int(x * PX_PER_MM), int(y * PX_PER_MM)),
                       int(L.BUBBLE_R * PX_PER_MM) + 4, color, 3)

    # --- Student ID ---
    id_scores = {}
    for d, v, x, y in L.id_bubbles():
        id_scores.setdefault(d, []).append((v, x, y, _darkness(norm, x, y)))
    digits, id_flags = [], []
    for d in sorted(id_scores):
        row = id_scores[d]
        ans, flag = _decide([s for _, _, _, s in row], [str(v) for v, _, _, _ in row])
        digits.append(ans if ans is not None else "?")
        if flag and not flag.startswith("check"):
            id_flags.append(f"digit {d}: {flag}")
        for v, x, y, s in row:
            if s >= FILLED:
                mark(x, y, (0, 170, 0) if not flag else (0, 0, 230))
            elif s >= UNCLEAR_LOW:
                mark(x, y, (0, 140, 255))
    student_id = "".join(digits)

    # --- Questions ---
    rows = {}
    for sec, q, opt, x, y in L.question_bubbles():
        rows.setdefault((sec, q), []).append((opt, x, y, _darkness(norm, x, y)))

    answers, flags = {}, []
    for (sec, q), row in rows.items():
        ans, flag = _decide([s for *_, s in row], [o for o, *_ in row])
        answers.setdefault(sec, {})[q] = ans
        if flag:
            flags.append({"section": sec, "question": q, "issue": flag})
        for opt, x, y, s in row:
            if s >= FILLED:
                ok = flag is None or flag == "blank"
                mark(x, y, (0, 170, 0) if ok else (0, 0, 230))
            elif s >= UNCLEAR_LOW:
                mark(x, y, (0, 140, 255))

    needs_review = [f for f in flags if f["issue"] != "blank"]
    blanks = [f for f in flags if f["issue"] == "blank"]
    if len(needs_review) > 10:
        warnings.append("Many unclear marks on this sheet. Check the photo, or review it fully.")

    result = {
        "student_id": student_id,
        "student_id_ok": not id_flags,
        "student_id_flags": id_flags,
        "answers": answers,
        "flags": needs_review,
        "blank_questions": [(f["section"], f["question"]) for f in blanks],
        "warnings": warnings,
        "status": "ok" if not (id_flags or needs_review) else "needs_review",
    }
    if debug:
        result["_overlay"] = overlay
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    debug_out = sys.argv[sys.argv.index("--debug") + 1] if "--debug" in sys.argv else None
    try:
        res = read_sheet(open(path, "rb").read(), debug=bool(debug_out))
    except SheetError as e:
        print(json.dumps({"status": "retake", "error": str(e)}, indent=2))
        sys.exit(2)
    if debug_out:
        cv2.imwrite(debug_out, res.pop("_overlay"))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
