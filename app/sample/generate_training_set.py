"""Generate a labeled training set for an Azure DI **custom classifier**.

Outputs one folder per class, each containing N single-document PDFs with
varied (synthetic) data. Folder names match class labels — this is the layout
Document Intelligence Studio expects when you point a classifier project at a
storage container.

    samples/training/
      paystub/             paystub_01.pdf ... paystub_NN.pdf
      bank_statement/
      w2/
      passport/
      drivers_license/

Usage:
    python -m app.sample.generate_training_set --count 8 --out samples/training

Then upload the `training/` folder to a Storage container and point a DI
custom-classifier project at it from the DI Studio.
"""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .generate_sample_pdf import _header, _kv, _id_card, _photo_box

CLASSES = ("paystub", "bank_statement", "w2", "passport", "drivers_license")

FIRST_NAMES = ["JANE QUINN", "JOHN ALAN", "MARIA ELENA", "DAVID LEE", "PRIYA REENA",
               "SAMUEL TODD", "ANNA RUTH", "MICHAEL RAY", "OLIVIA MAE", "CARLOS A"]
LAST_NAMES  = ["BORROWER", "MARTIN", "PATEL", "OKAFOR", "NGUYEN",
               "ROSSI", "JOHNSON", "RAMIREZ", "ANDERSEN", "HASSAN"]
EMPLOYERS   = ["ACME Corporation", "Globex Industries", "Initech LLC",
               "Umbrella Health", "Stark Solutions", "Wayne Logistics"]
BANKS       = ["Lakeside Bank", "First National Trust", "Silvercrest Bank",
               "Harbor Federal", "Pinecrest Savings"]
STATES      = ["ILLINOIS", "TEXAS", "OHIO", "GEORGIA", "ARIZONA", "OREGON"]
STREETS     = ["EVERGREEN TER", "MAPLE AVE", "OAK ST", "PINE DR", "ELM CT", "BIRCH LN"]
CITIES      = [("SPRINGFIELD","IL","62704"), ("AUSTIN","TX","78701"),
               ("COLUMBUS","OH","43215"), ("ATLANTA","GA","30303"),
               ("PHOENIX","AZ","85001"), ("PORTLAND","OR","97201")]


@dataclass
class Sample:
    rng: random.Random
    first: str
    last: str
    employer: str
    bank: str
    street_no: int
    street: str
    city: str
    state_abbr: str
    zip_code: str
    state_full: str
    dl_no: str
    pp_no: str
    dob: str            # ISO yyyy-mm-dd
    pay_date: str
    gross: float
    net: float
    ytd_gross: float

    @classmethod
    def random(cls, seed: int) -> "Sample":
        r = random.Random(seed)
        first = r.choice(FIRST_NAMES); last = r.choice(LAST_NAMES)
        city, st, zp = r.choice(CITIES)
        gross = round(r.uniform(2200, 6800), 2)
        return cls(
            rng=r, first=first, last=last,
            employer=r.choice(EMPLOYERS), bank=r.choice(BANKS),
            street_no=r.randint(101, 9899), street=r.choice(STREETS),
            city=city, state_abbr=st, zip_code=zp, state_full=r.choice(STATES),
            dl_no=f"{r.choice('ABCDEFGH')}{r.randint(100,999)}-{r.randint(1000,9999)}-{r.randint(1000,9999)}",
            pp_no=f"{r.choice('XYZW')}{r.randint(10_000_000, 99_999_999)}",
            dob=f"{r.randint(1965, 2000)}-{r.randint(1,12):02d}-{r.randint(1,28):02d}",
            pay_date=f"2026-{r.randint(1,4):02d}-{r.randint(1,28):02d}",
            gross=gross, net=round(gross * 0.74, 2),
            ytd_gross=round(gross * r.randint(4, 18), 2),
        )

    @property
    def address(self) -> str:
        return f"{self.street_no} {self.street}"

    @property
    def address_line2(self) -> str:
        return f"{self.city} {self.state_abbr} {self.zip_code}"


# ---------- per-class single-page PDF builders ----------

