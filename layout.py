"""
Single source of truth for the answer sheet layout.

Both the PDF generator (make_sheet.py) and the reader (omr_reader.py) import
this file, so a bubble is always read exactly where it was printed.
All positions are in millimetres from the TOP-LEFT corner of an A4 page.
"""

PAGE_W, PAGE_H = 210.0, 297.0

# ---- Corner markers (ArUco 4x4 dictionary, ids 0..3) ----
MARKER_SIZE = 12.0  # mm
MARKER_CENTERS = {
    0: (16.0, 16.0),    # top-left
    1: (194.0, 16.0),   # top-right
    2: (16.0, 280.0),   # bottom-left
    3: (194.0, 280.0),  # bottom-right
}

# ---- Bubbles ----
BUBBLE_R = 1.9          # radius in mm
OPTIONS = ["A", "B", "C", "D", "E"]
OPTION_PITCH = 6.2      # horizontal distance between option bubbles
ROW_PITCH = 5.0         # vertical distance between questions
QNUM_W = 7.0            # space for the question number

# ---- Sections (edit names / counts here; everything else follows) ----
SECTIONS = [
    {"key": "RC", "name": "Reading Comprehension", "count": 35, "x": 20.0},
    {"key": "QR", "name": "Quantitative Reasoning", "count": 35, "x": 85.0},
    {"key": "AR", "name": "Abstract Reasoning", "count": 35, "x": 150.0},
]
SECTION_HEADER_Y = 81.0
FIRST_ROW_Y = 88.0

# ---- Student ID: 6 digits, one row per digit, bubbles 0-9 ----
ID_DIGITS = 6
ID_LEFT = 106.0
ID_TOP = 38.0
ID_BOX_X = 110.0        # write-in box for each digit
ID_FIRST_BUBBLE_X = 123.0
ID_BUBBLE_PITCH = 6.0
ID_FIRST_ROW_Y = 49.5
ID_ROW_PITCH = 4.8


def question_bubbles():
    """Yield (section_key, question_number, option, x_mm, y_mm)."""
    for sec in SECTIONS:
        for q in range(sec["count"]):
            y = FIRST_ROW_Y + q * ROW_PITCH
            for i, opt in enumerate(OPTIONS):
                x = sec["x"] + QNUM_W + BUBBLE_R + i * OPTION_PITCH
                yield sec["key"], q + 1, opt, x, y


def id_bubbles():
    """Yield (digit_position 1..6, digit_value 0..9, x_mm, y_mm)."""
    for d in range(ID_DIGITS):
        y = ID_FIRST_ROW_Y + d * ID_ROW_PITCH
        for v in range(10):
            x = ID_FIRST_BUBBLE_X + v * ID_BUBBLE_PITCH
            yield d + 1, v, x, y
