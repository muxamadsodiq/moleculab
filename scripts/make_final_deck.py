"""Build the final MOLECULAB hackathon deck: 14 slides, 5-7 minutes of speaking.

Every figure in here was measured on this machine. Nothing is estimated, and the two
replicate-spread numbers that could not be reproduced from the shipped dataset are deliberately
absent.

    .venv/Scripts/python scripts/make_final_deck.py
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = Path(__file__).resolve().parent.parent / "presentation"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- design system (from template)
BG = RGBColor(0x0A, 0x0E, 0x1A)
BG_ALT = RGBColor(0x0D, 0x11, 0x17)
CARD = RGBColor(0x11, 0x19, 0x27)
RED = RGBColor(0xE5, 0x53, 0x4B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DIM = RGBColor(0xA8, 0xB3, 0xC4)
MUTED = RGBColor(0x6B, 0x78, 0x8A)
TEAL = RGBColor(0x4D, 0xB6, 0xA4)
AMBER = RGBColor(0xD4, 0xA9, 0x5A)
BLUE = RGBColor(0x6F, 0x9F, 0xE0)
PURPLE = RGBColor(0xB9, 0x8A, 0xE0)
GREEN = RGBColor(0x5F, 0xB3, 0x6F)
LINE = RGBColor(0x24, 0x2D, 0x39)

W, H = Inches(10), Inches(5.625)
TITLE_FONT = "Calibri"
MONO = "Consolas"

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]

_n = [0]


def slide(bg=BG):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    r.fill.solid()
    r.fill.fore_color.rgb = bg
    r.line.fill.background()
    r.shadow.inherit = False
    return s


def box(s, x, y, w, h, text, size=14, color=DIM, bold=False, font=TITLE_FONT,
        align=PP_ALIGN.LEFT, spacing=1.15, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        run = p.add_run()
        run.text = ln
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.bold = bold
        run.font.name = font
    return tb


def rect(s, x, y, w, h, fill=CARD, line=None, lw=0.75):
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    r.fill.solid()
    r.fill.fore_color.rgb = fill
    if line:
        r.line.color.rgb = line
        r.line.width = Pt(lw)
    else:
        r.line.fill.background()
    r.shadow.inherit = False
    return r


def chrome(s, title, kicker=None, page=True):
    """Standard slide furniture: red top bar, title, kicker, footer, page number."""
    rect(s, 0, 0, W, Inches(0.05), RED)
    box(s, Inches(0.55), Inches(0.42), Inches(8.9), Inches(0.5), title, 26, WHITE, True)
    if kicker:
        box(s, Inches(0.55), Inches(0.95), Inches(8.9), Inches(0.3), kicker, 12, RED, False, MONO)
    rect(s, 0, H - Inches(0.3), W, Inches(0.3), RGBColor(0x1A, 0x0E, 0x10))
    box(s, Inches(0.55), H - Inches(0.24), Inches(6), Inches(0.2),
        "#MilliyAIXakaton  #BukharaAI  2026", 8, MUTED, False, MONO)
    if page:
        _n[0] += 1
        box(s, Inches(9.1), H - Inches(0.24), Inches(0.5), Inches(0.2),
            str(_n[0]), 8, RED, True, MONO, PP_ALIGN.RIGHT)


def card(s, x, y, w, h, heading, body, accent=TEAL, hsize=13, bsize=10.5):
    rect(s, x, y, w, h, CARD, LINE)
    rect(s, x, y, Inches(0.035), h, accent)
    box(s, x + Inches(0.18), y + Inches(0.13), w - Inches(0.32), Inches(0.3),
        heading, hsize, WHITE, True)
    box(s, x + Inches(0.18), y + Inches(0.46), w - Inches(0.32), h - Inches(0.58),
        body, bsize, DIM, spacing=1.22)


def stat(s, x, y, w, value, label, accent=TEAL):
    rect(s, x, y, w, Inches(0.92), CARD, LINE)
    box(s, x, y + Inches(0.1), w, Inches(0.42), value, 22, accent, True,
        MONO, PP_ALIGN.CENTER)
    box(s, x, y + Inches(0.56), w, Inches(0.26), label, 9, MUTED, False,
        TITLE_FONT, PP_ALIGN.CENTER)


# ================================================================= 1. title
s = slide()
rect(s, 0, 0, W, Inches(0.05), RED)
rect(s, 0, H - Inches(0.05), W, Inches(0.05), RED)
box(s, Inches(0.7), Inches(0.5), Inches(4), Inches(0.3), "Pitch Day", 11, RED, True, MONO)
box(s, Inches(0.7), Inches(1.45), Inches(8.6), Inches(1.0), "MOLECULAB", 54, WHITE, True)
rect(s, Inches(0.7), Inches(2.55), Inches(2.2), Inches(0.04), RED)
box(s, Inches(0.7), Inches(2.8), Inches(8.4), Inches(0.8),
    "An evidence-grounded research environment for early-stage drug discovery\n"
    "and pharmaceutical formulation.", 17, DIM, spacing=1.3)
box(s, Inches(0.7), Inches(4.1), Inches(8.6), Inches(0.5),
    "It refuses to answer when the evidence cannot support an answer.", 13, TEAL, True)
box(s, Inches(0.7), Inches(4.72), Inches(8.6), Inches(0.3),
    "Uzbekistan National AI Hackathon   ·   #MilliyAIXakaton  #BukharaAI  2026",
    9.5, MUTED, False, MONO)

# ================================================================= 2. the problem
s = slide()
chrome(s, "Research is fragmented", "THE PROBLEM")
box(s, Inches(0.55), Inches(1.45), Inches(8.9), Inches(0.8),
    "A researcher investigating one compound moves between five systems that do not talk to\n"
    "each other. Most of the day goes on copying between tabs and trying not to lose track of\n"
    "where each fact came from.", 13.5, DIM, spacing=1.3)
labels = ["Structure\ndatabase", "Literature\nindex", "Activity\ndatabase", "Clinical\ntrials",
          "Spreadsheet"]
x = Inches(0.55)
for i, lab in enumerate(labels):
    rect(s, x, Inches(2.75), Inches(1.6), Inches(0.95), CARD, LINE)
    box(s, x, Inches(2.95), Inches(1.6), Inches(0.6), lab, 11, DIM, False,
        TITLE_FONT, PP_ALIGN.CENTER, 1.2)
    if i < 4:
        box(s, x + Inches(1.62), Inches(3.1), Inches(0.26), Inches(0.3), "✕", 13, RED,
            True, TITLE_FONT, PP_ALIGN.CENTER)
    x += Inches(1.78)
box(s, Inches(0.55), Inches(4.05), Inches(8.9), Inches(0.8),
    "The information exists. Nothing connects it, and nothing keeps a fact attached to its source.",
    13, AMBER, True)

# ================================================================= 3. the solution
s = slide()
chrome(s, "One research bench", "THE SOLUTION")
box(s, Inches(0.55), Inches(1.4), Inches(8.9), Inches(0.4),
    "You describe a goal in your own words. Seven stages, in plain language.", 13, DIM)
stages = ["Research", "Evidence", "Assist", "Explore", "Calculate", "Compare", "Report"]
x = Inches(0.5)
wid = Inches(1.28)
for i, st in enumerate(stages):
    acc = TEAL if i < 3 else (BLUE if i < 5 else PURPLE)
    rect(s, x, Inches(2.0), wid, Inches(0.72), CARD, acc, 1.0)
    box(s, x, Inches(2.22), wid, Inches(0.3), st, 10.5, WHITE, True,
        TITLE_FONT, PP_ALIGN.CENTER)
    if i < 6:
        box(s, x + wid - Inches(0.04), Inches(2.25), Inches(0.26), Inches(0.26), "›",
            14, MUTED, True, TITLE_FONT, PP_ALIGN.CENTER)
    x += wid + Inches(0.07)
card(s, Inches(0.55), Inches(3.05), Inches(2.85), Inches(1.5), "Evidence with provenance",
     "8 live providers. Every record keeps its source, retrieval time and release id. "
     "An attached record is a pointer, never a copy.", TEAL)
card(s, Inches(3.58), Inches(3.05), Inches(2.85), Inches(1.5), "Build and simulate",
     "Draw a molecule atom by atom, watch it move in 3D, dose it to a species and see where "
     "it goes.", BLUE)
card(s, Inches(6.61), Inches(3.05), Inches(2.84), Inches(1.5), "Reproducible output",
     "Every calculation returns a SHA-256 fingerprint over its inputs and software versions.",
     PURPLE)

# ================================================================= 4. differentiator
s = slide()
chrome(s, "Computed is never measured", "WHAT MAKES IT DIFFERENT")
box(s, Inches(0.55), Inches(1.4), Inches(8.9), Inches(0.5),
    "Most tools show a number and let you assume it was measured.\n"
    "MOLECULAB labels every value with where it came from.", 13.5, DIM, spacing=1.3)
states = [("experimental", TEAL), ("derived", BLUE), ("computed", PURPLE),
          ("predicted", AMBER), ("hypothesis", AMBER), ("unverified", MUTED),
          ("contradiction", RED), ("missing", MUTED)]
x, y = Inches(0.55), Inches(2.25)
for i, (name, col) in enumerate(states):
    if i == 4:
        x, y = Inches(0.55), Inches(2.75)
    rect(s, x, y, Inches(2.1), Inches(0.38), CARD, col, 1.0)
    box(s, x, y + Inches(0.08), Inches(2.1), Inches(0.24), name, 10, col, True,
        MONO, PP_ALIGN.CENTER)
    x += Inches(2.23)
rules = ("A computed descriptor cannot satisfy a physical constraint   ·   "
         "IC50 is never pooled with Ki\n"
         "Censored values never become exact numbers   ·   "
         "Missing stays missing, never zero   ·   No composite scores")
box(s, Inches(0.55), Inches(3.42), Inches(8.9), Inches(0.7), rules, 11, DIM, spacing=1.35)
rect(s, Inches(0.55), Inches(4.18), Inches(8.9), Inches(0.62), CARD, TEAL)
box(s, Inches(0.75), Inches(4.3), Inches(8.5), Inches(0.4),
    "The AI can only LOSE a claim, never promote one. Unsupported claims are deleted, "
    "not softened.", 12.5, TEAL, True)

# ================================================================= 5. refusal
s = slide()
chrome(s, "What it refuses to do", "AND WHY THAT IS THE POINT")
box(s, Inches(0.55), Inches(1.4), Inches(8.9), Inches(0.35),
    "Three capabilities return nothing at all. Each says why, on screen.", 13, DIM)
card(s, Inches(0.55), Inches(1.95), Inches(2.9), Inches(2.0), "Molecular docking",
     "No engine, no prepared receptors.\n\nA docking score is the easiest "
     "plausible-looking number to fabricate — and the hardest to challenge.", RED)
card(s, Inches(3.63), Inches(1.95), Inches(2.9), Inches(2.0), "QSAR prediction",
     "Gated on evidence, not effort.\n\n6,774 of 7,777 compounds have a single "
     "unreplicated measurement. Of the rest, 26% disagree with themselves by ≥10×.", RED)
card(s, Inches(6.71), Inches(1.95), Inches(2.74), Inches(2.0), "Dissolution prediction",
     "No validated model, and no measured profiles to validate one against.", RED)
rect(s, Inches(0.55), Inches(4.15), Inches(8.9), Inches(0.68), CARD, TEAL)
box(s, Inches(0.75), Inches(4.26), Inches(8.5), Inches(0.46),
    "Where docking would go, we show the real crystal structure instead:\n"
    "EGFR + erlotinib, the Met769 hinge bond measured at 2.70 Å — checkable against "
    "the literature.", 11.5, TEAL, True, TITLE_FONT, PP_ALIGN.LEFT, 1.25)

# ================================================================= 6. what it computes
s = slide()
chrome(s, "16 real calculations", "THE COMPUTATIONAL SANDBOX")
cats = [
    ("MOLECULAR", "descriptors · Lipinski · structure validation\nconformer ensemble · "
     "similarity · scaffold\nmolecular dynamics · implicit solvent", TEAL, Inches(1.5)),
    ("DOSING", "pharmacokinetics · whole-body PBPK · dilution", BLUE, Inches(2.62)),
    ("ACTIVITY", "measured activity distribution · assay comparability", PURPLE, Inches(3.34)),
    ("FORMULATION / STRUCTURAL", "composition totals · unit conversion · "
     "protein–ligand contacts", AMBER, Inches(4.06)),
]
for name, body, col, y in cats:
    h = Inches(1.02) if name == "MOLECULAR" else Inches(0.62)
    rect(s, Inches(0.55), y, Inches(5.6), h, CARD, LINE)
    rect(s, Inches(0.55), y, Inches(0.035), h, col)
    box(s, Inches(0.73), y + Inches(0.09), Inches(5.3), Inches(0.22), name, 9, col, True, MONO)
    box(s, Inches(0.73), y + Inches(0.33), Inches(5.3), h - Inches(0.4), body, 10, DIM,
        spacing=1.25)
box(s, Inches(6.35), Inches(1.5), Inches(3.1), Inches(0.3), "EVERY RUN RETURNS", 9, RED,
    True, MONO)
for i, (lab, val) in enumerate([("method", "the exact algorithm"),
                                ("inputs", "every value used"),
                                ("versions", "RDKit, engine, app"),
                                ("limitations", "what it cannot say"),
                                ("fingerprint", "SHA-256, reproducible")]):
    y = Inches(1.85) + Inches(0.52) * i
    rect(s, Inches(6.35), y, Inches(3.1), Inches(0.44), CARD, LINE)
    box(s, Inches(6.5), y + Inches(0.06), Inches(2.9), Inches(0.18), lab, 9, TEAL, True, MONO)
    box(s, Inches(6.5), y + Inches(0.24), Inches(2.9), Inches(0.18), val, 9, DIM)

# ================================================================= 7. dynamics + solvent
s = slide()
chrome(s, "Molecules that actually move", "MOLECULAR DYNAMICS")
box(s, Inches(0.55), Inches(1.4), Inches(8.9), Inches(0.45),
    "Velocity-Verlet integration on the MMFF94 potential with a Berendsen thermostat.\n"
    "Real time evolution — not a spinning picture.", 13, DIM, spacing=1.3)
for i, (v, l) in enumerate([("800", "steps"), ("0.15 s", "to compute"),
                            ("310 K", "thermostat"), ("2.4 Å", "RMSD drift")]):
    stat(s, Inches(0.55) + Inches(2.3) * i, Inches(2.25), Inches(2.1), v, l, TEAL)
card(s, Inches(0.55), Inches(3.4), Inches(4.35), Inches(1.4), "What is real",
     "Temperature fluctuates around target. Energy exchanges between kinetic and potential. "
     "RMSD climbs as the molecule flexes. The trajectory plays back in 3D.", TEAL)
card(s, Inches(5.1), Inches(3.4), Inches(4.35), Inches(1.4), "What we say out loud",
     "In vacuum or implicit solvent — no water molecules. Picoseconds, not microseconds. "
     "No bonds break. Not a binding simulation.", AMBER)

# ================================================================= 8. PK / PBPK
s = slide()
chrome(s, "Dose it, and see where it goes", "PHARMACOKINETICS & WHOLE-BODY PBPK")
box(s, Inches(0.55), Inches(1.38), Inches(8.9), Inches(0.4),
    "Give a dose, to a species, for a period. Nine organ compartments connected by real "
    "blood flow.", 13, DIM)
rect(s, Inches(0.55), Inches(1.95), Inches(4.35), Inches(1.35), CARD, LINE)
rect(s, Inches(0.55), Inches(1.95), Inches(0.035), Inches(1.35), GREEN)
box(s, Inches(0.73), Inches(2.06), Inches(4.0), Inches(0.26),
    "VALIDATED AGAINST CLOSED-FORM SOLUTIONS", 9, GREEN, True, MONO)
box(s, Inches(0.73), Inches(2.38), Inches(4.0), Inches(0.85),
    "half-life   analytic 4.159  →  simulated 4.159\n"
    "AUC         analytic 16.667 →  simulated 16.660   (0.04%)", 10, DIM, False, MONO,
    spacing=1.45)
rect(s, Inches(5.1), Inches(1.95), Inches(4.35), Inches(1.35), CARD, LINE)
rect(s, Inches(5.1), Inches(1.95), Inches(0.035), Inches(1.35), GREEN)
box(s, Inches(5.28), Inches(2.06), Inches(4.0), Inches(0.26), "MASS BALANCE, ALL 5 SPECIES",
    9, GREEN, True, MONO)
box(s, Inches(5.28), Inches(2.42), Inches(4.0), Inches(0.5), "0.00000 %", 24, GREEN, True,
    MONO)
box(s, Inches(5.28), Inches(2.95), Inches(4.0), Inches(0.28),
    "published on every run, so you can check it", 9, MUTED)
box(s, Inches(0.55), Inches(3.5), Inches(8.9), Inches(0.35),
    "human · rat · mouse · rabbit · dog     —     allometric scaling, hourly to 6 months",
    11.5, BLUE, True, MONO)
rect(s, Inches(0.55), Inches(4.0), Inches(8.9), Inches(0.78), CARD, AMBER)
box(s, Inches(0.75), Inches(4.12), Inches(8.5), Inches(0.56),
    "PK parameters are INPUTS, not predictions. A concentration is not an effect.\n"
    "Nothing here predicts efficacy, toxicity or harm — and the product says so on the page.",
    11.5, AMBER, True, TITLE_FONT, PP_ALIGN.LEFT, 1.25)

# ================================================================= 9. AI
s = slide()
chrome(s, "The AI can only lose a claim", "EVIDENCE-GROUNDED ASSISTANCE")
steps = [("Retrieve", "records from the index", TEAL),
         ("Draft", "the model writes wording", BLUE),
         ("Split", "into atomic claims", PURPLE),
         ("Verify", "each against its cited record", AMBER),
         ("Delete", "unsupported claims", RED)]
x = Inches(0.55)
for name, desc, col in steps:
    rect(s, x, Inches(1.6), Inches(1.68), Inches(1.15), CARD, col, 1.0)
    box(s, x, Inches(1.74), Inches(1.68), Inches(0.26), name, 11.5, col, True,
        TITLE_FONT, PP_ALIGN.CENTER)
    box(s, x + Inches(0.1), Inches(2.06), Inches(1.48), Inches(0.6), desc, 8.5, DIM,
        False, TITLE_FONT, PP_ALIGN.CENTER, 1.2)
    x += Inches(1.79)
box(s, Inches(0.55), Inches(3.0), Inches(8.9), Inches(0.4),
    "The model never supplies facts. It cannot promote a claim — only lose one.", 13,
    WHITE, True)
rect(s, Inches(0.55), Inches(3.55), Inches(8.9), Inches(1.0), CARD, TEAL)
box(s, Inches(0.75), Inches(3.68), Inches(8.5), Inches(0.26),
    'ASK:  "Is glabridin proven to cure cancer?"', 11, MUTED, False, MONO)
box(s, Inches(0.75), Inches(4.0), Inches(8.5), Inches(0.46),
    "→  It abstains, and names what the records do and do not contain.\n"
    "    Not a hedge. A refusal, with the reason.", 11.5, TEAL, True, TITLE_FONT,
    PP_ALIGN.LEFT, 1.25)

# ================================================================= 10. technical
s = slide()
chrome(s, "Built and tested", "TECHNICAL FOUNDATION")
for i, (v, l) in enumerate([("542", "tests passing"), ("0", "tests failing"),
                            ("146", "API endpoints"), ("16", "calculations")]):
    stat(s, Inches(0.55) + Inches(2.3) * i, Inches(1.5), Inches(2.1), v, l,
         GREEN if i < 2 else TEAL)
for i, (v, l) in enumerate([("8", "live providers"), ("33", "database tables"),
                            ("40/40", "frontend smoke"), ("0", "build steps")]):
    stat(s, Inches(0.55) + Inches(2.3) * i, Inches(2.6), Inches(2.1), v, l, BLUE)
box(s, Inches(0.55), Inches(3.75), Inches(8.9), Inches(0.9),
    "RDKit 2026.9.2  ·  FastAPI 0.163.1  ·  SQLAlchemy 2.1.8  ·  Alembic head 0007\n"
    "Vanilla JavaScript frontend, no build step, works offline\n"
    "Engines are pure Python with no web dependency — the science is testable without a server",
    11, DIM, False, TITLE_FONT, PP_ALIGN.LEFT, 1.35)

# ================================================================= 11. market
s = slide()
chrome(s, "Who this is for", "BUSINESS")
card(s, Inches(0.55), Inches(1.45), Inches(4.35), Inches(1.6), "Customers",
     "Pharmaceutical formulation R&D\nAcademic natural-product chemistry\n"
     "Contract research organisations\nRegulatory and quality teams\n"
     "Central Asian manufacturers", TEAL)
card(s, Inches(5.1), Inches(1.45), Inches(4.35), Inches(1.6), "What they get",
     "Less time collecting and reconciling\nEvidence that stays attached to its source\n"
     "Reproducible, auditable results\nHonest uncertainty instead of false precision", BLUE)
box(s, Inches(0.55), Inches(3.2), Inches(8.9), Inches(0.3),
    "REVENUE MODELS — options under discussion, not decisions", 9.5, RED, True, MONO)
x = Inches(0.55)
for opt in ["Institutional\nlicensing", "Per-seat\nsubscription", "Hosted\ntier",
            "Private-corpus\nconsulting"]:
    rect(s, x, Inches(3.6), Inches(2.1), Inches(0.72), CARD, LINE)
    box(s, x, Inches(3.74), Inches(2.1), Inches(0.5), opt, 10, DIM, False,
        TITLE_FONT, PP_ALIGN.CENTER, 1.2)
    x += Inches(2.28)
box(s, Inches(0.55), Inches(4.55), Inches(8.9), Inches(0.3),
    "No revenue, no customers, no pilots yet. This is a working prototype, and we say so.",
    11, AMBER, True)

# ================================================================= 12. position
s = slide()
chrome(s, "Where we sit", "COMPETITIVE POSITION")
rows = [("Electronic lab notebooks", "manage records", "no chemistry, no evidence grounding"),
        ("ChEMBL, PubChem", "excellent databases", "no workflow, no synthesis of evidence"),
        ("Schrödinger and similar", "powerful computation", "expensive, heavy, not "
         "evidence-grounded"),
        ("MOLECULAB", "evidence-grounded workflow", "with honest uncertainty")]
y = Inches(1.5)
for i, (name, good, gap) in enumerate(rows):
    last = i == len(rows) - 1
    rect(s, Inches(0.55), y, Inches(8.9), Inches(0.72), CARD, TEAL if last else LINE,
         1.2 if last else 0.75)
    if last:
        rect(s, Inches(0.55), y, Inches(0.035), Inches(0.72), TEAL)
    box(s, Inches(0.78), y + Inches(0.22), Inches(2.9), Inches(0.3), name, 12,
        TEAL if last else WHITE, True)
    box(s, Inches(3.75), y + Inches(0.22), Inches(2.3), Inches(0.3), good, 10.5, DIM)
    box(s, Inches(6.2), y + Inches(0.22), Inches(3.1), Inches(0.3), gap, 10.5,
        TEAL if last else MUTED)
    y += Inches(0.82)
box(s, Inches(0.55), Inches(4.9), Inches(8.9), Inches(0.3),
    "Qualitative positioning, not a feature audit.", 9, MUTED)

# ================================================================= 13. region + next
s = slide()
chrome(s, "Why Uzbekistan, and what is next", "OPPORTUNITY")
card(s, Inches(0.55), Inches(1.45), Inches(4.35), Inches(1.75), "Why this region",
     "Underserved natural-product chemistry with real pharmacological interest.\n\n"
     "The platform already handles Cyrillic and Uzbek plant aliases.\n\n"
     "No existing tooling for this corpus.", TEAL)
card(s, Inches(5.1), Inches(1.45), Inches(4.35), Inches(1.75), "To validate in a pilot",
     "Actual time saved against a measured manual baseline.\n\n"
     "Workflow fit with real researchers.\n\nWillingness to pay.", AMBER)
box(s, Inches(0.55), Inches(3.4), Inches(8.9), Inches(0.3), "NEXT", 9.5, RED, True, MONO)
box(s, Inches(0.55), Inches(3.72), Inches(8.9), Inches(1.0),
    "1.  Measure time-to-answer against a manual baseline — we will not claim a number "
    "until we have one\n"
    "2.  Wire the assistant into every stage of the bench\n"
    "3.  Deploy beyond a single machine, with a server-side key path\n"
    "4.  Expand the curated corpus with regional partners",
    11, DIM, False, TITLE_FONT, PP_ALIGN.LEFT, 1.4)

# ================================================================= 14. close
s = slide()
rect(s, 0, 0, W, Inches(0.05), RED)
rect(s, 0, H - Inches(0.05), W, Inches(0.05), RED)
box(s, Inches(0.7), Inches(1.1), Inches(8.6), Inches(0.9),
    "Science should not be\nslowed by disconnected tools.", 30, WHITE, True,
    TITLE_FONT, PP_ALIGN.LEFT, 1.25)
rect(s, Inches(0.7), Inches(2.6), Inches(2.2), Inches(0.04), RED)
box(s, Inches(0.7), Inches(2.9), Inches(8.6), Inches(0.9),
    "MOLECULAB connects evidence, molecules and computation in one workspace —\n"
    "and tells you plainly when it does not know.", 15, DIM, spacing=1.35)
x = Inches(0.7)
for v, l in [("542", "tests passing"), ("16", "calculations"), ("8", "data providers"),
             ("3", "honest refusals")]:
    stat(s, x, Inches(4.0), Inches(2.0), v, l, TEAL)
    x += Inches(2.17)

prs.save(str(OUT / "MOLECULAB_FINAL.pptx"))
print(f"wrote {OUT / 'MOLECULAB_FINAL.pptx'}  ({len(prs.slides)} slides)")