def make_paystub(c: canvas.Canvas, s: Sample) -> None:
    _header(c, f"{s.employer} — Earnings Statement (Pay Stub)")
    _kv(c, 1*inch, 9.7*inch, "Employee:", f"{s.first.title()} {s.last.title()}")
    _kv(c, 1*inch, 9.45*inch, "Employee ID:", f"E-{s.rng.randint(10000,99999)}")
    _kv(c, 1*inch, 9.20*inch, "Pay Period:", f"{s.pay_date} (bi-weekly)")
    _kv(c, 1*inch, 8.95*inch, "Pay Date:", s.pay_date)
    _kv(c, 1*inch, 8.70*inch, "Gross Pay:", f"${s.gross:,.2f}")
    _kv(c, 1*inch, 8.45*inch, "Net Pay:",   f"${s.net:,.2f}")
    _kv(c, 1*inch, 8.20*inch, "YTD Gross:", f"${s.ytd_gross:,.2f}")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 7.7*inch, "Earnings")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 7.45*inch, f"Regular         80.00 hrs   ${s.gross/80:.2f}/hr   ${s.gross:,.2f}")
    c.drawString(1*inch, 7.25*inch, f"Overtime         0.00 hrs   ${s.gross/80*1.5:.2f}/hr   $0.00")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 6.7*inch, "Deductions")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 6.45*inch, f"Federal Tax     ${s.gross*0.12:>8.2f}")
    c.drawString(1*inch, 6.25*inch, f"Social Security ${s.gross*0.062:>8.2f}")
    c.drawString(1*inch, 6.05*inch, f"Medicare        ${s.gross*0.0145:>8.2f}")
    c.drawString(1*inch, 5.85*inch, f"401(k)          ${s.gross*0.055:>8.2f}")
    c.showPage()


def make_bank_statement(c: canvas.Canvas, s: Sample) -> None:
    _header(c, f"{s.bank} — Account Statement")
    _kv(c, 1*inch, 9.7*inch, "Account Holder:", f"{s.first.title()} {s.last.title()}")
    _kv(c, 1*inch, 9.45*inch, "Account Number:", f"****-**-{s.rng.randint(1000,9999)}")
    _kv(c, 1*inch, 9.20*inch, "Statement Period:", "2026-03-01 to 2026-03-31")
    begin = round(s.rng.uniform(2000, 18000), 2)
    end = round(begin + s.rng.uniform(-1500, 1500), 2)
    _kv(c, 1*inch, 8.95*inch, "Beginning Balance:", f"${begin:,.2f}")
    _kv(c, 1*inch, 8.70*inch, "Ending Balance:",    f"${end:,.2f}")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 8.2*inch, "Transaction History")
    c.setFont("Helvetica", 9)
    c.drawString(1*inch, 7.95*inch, "DATE          DESCRIPTION                          AMOUNT")
    y = 7.77 * inch
    descs = ["ACH DEPOSIT  PAYROLL", "POS  GROCERY MART", "ATM WITHDRAWAL  #4421",
             "ELEC BILL  CITY POWER", "MORTGAGE PMT  HOMEFIRST", "TRANSFER TO SAVINGS",
             "POS  FUEL STATION", "POS  RESTAURANT", "INTEREST CREDIT", "CHECK CARD  PHARMACY"]
    for _ in range(10):
        d = f"2026-03-{s.rng.randint(1,28):02d}"
        amt = s.rng.uniform(-2000, 3200)
        sign = "+" if amt >= 0 else "-"
        c.drawString(1*inch, y, f"{d}    {s.rng.choice(descs):<36}{sign}{abs(amt):>10,.2f}")
        y -= 0.18 * inch
    c.showPage()


