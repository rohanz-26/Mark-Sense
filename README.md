# MarkSense

**Automated mock-exam marking and parent reports for tutoring centres.**

A teacher takes a phone photo of a student's answer sheet. MarkSense reads the
sheet, marks it against the answer key, analyses performance by topic and
emails a two-page PDF report to the parent.

**Demo video:** [Watch the walkthrough](https://drive.google.com/file/d/1SrWdiR8qHNQG64cXMqb83UooaNu_vI9j/view?usp=sharing)

![A marked answer sheet](docs/sample_marked_sheet.jpg)

---

## How it works

1. **Print** the answer sheet (`omr_sheet.pdf`). It has four corner markers
   and 35 questions each for Reading Comprehension, Quantitative Reasoning and
   Abstract Reasoning, plus a 6-digit student ID grid.
2. **Photograph** each completed sheet with a phone and upload the photos in
   the web app.
3. **Read:** OpenCV finds the four ArUco corner markers, straightens the page,
   evens out lighting and shadows, and measures how dark each bubble is.
4. **Review:** blank, double-marked or unclear answers are flagged for the
   teacher to confirm, so nothing is guessed.
5. **Mark and analyse:** every question in the answer key is tagged with a
   topic, so results show which topics cost each student marks, across the
   whole batch and over time.
6. **Report:** a two-page PDF report is generated and emailed to the parent.

## Features

| Page | What it does |
|---|---|
| Dashboard | Batch summary, score distribution, section averages vs the last mock, common weak topics, students needing attention, leaderboard |
| Scan sheets | Drag-and-drop photo upload, results table, review queue for flagged answers |
| Students | Search and batch filters; each profile shows a progress chart, strengths, topics to work on and past reports |
| Tests | Question-level analysis, answer-key alerts, leaderboard and topic heatmap for each mock |
| Reports | Preview each report, send one, or email all ready reports |
| Settings | Centre branding, email status, answer keys, printable sheet |

## Tech stack

- **Python** with **FastAPI** and **Uvicorn** for the web app
- **OpenCV** (with ArUco markers) and **NumPy** for reading the sheets
- **ReportLab** for the answer sheet and the PDF reports
- **Gmail SMTP** for sending reports

## Getting started

Requires Python 3.10 or later.

```bash
git clone https://github.com/<your-username>/MarkSense.git
cd MarkSense
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python seed_demo.py               # optional: fills the app with demo data (~10 min)
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>.

### Email setup

Open `settings.py` and set:

- `GMAIL_ADDRESS`: the Gmail account that sends the reports
- `GMAIL_APP_PASSWORD`: an [app password](https://myaccount.google.com/apppasswords)
  for that account (2-Step Verification must be on)
- `SEND_ALL_TO`: while testing, every report goes to this address. Set it to
  `None` to email the parents listed in `students.csv`.

Do not commit your app password.

### Branding

Set `CENTRE_NAME`, `CENTRE_TAGLINE` and `BRAND_COLOUR` in `settings.py`. To use
a logo, put `logo.png` in the project folder and set `LOGO_FILE = "logo.png"`.

## Project structure

| File | Purpose |
|---|---|
| `app.py` | The FastAPI web app and all its pages |
| `omr_reader.py` | Reads a photo of the answer sheet |
| `layout.py` | Bubble positions, shared by the sheet generator and the reader |
| `make_sheet.py` | Generates the printable answer sheet PDF |
| `report.py` | Builds the PDF report and email, and sends it through Gmail |
| `settings.py` | Branding and email settings |
| `students.csv` | Student ID, name, parent email and batch |
| `answer_keys/*.json` | One file per test: the answer and topic for every question |
| `simulate.py` | Tests the reader on synthetic phone photos (tilt, shadow, blur, noise) and prints the accuracy |
| `seed_demo.py` | Rebuilds the demo data: 24 students, 3 marked mocks and 1 upcoming |
| `static/` | Stylesheet and the downloadable answer sheet |

Results are saved in `data/results/<test>/` as one JSON file and one marked
sheet image per student. This folder is created automatically and is not
tracked by Git.

## Useful commands

```bash
python make_sheet.py --centre "My Centre"   # make a branded answer sheet
python omr_reader.py photo.jpg --debug out.png  # read one photo and save an overlay
python simulate.py --n 20                   # check reading accuracy on 20 synthetic sheets
```

`simulate.py` uses `pdftoppm` (from Poppler) to render the blank sheet.

## Demo data

All students, parent emails and answer keys in this repository are fictional
demo data.
