"""Self-generating synthetic verification dataset for the compliance pipeline.

The live compliance registers contain customer-identifiable data, so the
pipeline is verified against a synthesised set of the same shape, deliberately
seeded with every defect class the sanitizer is designed to absorb (report
Table 3.5). The composition below reproduces Table 4.1 - Table 4.5 of the
summer training report exactly, so a demo run prints the same figures the
report quotes.

    Source               Rows   Characteristics
    database_dump.csv     812   automated export; mostly clean ISO dates, nulls
    manual_records.xlsx   428   manual register; mixed date formats, placeholders

    Raw merged rows                      1,240
    Gate 1 - null gate rejected             36
    Gate 2 - hygiene gate rejected           3
    Gate 3 - date-coercion gate rejected    14
    Retained clean records               1,187   (yield 95.7%)

    Classification (threshold 7 days, inclusive)
        Valid                            1,022
        Critical - within 0..7 days        128
        Critical - already expired          37
        Total Critical                     165

    Retained records by document type (total / critical)
        Fitness       322 / 44     Insurance   301 / 51     Permit   268 / 33
        PUC           174 / 24     Road Tax    122 / 13

Because expiry dates are generated relative to a reference date (today, by
default), the distribution above is reproduced on every run no matter when the
utility is executed. Generation is deterministic: the same reference date always
yields byte-identical files.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta

import pandas as pd

# --------------------------------------------------------------------------- #
# Verified dataset composition (report Tables 4.1, 4.2, 4.4 and 4.5)
# --------------------------------------------------------------------------- #

#: Deterministic seed so the mock files are reproducible across machines.
RANDOM_SEED: int = 20260708

CSV_ROW_COUNT: int = 812
EXCEL_ROW_COUNT: int = 428

#: doc type -> (retained rows, critical rows, already-expired rows, max validity days)
DOC_TYPE_MIX: dict[str, tuple[int, int, int, int]] = {
    "Fitness": (322, 44, 10, 730),
    "Insurance": (301, 51, 12, 365),
    "Permit": (268, 33, 7, 1825),
    "PUC": (174, 24, 5, 180),
    "Road Tax": (122, 13, 3, 365),
}

#: Rows deliberately seeded at each sanitization gate, split across sources.
NULL_GATE_ROWS: dict[str, int] = {"csv": 10, "excel": 26}  # 36 genuine nulls
HYGIENE_GATE_ROWS: dict[str, int] = {"csv": 1, "excel": 2}  # 3 blank/whitespace ids
DATE_GATE_ROWS: dict[str, int] = {"csv": 1, "excel": 13}  # 14 unparseable dates

#: Number of clean rows contributed by the CSV export; the Excel register takes
#: the remainder so that both source totals match Table 4.1 exactly.
CSV_CLEAN_ROWS: int = CSV_ROW_COUNT - sum(
    gate["csv"] for gate in (NULL_GATE_ROWS, HYGIENE_GATE_ROWS, DATE_GATE_ROWS)
)

#: Values that the pandas readers do not treat as missing but that no date
#: parser can honour either -- exactly the residue the date-coercion gate exists
#: for. ("N/A", "None" and "nan" are consumed earlier, by the readers
#: themselves, and are therefore counted at the null gate.)
UNPARSEABLE_DATES: tuple[str, ...] = (
    "TBD",
    "Not Available",
    "unknown",
    "Pending from RTO",
    "31-02-2026",
    "13/13/2026",
    "07/2026 (month only)",
    "Expired?",
    "0",
    "to be confirmed",
    "NIL RECEIVED",
    "date entry error",
    "Aug-2026 (register cut-off)",
    "see attachment",
)

#: Placeholder cells kept in the register for documents that were never entered.
MISSING_MARKERS: tuple[str, ...] = ("N/A", "NA", "None", "nan", "NULL")

STATE_PLATES: tuple[str, ...] = ("DL", "HR", "PB", "UP", "RJ", "GJ", "MH", "KA", "TN", "TS")

OPERATORS: tuple[str, ...] = (
    "Shakambhara Logistics Pvt Ltd",
    "Ganesh Cargo Movers",
    "Speedway Fleet Services",
    "NordTrans Mobility",
    "Chetak Distribution",
    "Vayu Freight Systems",
    "Anmol Travels & Rentals",
    "Trishul Haulage",
)

RTO_NAMES: dict[str, str] = {
    "DL": "Delhi RTO",
    "HR": "Haryana RTO",
    "PB": "Punjab RTO",
    "UP": "Uttar Pradesh RTO",
    "RJ": "Rajasthan RTO",
    "GJ": "Gujarat RTO",
    "MH": "Maharashtra RTO",
    "KA": "Karnataka RTO",
    "TN": "Tamil Nadu RTO",
    "TS": "Telangana RTO",
}

CLERK_INITIALS: tuple[str, ...] = ("H.G.", "R.S.", "A.K.", "S.M.", "V.P.", "N.D.")

#: Date renderings used by the manual register. The mixed-format parser plus
#: ``dayfirst=True`` resolves every one of them (report Table 4.3).
EXCEL_DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%Y.%m.%d",
    "%d-%b-%Y",
    "%Y-%m-%d",
    "%d %B %Y",
)


# --------------------------------------------------------------------------- #
# Row construction
# --------------------------------------------------------------------------- #


def _vehicle_pool(rng: random.Random, size: int = 420) -> list[tuple[str, str, str]]:
    """Build unique ``(registration, operator, rto)`` triples for the fleet."""
    pool: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    while len(pool) < size:
        state = rng.choice(STATE_PLATES)
        series = f"{rng.randint(1, 43):02d}"
        letters = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(2))
        number = rng.randint(1000, 9999)
        registration = f"{state}-{series}-{letters}-{number}"
        if registration in seen:
            continue
        seen.add(registration)
        operator = rng.choice(OPERATORS) if rng.random() > 0.04 else ""
        rto = f"{RTO_NAMES[state]} ({state}-{series})"
        pool.append((registration, operator, rto))
    return pool


def _clean_rows(reference: date, rng: random.Random) -> list[dict[str, object]]:
    """Return the 1,187 rows that the pipeline is expected to retain.

    Expiry offsets are drawn so that each document type carries exactly the
    critical / expired split recorded in Table 4.4 and Table 4.5.
    """
    rows: list[dict[str, object]] = []
    vehicles = _vehicle_pool(rng)
    cursor = 0

    for document_type, (total, critical, expired_count, max_valid) in DOC_TYPE_MIX.items():
        window = critical - expired_count
        valid = total - critical
        offsets: list[int] = []
        offsets += [-rng.randint(1, 120) for _ in range(expired_count)]
        offsets += [rng.randint(0, 7) for _ in range(window)]
        offsets += [rng.randint(8, max(9, min(max_valid, 400))) for _ in range(valid)]
        rng.shuffle(offsets)

        for offset in offsets:
            registration, operator, rto = vehicles[cursor % len(vehicles)]
            cursor += 1
            rows.append(
                {
                    "Vehicle_Reg": registration,
                    "Document_Type": document_type,
                    "Expiry_Date": reference + timedelta(days=offset),
                    "Operator_Name": operator,
                    "Regional_RTO": rto,
                }
            )
    rng.shuffle(rows)
    return rows


def _seed_defects(
    reference: date, rng: random.Random, source: str
) -> list[dict[str, object]]:
    """Return the rows that must be rejected, one per documented defect class.

    ``source`` is ``"csv"`` or ``"excel"``; the counts come from Table 4.2.
    """
    defects: list[dict[str, object]] = []
    registration = f"{rng.choice(STATE_PLATES)}-{rng.randint(1, 43):02d}-ZZ-{rng.randint(1000, 9999)}"
    document = rng.choice(list(DOC_TYPE_MIX))

    for _ in range(NULL_GATE_ROWS[source]):
        # Genuine gaps in the register: the reader turns these cells into nulls,
        # which is what the null gate is built to catch.
        if rng.random() < 0.5:
            defects.append(
                {
                    "Vehicle_Reg": None if source == "excel" else "",
                    "Document_Type": document,
                    "Expiry_Date": reference + timedelta(days=rng.randint(8, 400)),
                    "Operator_Name": rng.choice(OPERATORS),
                    "Regional_RTO": f"{RTO_NAMES[registration[:2]]} (DL-99)",
                }
            )
        else:
            marker = rng.choice(MISSING_MARKERS) if source == "excel" else ""
            defects.append(
                {
                    "Vehicle_Reg": registration,
                    "Document_Type": document,
                    "Expiry_Date": marker,
                    "Operator_Name": rng.choice(OPERATORS),
                    "Regional_RTO": f"{RTO_NAMES[registration[:2]]} (DL-99)",
                }
            )

    for _ in range(HYGIENE_GATE_ROWS[source]):
        # Whitespace-only cells survive the readers as strings, so they are only
        # caught once the sanitizer has stripped and re-inspected them.
        defects.append(
            {
                "Vehicle_Reg": "   " if rng.random() < 0.5 else "\t",
                "Document_Type": document,
                "Expiry_Date": reference + timedelta(days=rng.randint(8, 400)),
                "Operator_Name": rng.choice(OPERATORS),
                "Regional_RTO": f"{RTO_NAMES[registration[:2]]} (DL-99)",
            }
        )

    for index in range(DATE_GATE_ROWS[source]):
        defects.append(
            {
                "Vehicle_Reg": f"{rng.choice(STATE_PLATES)}-{rng.randint(1, 43):02d}"
                f"-Q{index % 9}-{rng.randint(1000, 9999)}",
                "Document_Type": rng.choice(list(DOC_TYPE_MIX)),
                "Expiry_Date": UNPARSEABLE_DATES[index % len(UNPARSEABLE_DATES)],
                "Operator_Name": rng.choice(OPERATORS),
                "Regional_RTO": f"{RTO_NAMES[rng.choice(STATE_PLATES)]} (misc)",
            }
        )
    return defects


def _render_date(value: object, source: str, rng: random.Random) -> object:
    """Write one expiry value in the style its source uses."""
    if not isinstance(value, date):
        return value
    if source == "csv":
        # The automated export is almost uniformly ISO-8601, with a little
        # slash-separated drift that normalization has to absorb.
        return value.strftime("%Y/%m/%d") if rng.random() < 0.06 else value.isoformat()
    if rng.random() < 0.10:
        return value  # a real Excel date cell, typed as a date by openpyxl
    return value.strftime(rng.choice(EXCEL_DATE_FORMATS))


def build_frames(
    reference_date: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the mock CSV export and Excel register as DataFrames.

    Args:
        reference_date: Date that "today" is for the generated expiries. Defaults
            to the current date, which is what keeps the reported classification
            distribution true on any day the demo is run.

    Returns:
        ``(csv_frame, excel_frame)`` with 812 and 428 rows respectively.
    """
    reference = reference_date or date.today()
    rng = random.Random(RANDOM_SEED)

    clean = _clean_rows(reference, rng)
    csv_rows = clean[:CSV_CLEAN_ROWS]
    excel_rows = clean[CSV_CLEAN_ROWS:]

    csv_rows += _seed_defects(reference, rng, "csv")
    excel_rows += _seed_defects(reference, rng, "excel")
    rng.shuffle(csv_rows)
    rng.shuffle(excel_rows)

    csv_frame = pd.DataFrame(
        [
            {
                "Vehicle_Reg": row["Vehicle_Reg"],
                "Document_Type": row["Document_Type"],
                "Expiry_Date": _render_date(row["Expiry_Date"], "csv", rng),
                "Operator_Name": row["Operator_Name"],
                "Regional_RTO": row["Regional_RTO"],
                "Source_System": "TMS-Export v4.2",
            }
            for row in csv_rows
        ]
    )
    excel_frame = pd.DataFrame(
        [
            {
                "Vehicle_Reg": row["Vehicle_Reg"],
                "Document_Type": row["Document_Type"],
                "Expiry_Date": _render_date(row["Expiry_Date"], "excel", rng),
                "Operator_Name": row["Operator_Name"],
                "Regional_RTO": row["Regional_RTO"],
                "Entered_By": rng.choice(CLERK_INITIALS),
                "Remarks": rng.choice(
                    ["", "", "", "renewal quote pending", "copy awaited from driver"]
                ),
            }
            for row in excel_rows
        ]
    )
    return csv_frame, excel_frame


