"""
Generate the printable OMR answer sheet as a PDF.

Usage:
    python make_sheet.py                      # writes omr_sheet.pdf
    python make_sheet.py --centre "My Centre" --out my_sheet.pdf
"""
import argparse
import io

import cv2
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import layout as L

INK = HexColor("#222222")
BUBBLE_LINE = HexColor("#555555")
BUBBLE_TEXT = HexColor("#9a9a9a")   # light, so the reader ignores it
MUTED = HexColor("#666666")
RULE = HexColor("#bbbbbb")


def X(x_mm):
    return x_mm * mm


def Y(y_mm):
    """Convert a top-left mm y coordinate into reportlab's bottom-left points."""
    return (L.PAGE_H - y_mm) * mm


def marker_image(marker_id):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(dictionary, marker_id, 400)
    ok, png = cv2.imencode(".png", img)
    return ImageReader(io.BytesIO(png.tobytes()))


def bubble(c, x, y, label, font_size=5.2):
    c.setStrokeColor(BUBBLE_LINE)
    c.setLineWidth(0.6)
    c.setFillColor(white)
    c.circle(X(x), Y(y), L.BUBBLE_R * mm, stroke=1, fill=1)
    c.setFillColor(BUBBLE_TEXT)
    c.setFont("Helvetica", font_size)
    c.drawCentredString(X(x), Y(y) - font_size * 0.35, label)


def draw_sheet(c, centre_name):
    # Corner markers
    for mid, (cx, cy) in L.MARKER_CENTERS.items():
        s = L.MARKER_SIZE
        c.drawImage(marker_image(mid), X(cx - s / 2), Y(cy + s / 2), s * mm, s * mm)

    # Title
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(X(105), Y(15), f"{centre_name} · Mock Test Answer Sheet")
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    c.drawCentredString(X(105), Y(21),
                        "Reading Comprehension · Quantitative Reasoning · Abstract Reasoning")

    # Name and date lines
    c.setFillColor(INK)
    c.setFont("Helvetica", 9)
    c.drawString(X(22), Y(31), "Student name")
    c.setStrokeColor(INK)
    c.setLineWidth(0.5)
    c.line(X(45), Y(31.5), X(140), Y(31.5))
    c.drawString(X(146), Y(31), "Date")
    c.line(X(155), Y(31.5), X(188), Y(31.5))

    # Instructions box
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.roundRect(X(22), Y(77.5), 78 * mm, 39.5 * mm, 2 * mm, stroke=1, fill=0)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(X(26), Y(44), "How to fill in this sheet")
    c.setFont("Helvetica", 7.6)
    lines = [
        "Use a dark pencil (2B or HB).",
        "Fill ONE bubble per question, completely.",
        "To change an answer, erase it cleanly.",
        "Write your ID in the boxes and fill the bubbles.",
        "Do not write on the corner squares.",
    ]
    for i, t in enumerate(lines):
        c.drawString(X(26), Y(50 + i * 4.2), "•  " + t)

    # Correct / wrong examples
    c.setFont("Helvetica-Bold", 7.2)
    c.drawString(X(26), Y(72.5), "Right")
    c.setFillColor(INK)
    c.circle(X(37), Y(71.6), L.BUBBLE_R * mm, stroke=0, fill=1)
    c.drawString(X(46), Y(72.5), "Wrong")
    ex_x = [58, 66, 74, 82]
    for i, x in enumerate(ex_x):
        c.setStrokeColor(BUBBLE_LINE)
        c.setFillColor(white)
        c.circle(X(x), Y(71.6), L.BUBBLE_R * mm, stroke=1, fill=1)
        c.setStrokeColor(INK)
        c.setLineWidth(0.9)
        r = L.BUBBLE_R
        if i == 0:   # tick
            c.line(X(x - 1.2), Y(71.6), X(x - 0.3), Y(72.6))
            c.line(X(x - 0.3), Y(72.6), X(x + 1.4), Y(70.4))
        elif i == 1:  # cross
            c.line(X(x - 1.1), Y(70.5), X(x + 1.1), Y(72.7))
            c.line(X(x - 1.1), Y(72.7), X(x + 1.1), Y(70.5))
        elif i == 2:  # dot
            c.setFillColor(INK)
            c.circle(X(x), Y(71.6), 0.6 * mm, stroke=0, fill=1)
        else:         # half filled
            c.setFillColor(INK)
            c.wedge(X(x - r), Y(71.6 + r), X(x + r), Y(71.6 - r), 90, 180,
                    stroke=0, fill=1)
        c.setLineWidth(0.6)

    # Student ID block
    c.setStrokeColor(RULE)
    c.roundRect(X(L.ID_LEFT), Y(77.5), 84 * mm, 39.5 * mm, 2 * mm, stroke=1, fill=0)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(X(L.ID_LEFT + 4), Y(L.ID_TOP + 6), "Student ID")
    c.setFont("Helvetica", 6.5)
    c.setFillColor(MUTED)
    c.drawString(X(L.ID_LEFT + 23), Y(L.ID_TOP + 6),
                 "Write each digit in its box, then fill the matching bubble.")
    for d in range(L.ID_DIGITS):
        y = L.ID_FIRST_ROW_Y + d * L.ID_ROW_PITCH
        c.setStrokeColor(INK)
        c.setLineWidth(0.5)
        c.rect(X(L.ID_BOX_X), Y(y + 2.1), 6 * mm, 4.2 * mm, stroke=1, fill=0)
    for d, v, x, y in L.id_bubbles():
        bubble(c, x, y, str(v))

    # Sections
    for sec in L.SECTIONS:
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(X(sec["x"]), Y(L.SECTION_HEADER_Y), sec["name"])
        # option letters over the columns
        c.setFont("Helvetica-Bold", 6.5)
        c.setFillColor(MUTED)
        for i, opt in enumerate(L.OPTIONS):
            x = sec["x"] + L.QNUM_W + L.BUBBLE_R + i * L.OPTION_PITCH
            c.drawCentredString(X(x), Y(L.FIRST_ROW_Y - 4.2), opt)
        for q in range(sec["count"]):
            y = L.FIRST_ROW_Y + q * L.ROW_PITCH
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 7)
            c.drawRightString(X(sec["x"] + L.QNUM_W - 1.5), Y(y) - 2.4, str(q + 1))
            # thin guide line every 5 questions
            if q % 5 == 4 and q != sec["count"] - 1:
                c.setStrokeColor(RULE)
                c.setLineWidth(0.4)
                yy = y + L.ROW_PITCH / 2
                c.line(X(sec["x"]), Y(yy), X(sec["x"] + L.QNUM_W + 2 * L.BUBBLE_R
                                              + 4 * L.OPTION_PITCH), Y(yy))
    for _, _, opt, x, y in L.question_bubbles():
        bubble(c, x, y, opt)

    # Footer
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.2)
    c.drawCentredString(X(105), Y(266),
                        "The Writing task is completed in the separate writing booklet.")
    c.drawCentredString(X(105), Y(270),
                        "Keep this sheet flat and clean. Do not fold or staple near the corners.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--centre", default="Your Centre")
    ap.add_argument("--out", default="omr_sheet.pdf")
    args = ap.parse_args()
    c = canvas.Canvas(args.out, pagesize=A4)
    c.setTitle("OMR Answer Sheet")
    draw_sheet(c, args.centre)
    c.showPage()
    c.save()
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