def make_w2(c: canvas.Canvas, s: Sample) -> None:
    _header(c, "Form W-2 — Wage and Tax Statement (Tax Year 2025)")
    _kv(c, 1*inch, 9.7*inch, "Employee SSN:", f"***-**-{s.rng.randint(1000,9999)}")
    _kv(c, 1*inch, 9.45*inch, "Employer Identification Number:",
        f"{s.rng.randint(10,99)}-{s.rng.randint(1000000,9999999)}")
    _kv(c, 1*inch, 9.20*inch, "Employer:", s.employer)
    _kv(c, 1*inch, 8.95*inch, "Employer Address:", "100 Industrial Way, " + s.address_line2)
    _kv(c, 1*inch, 8.70*inch, "Employee:", f"{s.first.title()} {s.last.title()}")
    _kv(c, 1*inch, 8.45*inch, "Employee Address:", f"{s.address}, {s.address_line2}")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 7.9*inch, "Wage and tax information")
    c.setFont("Helvetica", 10)
    wages = round(s.gross * 26, 2)
    rows = [
        ("Box 1  Wages, tips, other compensation", f"${wages:,.2f}"),
        ("Box 2  Federal income tax withheld",      f"${wages*0.12:,.2f}"),
        ("Box 3  Social security wages",            f"${wages:,.2f}"),
        ("Box 4  Social security tax withheld",     f"${wages*0.062:,.2f}"),
        ("Box 5  Medicare wages and tips",          f"${wages:,.2f}"),
        ("Box 6  Medicare tax withheld",            f"${wages*0.0145:,.2f}"),
    ]
    y = 7.6 * inch
    for label, amt in rows:
        c.drawString(1*inch, y, f"{label:<46}{amt:>14}")
        y -= 0.22 * inch
    c.showPage()


def make_passport(c: canvas.Canvas, s: Sample) -> None:
    _header(c, "Identity Document — U.S. Passport (Image)")
    cx, cy, cw, ch = 1*inch, 4.5*inch, 6.5*inch, 4.0*inch
    _id_card(c, x=cx, y=cy, w=cw, h=ch, bg_hex="#0b3d2e")
    c.setFillColor(white); c.setFont("Helvetica-Bold", 16)
    c.drawString(cx + 0.3*inch, cy + ch - 0.45*inch, "UNITED STATES OF AMERICA")
    c.setFont("Helvetica", 10)
    c.drawString(cx + 0.3*inch, cy + ch - 0.65*inch, "PASSPORT")
    _photo_box(c, x=cx + 0.3*inch, y=cy + 0.5*inch, w=1.4*inch, h=1.9*inch)
    c.setFillColor(white)
    yr = int(s.dob[:4])
    issue_yr = max(yr + 18, 2014); exp_yr = issue_yr + 10
    fields = [
        ("Type/Type", "P"),
        ("Country Code/Code", "USA"),
        ("Passport No./No.", s.pp_no),
        ("Surname/Nom", s.last),
        ("Given Names/Prenoms", s.first),
        ("Nationality/Nationalite", "UNITED STATES OF AMERICA"),
        ("Date of Birth/Date de nais.", s.dob),
        ("Sex/Sexe", s.rng.choice(["M","F","X"])),
        ("Place of Birth/Lieu de nais.", f"{s.state_full}, U.S.A."),
        ("Date of Issue/Date deliv.", f"{issue_yr}-01-10"),
        ("Date of Expiration/Date exp.", f"{exp_yr}-01-09"),
        ("Authority/Autorite", "United States Department of State"),
    ]
    fx = cx + 2.0*inch; fy = cy + ch - 0.95*inch
    for label, value in fields:
        c.setFont("Helvetica", 7); c.drawString(fx, fy, label)
        c.setFont("Helvetica-Bold", 10); c.drawString(fx, fy - 0.14*inch, value)
        fy -= 0.30 * inch
    # MRZ band
    c.setFillColor(white)
    c.rect(cx + 0.2*inch, cy + 0.15*inch, cw - 0.4*inch, 0.55*inch, stroke=0, fill=1)
    c.setFillColor(black); c.setFont("Courier-Bold", 11)
    mrz_name = f"P<USA{s.last}<<{s.first.replace(' ','<')}<<<<<<<<<<<<<<<<<<<<<"[:46]
    c.drawString(cx + 0.3*inch, cy + 0.50*inch, mrz_name)
    c.drawString(cx + 0.3*inch, cy + 0.28*inch,
                 f"{s.pp_no}4USA{s.dob.replace('-','')[:6]}5F{exp_yr}0109<<<<<<<<<<<<<<04")
    c.setFillColor(black); c.setFont("Helvetica-Oblique", 8)
    c.drawString(1*inch, 4.2*inch, "Specimen — fictitious data, not a real travel document.")
    c.showPage()


