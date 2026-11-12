"""Unit-level checks for Module 2 - sanitization and normalization.

Covers report tests T-04 (null rejection), T-05 (placeholder elimination) and
T-06 (date normalization), plus the gate-ordering regression documented in
Chapter 4 (``dropna`` must precede ``astype(str)``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fleet_compliance import pipeline
from tests.support import days_from_today, frame, record


def test_both_sources_absent_reports_failure(capsys):
    """T-03 at the sanitizer level: no source, no dataset."""
    assert pipeline.merge_and_clean(None, None) is None
    assert "Ingestion failure" in capsys.readouterr().out


def test_single_source_proceeds(capsys):
    """T-02: with only one frame available the pipeline continues with it."""
    only_csv = frame([record(), record(reg="DL-01-AB-1299")])

    result = pipeline.merge_and_clean(only_csv, None)

    assert result is not None and len(result) == 2
    assert "CSV export alone" in capsys.readouterr().out


def test_vertical_merge_preserves_extra_columns():
    """``pd.concat`` keeps source-specific columns from either register."""
    csv_part = frame([record(Source_System="TMS-Export v4.2")])
    excel_part = frame([record(reg="HR-26-CD-0001", Remarks="quote pending")])

    merged = pipeline.merge_and_clean(csv_part, excel_part)

    assert merged is not None
    assert {"Source_System", "Remarks"} <= set(merged.columns)
    assert len(merged) == 2


def test_null_critical_rows_are_dropped_and_counted(capsys):
    """T-04: rows without Vehicle_Reg or Expiry_Date are rejected, and counted."""
    source = frame(
        [
            record(reg="DL-01-AA-0001"),
            record(reg=np.nan, document="Permit"),
            record(reg="DL-01-AA-0002", expiry=np.nan),
            record(reg="DL-01-AA-0003"),
        ]
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert list(cleaned["Vehicle_Reg"]) == ["DL-01-AA-0001", "DL-01-AA-0003"]
    assert "Gate 1 null gate        : 2 row(s) rejected" in capsys.readouterr().out


def test_nulls_are_not_turned_into_the_literal_string_nan():
    """Regression guard for the sequencing defect documented in section 4.10.

    Had ``astype(str)`` run before ``dropna``, a missing registration would have
    become the four-character string ``"nan"`` and survived the null gate.
    """
    cleaned = pipeline.merge_and_clean(frame([record(reg=np.nan), record(expiry=np.nan)]), None)

    assert cleaned is not None and cleaned.empty


def test_whitespace_and_empty_cells_rejected_after_coercion():
    """Cells the readers keep as strings are caught by the hygiene gate."""
    source = frame(
        [
            record(reg="   "),
            record(reg="DL-01-AA-0005"),
            record(document="PUC", expiry=" "),
        ]
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert list(cleaned["Vehicle_Reg"]) == ["DL-01-AA-0005"]


def test_placeholder_dates_are_dropped(capsys):
    """T-05: N/A / TBD / invalid calendar dates never reach the logic engine."""
    source = frame(
        [
            record(reg="DL-01-AA-0010", expiry="N/A"),
            record(reg="DL-01-AA-0011", expiry="TBD"),
            record(reg="DL-01-AA-0012", expiry="31-02-2026"),
            record(reg="DL-01-AA-0013", expiry=days_from_today(45)),
        ]
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert list(cleaned["Vehicle_Reg"]) == ["DL-01-AA-0013"]
    assert "Gate 3 date-coercion    : 3 row(s) rejected" in capsys.readouterr().out


def test_mixed_date_formats_are_normalised():
    """T-06 / Table 4.3: every representation collapses onto ISO-8601."""
    source = frame(
        [
            record(reg="DL-01-AA-0020", expiry="15/07/2026"),
            record(reg="DL-01-AA-0021", expiry="2026.08.10"),
            record(reg="DL-01-AA-0022", expiry="2026-07-25"),
            record(reg="DL-01-AA-0023", expiry="10-Aug-2026"),
        ]
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert list(cleaned["Expiry_Date"]) == [
        "2026-07-15",  # 15/07/2026 read as DD/MM/YYYY
        "2026-08-10",  # 2026.08.10 dotted ISO
        "2026-07-25",  # already ISO-8601
        "2026-08-10",  # 10-Aug-2026 textual month
    ]


def test_iso_dates_are_not_rotated_by_dayfirst():
    """Year-first dates stay put on every supported pandas line.

    Regression: pandas 3.x started applying ``dayfirst`` to ISO-8601 strings,
    so ``2026-11-07`` came back as 11 July 2026 and 14 records of the
    verification dataset drifted across the Critical boundary (179 instead of
    the reported 165). The gate now parses year-first layouts without the flag.
    """
    cleaned = pipeline.merge_and_clean(
        frame(
            [
                record(reg="DL-01-AA-0030", expiry="2026-11-07"),
                record(reg="DL-01-AA-0031", expiry="2026/12/01"),
            ]
        ),
        None,
    )

    assert cleaned is not None
    assert list(cleaned["Expiry_Date"]) == ["2026-11-07", "2026-12-01"]


def test_dayfirst_resolves_the_indian_convention():
    """01/02/2027 is 1 February in a DD/MM/YYYY register, not 2 January."""
    cleaned = pipeline.merge_and_clean(frame([record(expiry="01/02/2027")]), None)

    assert cleaned is not None
    assert cleaned.loc[0, "Expiry_Date"] == "2027-02-01"


def test_headers_and_cells_are_stripped():
    """'  Vehicle_Reg ' must still be recognised, ' DL-01-1234 ' compared."""
    source = pd.DataFrame(
        [
            {
                "  Vehicle_Reg ": "  DL-01-AB-1234  ",
                "Document_Type": " Insurance ",
                "Expiry_Date ": days_from_today(60),
            }
        ]
    )
    source.columns = [column.strip() for column in source.columns]

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert cleaned.loc[0, "Vehicle_Reg"] == "DL-01-AB-1234"
    assert cleaned.loc[0, "Document_Type"] == "Insurance"


def test_missing_mandatory_column_raises_key_error():
    """A register without an expiry column cannot be evaluated at all."""
    source = frame([{"Vehicle_Reg": "DL-01-AB-1234", "Document_Type": "Insurance"}])

    with pytest.raises(KeyError, match="mandatory column"):
        pipeline.merge_and_clean(source, None)


def test_yield_and_rejection_counts_are_reported(capsys):
    """Every rejection is counted, so silent gaps become visible numbers."""
    source = frame(
        [record(reg=f"DL-02-AA-{index:04d}") for index in range(9)] + [record(reg=np.nan)]
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None and len(cleaned) == 9
    assert "9 retained, 1 rejected, yield 90.0%" in capsys.readouterr().out


def test_retained_dates_are_all_iso_8601():
    """The output of the sanitizer is a hard guarantee for the logic engine."""
    source = frame(
        [record(reg=f"DL-03-AA-{index:04d}", expiry=f"{index % 28 + 1}/03/2027") for index in range(25)]
        + [record(reg=np.nan)] * 5
    )

    cleaned = pipeline.merge_and_clean(source, None)

    assert cleaned is not None
    assert len(cleaned) == 25
    assert all(isinstance(value, str) for value in cleaned["Expiry_Date"])
    assert pd.to_datetime(cleaned["Expiry_Date"], format="%Y-%m-%d").notna().all()