# --------------------------------------------------------------------------- #
# File writers
# --------------------------------------------------------------------------- #


def write_mock_csv(path: str, reference_date: date | None = None) -> int:
    """Write the mock ``database_dump.csv`` and return the number of rows."""
    csv_frame, _ = build_frames(reference_date)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    csv_frame.to_csv(path, index=False)
    return len(csv_frame)


def write_mock_excel(path: str, reference_date: date | None = None) -> int:
    """Write the mock ``manual_records.xlsx`` and return the number of rows."""
    _, excel_frame = build_frames(reference_date)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        excel_frame.to_excel(writer, index=False, sheet_name="Register")
    return len(excel_frame)


def ensure_mock_inputs(
    csv_path: str,
    excel_path: str,
    reference_date: date | None = None,
) -> None:
    """Generate whichever mock input file is still missing (report section 3.10.1).

    This keeps the utility self-testing: a fresh clone can run the whole pipeline
    without any data being supplied by hand.
    """
    if not os.path.exists(csv_path):
        rows = write_mock_csv(csv_path, reference_date)
        print(
            f"[INGEST] Mock database export generated at {csv_path} "
            f"({rows:,} rows) - delete it and drop in the real dump to go live."
        )
    if not os.path.exists(excel_path):
        rows = write_mock_excel(excel_path, reference_date)
        print(
            f"[INGEST] Mock operations register generated at {excel_path} "
            f"({rows:,} rows) - delete it and drop in the real register to go live."
        )