def make_drivers_license(c: canvas.Canvas, s: Sample) -> None:
    _header(c, "Identity Document — State ID / Driver License (Image)")
    cx, cy, cw, ch = 1*inch, 5.0*inch, 6.5*inch, 3.5*inch
    _id_card(c, x=cx, y=cy, w=cw, h=ch, bg_hex="#1d4ed8")
    c.setFillColor(white); c.setFont("Helvetica-Bold", 14)
    c.drawString(cx + 0.3*inch, cy + ch - 0.40*inch, s.state_full)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(cx + 0.3*inch, cy + ch - 0.62*inch, "DRIVER LICENSE")
    _photo_box(c, x=cx + 0.3*inch, y=cy + 0.4*inch, w=1.3*inch, h=1.7*inch)
    yr = int(s.dob[:4])
    iss = f"{max(yr+16, 2018)}-03-15"; exp = f"{int(iss[:4])+6}-03-15"
    fields = [
        ("DL NO", s.dl_no), ("CLASS", s.rng.choice(["C","D","M"])),
        ("EXP", exp), ("LN", s.last), ("FN", s.first),
        ("ADDRESS", s.address), ("", s.address_line2),
        ("DOB", s.dob), ("SEX", s.rng.choice(["M","F","X"])),
        ("HGT", f"{s.rng.randint(5,6)}'-{s.rng.randint(0,11):02d}\""),
        ("EYES", s.rng.choice(["BRN","BLU","GRN","HZL"])),
        ("ISS", iss), ("END", "NONE"), ("REST", "NONE"),
    ]
    fx = cx + 1.85*inch; fy = cy + ch - 0.9*inch
    c.setFillColor(white)
    for label, value in fields:
        c.setFont("Helvetica", 7); c.drawString(fx, fy, label)
        c.setFont("Helvetica-Bold", 10); c.drawString(fx + 0.7*inch, fy, value)
        fy -= 0.18 * inch
    c.setStrokeColor(white); c.setLineWidth(0.7)
    c.line(cx + 0.3*inch, cy + 0.30*inch, cx + 1.6*inch, cy + 0.30*inch)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(cx + 0.3*inch, cy + 0.15*inch, f"{s.first.title()} {s.last.title()}")
    c.setFillColor(black); c.setFont("Helvetica-Oblique", 8)
    c.drawString(1*inch, 4.7*inch, "Specimen — fictitious data, not a real ID.")
    c.showPage()


BUILDERS = {
    "paystub": make_paystub,
    "bank_statement": make_bank_statement,
    "w2": make_w2,
    "passport": make_passport,
    "drivers_license": make_drivers_license,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8, help="PDFs per class")
    parser.add_argument("--out", type=str, default="samples/training", help="output dir")
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    for cls in CLASSES:
        cls_dir = out_root / cls
        cls_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, args.count + 1):
            sample = Sample.random(seed=hash((cls, i)) & 0xFFFFFFFF)
            path = cls_dir / f"{cls}_{i:03d}.pdf"
            c = canvas.Canvas(str(path), pagesize=LETTER)
            BUILDERS[cls](c, sample)
            c.save()
        print(f"Wrote {args.count:>3} {cls:<18} -> {cls_dir}")

    print(f"\nDone. Upload `{out_root}` to a Storage container, then in DI Studio:")
    print("  1. Create custom classification project, point it at the container.")
    print("  2. Each subfolder is auto-detected as a class.")
    print("  3. Train (~few minutes), copy the modelId.")
    print("  4. azd env set CLASSIFIER_ID <modelId> && azd deploy")


if __name__ == "__main__":
    main()
