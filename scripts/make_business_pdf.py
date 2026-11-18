"""Generate docs/business/MOLECULAB_BUSINESS_OVERVIEW.pdf from the Markdown source.

    .venv\\Scripts\\python.exe scripts/make_business_pdf.py

The Markdown file is the single editable source. This script parses it (headings, paragraphs, bullet
and numbered lists, tables, bold and code spans) and lays it out with reportlab, then inserts vector
diagrams and charts at the marker comments found in the Markdown:

    <!-- cover -->          the next block (until a pagebreak) is rendered as the designed cover
    <!-- pagebreak -->      start a new page
    <!-- diagram:arch -->   product architecture flow (implemented vs proposed)
    <!-- diagram:evidence -> data-provider map and evidence flow
    <!-- chart:pic50 -->    pIC50 histogram, computed from research_artifacts/jak1_curated.json
    <!-- chart:corpus -->   evidence-record counts, read-only from moleculab.db

Charts use real project data only. If a data file is missing the chart is replaced by an explicit note;
nothing is invented. No screenshots are used.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, HRFlowable, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "business" / "MOLECULAB_BUSINESS_OVERVIEW.md"
OUT = ROOT / "docs" / "business" / "MOLECULAB_BUSINESS_OVERVIEW.pdf"
JAK1 = ROOT / "research_artifacts" / "jak1_curated.json"
DB = ROOT / "moleculab.db"

# ---------------------------------------------------------------- design system
INK = colors.HexColor("#14213D")        # body and headings: dark on light
MUTED = colors.HexColor("#5B6472")
RULE = colors.HexColor("#CBD2DC")
PAPER = colors.HexColor("#F4F6F9")
TEAL = colors.HexColor("#0F766E")       # IMPLEMENTED
TEAL_BG = colors.HexColor("#DDF1EE")
AMBER = colors.HexColor("#9A5B00")      # PROPOSED
AMBER_BG = colors.HexColor("#FFF4DC")
NAVY_BG = colors.HexColor("#E3E8F1")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.4, leading=13.2, textColor=INK, spaceAfter=5.5,
                      alignment=TA_LEFT)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=16.5, leading=20, spaceBefore=14,
                    spaceAfter=3, keepWithNext=1)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=8,
                    spaceAfter=3, keepWithNext=1)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=13, bulletIndent=2, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8, leading=10.4, spaceAfter=0)
CELLH = ParagraphStyle("cellh", parent=CELL, fontName="Helvetica-Bold", textColor=INK)
CAPTION = ParagraphStyle("caption", parent=BODY, fontSize=7.8, leading=10, textColor=MUTED, spaceBefore=2,
                         spaceAfter=8)

# characters Helvetica (WinAnsi) cannot show are mapped to safe ASCII
UNI = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "→": "->",
       "•": "-", "·": "-", "≥": ">=", "≤": "<=", " ": " "}


def clean(text: str) -> str:
    for k, v in UNI.items():
        text = text.replace(k, v)
    return text


def inline(text: str) -> str:
    """Markdown inline -> reportlab paragraph markup."""
    text = clean(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r'<font name="Courier" size="8.4">\1</font>', text)
    text = re.sub(r"\bIMPLEMENTED\b", f'<font color="{TEAL.hexval().replace("0x", "#")}"><b>IMPLEMENTED</b></font>', text)
    text = re.sub(r"\bPROPOSED\b", f'<font color="{AMBER.hexval().replace("0x", "#")}"><b>PROPOSED</b></font>', text)
    return text


# ---------------------------------------------------------------- real data
def load_pic50() -> list[float] | None:
    if not JAK1.exists():
        return None
    rows = json.loads(JAK1.read_text(encoding="utf-8"))
    vals = [r["p_activity_median"] for r in rows if isinstance(r.get("p_activity_median"), (int, float))]
    return vals or None


def load_corpus() -> dict | None:
    if not DB.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
        try:
            types = con.execute("select evidence_type, count(*) from evidence_record group by 1 "
                                "order by 2 desc").fetchall()
            plants = con.execute("select p.accepted_name, count(*) from evidence_record e join plant p "
                                 "on p.id = e.plant_id group by 1 order by 2 desc").fetchall()
            totals = {t: con.execute(f"select count(*) from {t}").fetchone()[0]
                      for t in ("plant", "compound", "publication", "evidence_record")}
        finally:
            con.close()
        return {"types": types, "plants": plants, "totals": totals}
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------- drawing helpers
def rbox(c, x, y, w, h, fill=None, stroke=INK, dash=None, lw=0.8, r=4):
    c.saveState()
    if fill is not None:
        c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(lw)
    if dash:
        c.setDash(*dash)
    c.roundRect(x, y, w, h, r, stroke=1, fill=1 if fill is not None else 0)
    c.restoreState()


def text(c, x, y, s, font="Helvetica", size=7, color=INK, align="left"):
    c.saveState()
    c.setFont(font, size)
    c.setFillColor(color)
    {"left": c.drawString, "center": c.drawCentredString, "right": c.drawRightString}[align](x, y, clean(s))
    c.restoreState()


def wrapped(c, x, y, w, s, font="Helvetica", size=6.9, color=INK, leading=8.4, align="left"):
    """Draw wrapped text downward from y; returns the y after the last line."""
    for line in simpleSplit(clean(s), font, size, w):
        text(c, x if align == "left" else x + w / 2, y, line, font, size, color, align)
        y -= leading
    return y


def arrow(c, x1, y1, x2, y2, color=INK, lw=1.1, dash=None):
    import math
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(lw)
    if dash:
        c.setDash(*dash)
    c.line(x1, y1, x2, y2)
    c.setDash()
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 5
    p = c.beginPath()
    p.moveTo(x2, y2)
    p.lineTo(x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45))
    p.lineTo(x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def badge(c, x, y, w, label, implemented=True, h=11):
    if implemented:
        rbox(c, x, y, w, h, fill=TEAL, stroke=TEAL, r=3)
        text(c, x + w / 2, y + 3.3, label, "Helvetica-Bold", 6.3, colors.white, "center")
    else:
        rbox(c, x, y, w, h, fill=AMBER_BG, stroke=AMBER, dash=(2, 2), lw=0.9, r=3)
        text(c, x + w / 2, y + 3.3, label, "Helvetica-Bold", 6.3, AMBER, "center")


# ---------------------------------------------------------------- flowables: diagrams
class ArchDiagram(Flowable):
    """Researcher -> Workbench -> AI agents -> Data sources -> Computational lab -> Evidence -> Report."""

    def __init__(self):
        super().__init__()
        self.width, self.height = CONTENT_W, 262

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        W = self.width
        bw, bh = 110, 104
        gap = (W - 4 * bw) / 3
        top_y = self.height - 24 - bh
        row2_y = 18
        text(c, 0, self.height - 10, "Product architecture: from a researcher's question to a research report",
             "Helvetica-Bold", 8.6)
        xs1 = [i * (bw + gap) for i in range(4)]
        xs2 = [i * (bw + gap) for i in range(3)]

        def box(x, y, n, title, lines, status, extra=None, human=False):
            impl = status == "IMPLEMENTED"
            if human:
                rbox(c, x, y, bw, bh, fill=NAVY_BG, stroke=INK)
            elif impl:
                rbox(c, x, y, bw, bh, fill=TEAL_BG, stroke=TEAL, lw=1.1)
            else:
                rbox(c, x, y, bw, bh, fill=AMBER_BG, stroke=AMBER, dash=(3, 2))
            rbox(c, x + 6, y + bh - 19, 13, 13, fill=INK, stroke=INK, r=6.5)
            text(c, x + 12.5, y + bh - 15, str(n), "Helvetica-Bold", 7.4, colors.white, "center")
            yy = wrapped(c, x + 24, y + bh - 14, bw - 28, title, "Helvetica-Bold", 8, INK, 9.4)
            yy = wrapped(c, x + 7, min(yy, y + bh - 28) - 4, bw - 14, " ".join(lines), "Helvetica", 6.9, INK, 8.4)
            if extra:
                rbox(c, x + 6, y + 18, bw - 12, 20, fill=AMBER_BG, stroke=AMBER, dash=(2, 2), lw=0.8, r=3)
                wrapped(c, x + 9, y + 31, bw - 18, extra, "Helvetica-Bold", 6, AMBER, 7)
            if human:
                badge(c, x + 6, y + 5, bw - 12, "HUMAN", True)
                c.saveState()
                c.setFillColor(INK)
                c.roundRect(x + 6, y + 5, bw - 12, 11, 3, stroke=0, fill=1)
                c.restoreState()
                text(c, x + bw / 2, y + 8.3, "HUMAN JUDGEMENT", "Helvetica-Bold", 6.3, colors.white, "center")
            else:
                badge(c, x + 6, y + 5, bw - 12, status, impl)

        box(xs1[0], top_y, 1, "Researcher", ["Poses the question, reviews every result, decides what to test."], "", None, True)
        box(xs1[1], top_y, 2, "Research Workbench", ["Explorers, investigations, notes, tasks and hypotheses in one web application."], "IMPLEMENTED")
        # AI agents: split box
        x = xs1[2]
        rbox(c, x, top_y, bw, bh, fill=white_or(TEAL_BG), stroke=TEAL, lw=1.1)
        rbox(c, x, top_y, bw, bh * 0.47, fill=AMBER_BG, stroke=AMBER, dash=(3, 2))
        rbox(c, x + 6, top_y + bh - 19, 13, 13, fill=INK, stroke=INK, r=6.5)
        text(c, x + 12.5, top_y + bh - 15, "3", "Helvetica-Bold", 7.4, colors.white, "center")
        wrapped(c, x + 24, top_y + bh - 14, bw - 28, "AI Research Agents", "Helvetica-Bold", 8, INK, 9.4)
        wrapped(c, x + 7, top_y + bh - 31, bw - 14, "Grounded assistant: retrieve, draft, verify every claim.", "Helvetica", 6.9, INK, 8.4)
        badge(c, x + 6, top_y + bh * 0.47 + 3, bw - 12, "ASSISTANT: IMPLEMENTED", True)
        wrapped(c, x + 7, top_y + bh * 0.47 - 9, bw - 14, "Autonomous multi-step agents", "Helvetica", 6.9, INK, 8.4)
        badge(c, x + 6, top_y + 5, bw - 12, "AGENTS: PROPOSED", False)
        box(xs1[3], top_y, 4, "Scientific Data Sources", ["Open Targets, ChEMBL, PubChem, PubMed, Crossref, GBIF, ClinicalTrials.gov, DailyMed."], "IMPLEMENTED")
        box(xs2[0], row2_y, 5, "Computational Laboratory", ["RDKit descriptors, MMFF94 conformers, Tanimoto similarity, curated activity data."], "IMPLEMENTED", extra="Docking and QSAR: PROPOSED")
        box(xs2[1], row2_y, 6, "Evidence & Results", ["Source-linked claims with verdicts, constraint status, ranked experiments."], "IMPLEMENTED")
        box(xs2[2], row2_y, 7, "Research Report", ["Reproducible Markdown report with a content hash, from a workspace."], "IMPLEMENTED", extra="PDF/DOCX export: PROPOSED")
        # arrows row 1
        ymid = top_y + bh / 2
        for i in range(3):
            arrow(c, xs1[i] + bw + 1, ymid, xs1[i + 1] - 1, ymid)
        # elbow from box 4 to box 5
        xa = xs1[3] + bw / 2
        yb = top_y - 9
        c.saveState()
        c.setStrokeColor(INK)
        c.setLineWidth(1.1)
        c.line(xa, top_y - 1, xa, yb)
        c.line(xa, yb, xs2[0] + bw / 2, yb)
        c.restoreState()
        arrow(c, xs2[0] + bw / 2, yb, xs2[0] + bw / 2, row2_y + bh + 1)
        for i in range(2):
            arrow(c, xs2[i] + bw + 1, row2_y + bh / 2, xs2[i + 1] - 1, row2_y + bh / 2)
        # legend in the free slot
        lx = xs1[3]
        rbox(c, lx, row2_y, bw, bh, fill=None, stroke=RULE, lw=0.8)
        text(c, lx + 7, row2_y + bh - 13, "Legend", "Helvetica-Bold", 7.6)
        rbox(c, lx + 7, row2_y + bh - 33, 16, 11, fill=TEAL_BG, stroke=TEAL, lw=1.1, r=3)
        text(c, lx + 29, row2_y + bh - 30.5, "Implemented, in code", "Helvetica", 6.8)
        rbox(c, lx + 7, row2_y + bh - 51, 16, 11, fill=AMBER_BG, stroke=AMBER, dash=(2, 2), r=3)
        text(c, lx + 29, row2_y + bh - 48.5, "Proposed, not built", "Helvetica", 6.8)
        rbox(c, lx + 7, row2_y + bh - 69, 16, 11, fill=NAVY_BG, stroke=INK, r=3)
        text(c, lx + 29, row2_y + bh - 66.5, "Human step", "Helvetica", 6.8)
        wrapped(c, lx + 7, row2_y + 22, bw - 14, "Status checked against source code and a running instance, 2026-11-18.", "Helvetica", 6.2, MUTED, 7.4)


def white_or(col):
    return colors.white


class EvidenceDiagram(Flowable):
    """Data-provider map and evidence flow."""

    PROVIDERS = [("Open Targets", "disease-target associations"), ("ChEMBL", "measured bioactivity"),
                 ("PubChem", "identifiers, properties"), ("PubMed", "literature abstracts"),
                 ("Crossref", "DOI metadata"), ("GBIF", "taxonomy"),
                 ("ClinicalTrials.gov", "registered studies"), ("DailyMed", "US drug labels")]
    STATUSES = [("Experimental", "measured by a source"), ("Derived", "arithmetic on a measurement"),
                ("Computed", "calculated here, named method"), ("Hypothesis", "unvalidated by definition"),
                ("Missing", "no record; not evidence of absence")]

    def __init__(self):
        super().__init__()
        self.width, self.height = CONTENT_W, 282

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        W, H = self.width, self.height
        text(c, 0, H - 10, "Data-provider map and evidence flow", "Helvetica-Bold", 8.6)
        pw, ph, pg = 126, 24, 6
        top = H - 24
        mx, mw = 176, 134
        rx, rw = W - 128, 128
        # providers
        py = []
        for i, (name, what) in enumerate(self.PROVIDERS):
            y = top - (i + 1) * ph - i * pg
            py.append(y + ph / 2)
            rbox(c, 0, y, pw, ph, fill=TEAL_BG, stroke=TEAL, lw=1.0)
            text(c, 7, y + ph - 10, name, "Helvetica-Bold", 7.4)
            text(c, 7, y + 5, what, "Helvetica", 6.6, MUTED)
        # middle evidence layer
        mtop, mbot = top, top - 8 * ph - 7 * pg
        rbox(c, mx, mbot + 62, mw, mtop - mbot - 62, fill=PAPER, stroke=INK, lw=1.1)
        text(c, mx + mw / 2, mtop - 12, "MOLECULAB evidence layer", "Helvetica-Bold", 8, INK, "center")
        inner = [("Local HTTP cache", "repeat requests served locally"),
                 ("Provenance on every response", "provider, release, time, payload hash"),
                 ("Status label on every value", "colour + symbol + word"),
                 ("Failure shown, never filled", "provider down = unavailable")]
        iy = mtop - 22
        for t1, t2 in inner:
            rbox(c, mx + 8, iy - 34, mw - 16, 34, fill=colors.white, stroke=RULE)
            text(c, mx + 13, iy - 13, t1, "Helvetica-Bold", 6.9)
            wrapped(c, mx + 13, iy - 22, mw - 26, t2, "Helvetica", 6.4, MUTED, 7.4)
            iy -= 40
        # verification gate under
        gy = mbot
        rbox(c, mx, gy, mw, 54, fill=TEAL_BG, stroke=TEAL, lw=1.1)
        text(c, mx + mw / 2, gy + 41, "Verification gate", "Helvetica-Bold", 7.6, INK, "center")
        wrapped(c, mx + 8, gy + 30, mw - 16, "Drafted claim -> checked against cited source excerpt -> supported, or rejected and shown separately.", "Helvetica", 6.5, INK, 7.7)
        # statuses
        sh_, sg = 33, 8
        sy_list = []
        for i, (name, what) in enumerate(self.STATUSES):
            y = top - (i + 1) * sh_ - i * sg
            sy_list.append(y + sh_ / 2)
            rbox(c, rx, y, rw, sh_, fill=colors.white, stroke=INK, lw=0.9)
            text(c, rx + 8, y + sh_ - 13, name, "Helvetica-Bold", 7.6)
            wrapped(c, rx + 8, y + 12, rw - 14, what, "Helvetica", 6.5, MUTED, 7.4)
        # researcher decision
        dy = mbot
        rbox(c, rx, dy, rw, 40, fill=NAVY_BG, stroke=INK, lw=1.0)
        text(c, rx + rw / 2, dy + 25, "Researcher decision", "Helvetica-Bold", 7.6, INK, "center")
        text(c, rx + rw / 2, dy + 12, "and laboratory validation", "Helvetica", 6.8, INK, "center")
        # arrows providers -> layer
        for i, y in enumerate(py):
            ty = mtop - 22 - (mtop - 22 - (mbot + 70)) * i / 7.0
            arrow(c, pw + 1, y, mx - 1, ty, TEAL, 0.9)
        # layer -> statuses
        for i, y in enumerate(sy_list):
            sy_src = mtop - 22 - (mtop - 22 - (mbot + 70)) * i / 4.0
            arrow(c, mx + mw + 1, sy_src, rx - 1, y, INK, 0.9)
        arrow(c, mx + mw + 1, mbot + 27, rx - 1, dy + 20, INK, 0.9)
        text(c, 0, 4, "All eight provider clients exist in code (app/clients). Drug-regulatory approval is never inferred from presence in a provider.",
             "Helvetica", 6.3, MUTED)


class Pic50Chart(Flowable):
    def __init__(self, vals):
        super().__init__()
        self.vals = vals
        self.width, self.height = CONTENT_W, 186

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        vals = self.vals
        lo, hi, step = 4.0, 11.25, 0.25
        n = int((hi - lo) / step)
        counts = [0] * n
        for v in vals:
            k = min(n - 1, max(0, int((v - lo) / step)))
            counts[k] += 1
        mx = -(-max(counts) // 200) * 200
        left, bot, top = 38, 30, self.height - 26
        pw = self.width - left - 8
        ph = top - bot
        text(c, 0, self.height - 10, "Distribution of measured JAK1 potency (pIC50), %s compounds" % f"{len(vals):,}", "Helvetica-Bold", 8.6)
        for t in range(0, 5):
            yv = bot + ph * t / 4
            c.saveState()
            c.setStrokeColor(RULE)
            c.setLineWidth(0.4)
            c.line(left, yv, left + pw, yv)
            c.restoreState()
            text(c, left - 4, yv - 2.4, str(round(mx * t / 4)), "Helvetica", 6.4, MUTED, "right")
        bw = pw / n
        for i, k in enumerate(counts):
            h = ph * k / mx
            c.saveState()
            c.setFillColor(TEAL)
            c.rect(left + i * bw + 0.6, bot, bw - 1.2, h, stroke=0, fill=1)
            c.restoreState()
        c.saveState()
        c.setStrokeColor(INK)
        c.setLineWidth(0.8)
        c.line(left, bot, left + pw, bot)
        c.restoreState()
        for v in range(4, 12):
            x = left + (v - lo) / (hi - lo) * pw
            text(c, x, bot - 10, str(v), "Helvetica", 6.6, INK, "center")
        med = statistics.median(vals)
        xm = left + (med - lo) / (hi - lo) * pw
        c.saveState()
        c.setStrokeColor(AMBER)
        c.setDash(3, 2)
        c.setLineWidth(1.1)
        c.line(xm, bot, xm, top)
        c.restoreState()
        text(c, xm + 4, top + 4, "median %.2f" % med, "Helvetica-Bold", 7, AMBER)
        text(c, left + pw / 2, 6, "pIC50 (-log10 molar IC50)", "Helvetica", 6.8, MUTED, "center")
        c.saveState()
        c.translate(9, bot + ph / 2)
        c.rotate(90)
        c.setFont("Helvetica", 6.8)
        c.setFillColor(MUTED)
        c.drawCentredString(0, 0, "compounds")
        c.restoreState()


class CorpusChart(Flowable):
    def __init__(self, corpus):
        super().__init__()
        self.corpus = corpus
        self.width, self.height = CONTENT_W, 128

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        d = self.corpus
        t = d["totals"]
        text(c, 0, self.height - 10, "Evidence corpus in the demonstration database: %d records (%d plants, %d compounds, %d publications)"
             % (t["evidence_record"], t["plant"], t["compound"], t["publication"]), "Helvetica-Bold", 8.6)
        half = (self.width - 20) / 2

        def bars(x0, title, rows, color):
            text(c, x0, self.height - 28, title, "Helvetica-Bold", 7.4, MUTED)
            mx = max(v for _, v in rows) or 1
            lab_w = 112
            y = self.height - 44
            for name, v in rows:
                label = name.replace("_", " ")
                text(c, x0, y + 1.5, label, "Helvetica", 6.8, INK)
                bwid = (half - lab_w - 22) * v / mx
                c.saveState()
                c.setFillColor(color)
                c.rect(x0 + lab_w, y - 1, bwid, 10, stroke=0, fill=1)
                c.restoreState()
                text(c, x0 + lab_w + bwid + 4, y + 1.5, str(v), "Helvetica-Bold", 6.8, INK)
                y -= 17

        bars(0, "By evidence type (kept separate, never merged into one score)", d["types"], TEAL)
        bars(half + 20, "By plant", d["plants"], INK)


class Note(Flowable):
    def __init__(self, msg):
        super().__init__()
        self.msg = msg
        self.width, self.height = CONTENT_W, 34

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        rbox(self.canv, 0, 0, self.width, self.height, fill=AMBER_BG, stroke=AMBER, dash=(2, 2))
        wrapped(self.canv, 8, self.height - 13, self.width - 16, self.msg, "Helvetica", 7.6, AMBER, 9.4)


# ---------------------------------------------------------------- markdown -> flowables
def table_flow(rows: list[list[str]]):
    header, body = rows[0], rows[1:]
    ncol = len(header)
    presets = {3: [0.19, 0.55, 0.26], 4: [0.2, 0.3, 0.3, 0.2]}
    h0 = header[0].lower()
    if h0 == "layer":
        fr = [0.17, 0.57, 0.26]
    elif h0 == "option":
        fr = [0.19, 0.46, 0.35]
    elif h0 == "area":
        fr = [0.13, 0.46, 0.41]
    else:
        fr = presets.get(ncol, [1 / ncol] * ncol)
    data = [[Paragraph(inline(h), CELLH) for h in header]] + [[Paragraph(inline(c), CELL) for c in r] for r in body]
    t = Table(data, colWidths=[CONTENT_W * f for f in fr], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [t, Spacer(1, 8)]


def build_story(md: str):
    story, cover = [], None
    lines = md.splitlines()
    i, para = 0, []
    in_cover = False
    cover_items: list[tuple[str, str]] = []

    def flush():
        nonlocal para
        if para:
            txt = " ".join(para)
            if in_cover:
                cover_items.append(("p", txt))
            else:
                story.append(Paragraph(inline(txt), BODY))
            para = []

    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        m = re.match(r"<!--\s*(.+?)\s*-->", s)
        if m:
            flush()
            key = m.group(1)
            if key == "cover":
                in_cover = True
            elif key == "pagebreak":
                if in_cover:
                    in_cover = False
                    story.append(("COVER", cover_items))
                    story.append(NextPageTemplate("body"))
                story.append(PageBreak())
            elif key == "diagram:arch":
                story += [Spacer(1, 4), KeepTogether([ArchDiagram(), Paragraph(
                    "Figure 1. Product architecture. Teal solid boxes exist in code today; amber dashed elements are proposed and not built.", CAPTION)])]
            elif key == "diagram:evidence":
                story += [Spacer(1, 4), KeepTogether([EvidenceDiagram(), Paragraph(
                    "Figure 2. Data-provider map and evidence flow. Provenance and a status label attach to every value on its way to the researcher.", CAPTION)])]
            elif key == "chart:pic50":
                vals = load_pic50()
                if vals:
                    story += [Spacer(1, 4), KeepTogether([Pic50Chart(vals), Paragraph(
                        "Figure 4. Source: research_artifacts/jak1_curated.json, ChEMBL release 37, target CHEMBL2835 (human JAK1), retrieved 2026-11-17. "
                        "Experimental IC50 values converted to pIC50 (derived arithmetic). Binding assays only; replicate values aggregated by median.", CAPTION)])]
                else:
                    story.append(Note("Chart unavailable: research_artifacts/jak1_curated.json was not found."))
            elif key == "chart:corpus":
                corp = load_corpus()
                if corp:
                    story += [Spacer(1, 4), KeepTogether([CorpusChart(corp), Paragraph(
                        "Figure 3. Source: moleculab.db opened read-only. Excerpt-verified records curated from abstracts; a small demonstration corpus, not a literature index.", CAPTION)])]
                else:
                    story.append(Note("Chart unavailable: moleculab.db could not be read."))
            i += 1
            continue
        if not s:
            flush(); i += 1; continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if story and isinstance(story[-1], Paragraph):
                story[-1].keepWithNext = 1
            story += table_flow(rows)
            continue
        if in_cover:
            flush()
            if s.startswith("## "):
                cover_items.append(("sub", s[3:]))
            elif s.startswith("# "):
                cover_items.append(("title", s[2:]))
            else:
                para.append(s)
            i += 1
            continue
        if s.startswith("## "):
            flush()
            title = s[3:]
            story.append(Paragraph(inline(title), H2))
            hr = HRFlowable(width="100%", thickness=1.6, color=TEAL, spaceBefore=0, spaceAfter=7)
            hr.keepWithNext = 1
            story.append(hr)
        elif s.startswith("### "):
            flush(); story.append(Paragraph(inline(s[4:]), H3))
        elif s.startswith("- "):
            flush(); story.append(Paragraph(inline(s[2:]), BULLET, bulletText="•"))
        elif re.match(r"\d+\.\s", s):
            flush()
            num, rest = s.split(". ", 1)
            if story and isinstance(story[-1], Paragraph) and story[-1].style.name == "body":
                story[-1].keepWithNext = 1
            story.append(Paragraph(inline(rest), BULLET, bulletText=num + "."))
        else:
            para.append(s)
        i += 1
    flush()
    return story


def draw_cover(canvas, doc):
    c = canvas
    c.saveState()
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    c.setFillColor(INK)
    c.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN, PAGE_H - 9 * mm, "BUSINESS OVERVIEW  |  PARTNERSHIP PROPOSAL")
    c.setFillColor(TEAL)
    c.rect(MARGIN, PAGE_H - 92 * mm, 26 * mm, 2.4 * mm, stroke=0, fill=1)
    # molecular motif: two fused hexagons and a chain, drawn as vectors
    import math

    def hexagon(cx, cy, r, fill=None):
        p = c.beginPath()
        for k in range(6):
            a = math.radians(60 * k + 30)
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            p.moveTo(x, y) if k == 0 else p.lineTo(x, y)
        p.close()
        c.setStrokeColor(INK)
        c.setLineWidth(1.6)
        if fill:
            c.setFillColor(fill)
        c.drawPath(p, stroke=1, fill=1 if fill else 0)

    r = 17 * mm
    cx, cy = PAGE_W - 66 * mm, 82 * mm
    hexagon(cx, cy, r, TEAL_BG)
    hexagon(cx + r * math.sqrt(3), cy, r, colors.white)
    hexagon(cx + r * math.sqrt(3) / 2, cy - 1.5 * r, r, NAVY_BG)
    c.setFillColor(TEAL)
    for k, (dx, dy) in enumerate([(0, r), (r * math.sqrt(3), r), (r * math.sqrt(3) * 1.5, -0.5 * r)]):
        c.circle(cx + dx * (1 if k != 2 else 1), cy + dy, 2.2 * mm, stroke=0, fill=1)
    # footer band
    c.setFillColor(INK)
    c.rect(0, 0, PAGE_W, 22 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, 12 * mm, "Working prototype. No revenue, customer, partnership or market figures are claimed in this document.")
    c.drawString(MARGIN, 7.5 * mm, "Capabilities are labelled IMPLEMENTED or PROPOSED.")
    c.restoreState()


def draw_body(canvas, doc):
    c = canvas
    c.saveState()
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(MARGIN, 16 * mm, PAGE_W - MARGIN, 16 * mm)
    c.setFont("Helvetica", 7.4)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, 11.5 * mm, "MOLECULAB  |  Business overview  |  2026-11-18")
    c.drawRightString(PAGE_W - MARGIN, 11.5 * mm, "Page %d" % doc.page)
    c.setFillColor(TEAL)
    c.rect(MARGIN, PAGE_H - 13 * mm, 14 * mm, 1.6 * mm, stroke=0, fill=1)
    c.restoreState()


def cover_flowables(items):
    t_style = ParagraphStyle("ct", fontName="Helvetica-Bold", fontSize=54, leading=58, textColor=INK)
    s_style = ParagraphStyle("cs", fontName="Helvetica", fontSize=17, leading=23, textColor=INK, spaceBefore=4)
    p_style = ParagraphStyle("cp", fontName="Helvetica", fontSize=10, leading=14.5, textColor=MUTED, spaceBefore=9)
    out = [Spacer(1, 88 * mm)]
    for kind, txt in items:
        if kind == "title":
            out.append(Paragraph(inline(txt), t_style))
        elif kind == "sub":
            out.append(Paragraph(inline(txt), s_style))
        else:
            out.append(Paragraph(inline(txt), p_style))
    return out


def main() -> int:
    if not SRC.exists():
        print("source not found:", SRC)
        return 1
    story = build_story(SRC.read_text(encoding="utf-8"))
    final = []
    for item in story:
        if isinstance(item, tuple) and item[0] == "COVER":
            final += cover_flowables(item[1])
        else:
            final.append(item)
    doc = BaseDocTemplate(str(OUT), pagesize=A4, title="MOLECULAB Business Overview", author="MOLECULAB project",
                          subject="Business overview and partnership proposal", leftMargin=MARGIN,
                          rightMargin=MARGIN, topMargin=22 * mm, bottomMargin=22 * mm)
    cover_frame = Frame(MARGIN, 26 * mm, CONTENT_W, PAGE_H - 26 * mm - 26 * mm, id="cover", leftPadding=0,
                        rightPadding=0, topPadding=0, bottomPadding=0)
    body_frame = Frame(MARGIN, 22 * mm, CONTENT_W, PAGE_H - 44 * mm, id="body", leftPadding=0, rightPadding=0,
                       topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="cover", frames=[cover_frame], onPage=draw_cover),
                          PageTemplate(id="body", frames=[body_frame], onPage=draw_body)])
    doc.build(final)
    print("wrote", OUT, OUT.stat().st_size, "bytes,", doc.page, "pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
