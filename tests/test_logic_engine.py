"""Unit-level checks for Module 3 - the temporal compliance logic engine.

Covers report test T-07 (already-expired documents) and the exact classification
rules of Table 3.6, including the inclusive seven-day boundary and the fail-safe
sentinel.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from fleet_compliance import pipeline
from tests.support import days_from_today, frame, record


def classify(expiry: object, threshold: int = 7) -> tuple[int, str]:
    """Run the logic engine over a single record and return its verdict."""
    enriched = pipeline.add_compliance_status(
        frame([record(expiry=expiry)]), critical_threshold_days=threshold
    )
    return int(enriched.loc[0, "Days_Remaining"]), enriched.loc[0, "Compliance_Status"]


def test_days_remaining_matches_the_exact_delta():
    days, _ = classify(days_from_today(30))
    assert days == 30


def test_reference_date_is_captured_once_per_run():
    """All records are measured against one reference point (section 3.6.2)."""
    source = frame([record(reg=f"DL-04-AA-{i:04d}", expiry=days_from_today(i)) for i in range(5)])

    enriched = pipeline.add_compliance_status(source)

    assert list(enriched["Days_Remaining"]) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("offset,expected", [(8, "Valid"), (7, "Critical"), (0, "Critical"), (-1, "Critical")])
def test_threshold_boundary_is_inclusive(offset: int, expected: str):
    """delta <= 7 is Critical, delta > 7 is Valid; the boundary belongs to Critical."""
    _, status = classify(days_from_today(offset))
    assert status == expected


def test_expired_documents_carry_a_negative_delta_and_are_critical():
    """T-07: a document that lapsed last month is surfaced, not skipped."""
    days, status = classify(days_from_today(-45))
    assert days == -45
    assert status == "Critical"


def test_unparseable_date_is_classified_critical_with_sentinel():
    """Fail-safe: an unverifiable record is surfaced with the -999 marker."""
    days, status = classify("not-a-date-at-all")
    assert days == pipeline.UNPARSEABLE_SENTINEL
    assert status == "Critical"


def test_custom_threshold_is_respected():
    assert classify(days_from_today(12), threshold=14)[1] == "Critical"
    assert classify(days_from_today(15), threshold=14)[1] == "Valid"


def test_datetime_values_from_excel_are_accepted():
    """The logic engine only ever sees ISO strings, but must not break on them."""
    future = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
    assert classify(future)[1] == "Valid"


def test_source_frame_is_not_mutated():
    source = frame([record(expiry=days_from_today(2))])

    enriched = pipeline.add_compliance_status(source)

    assert list(source.columns) == [
        "Vehicle_Reg",
        "Document_Type",
        "Expiry_Date",
        "Operator_Name",
        "Regional_RTO",
    ]
    assert {"Days_Remaining", "Compliance_Status"} <= set(enriched.columns)


def test_status_domain_is_closed():
    source = frame(
        [record(reg=f"DL-05-AA-{i:04d}", expiry=days_from_today(i * 3 - 9)) for i in range(12)]
    )

    enriched = pipeline.add_compliance_status(source)

    assert set(enriched["Compliance_Status"]) <= {"Critical", "Valid"}
    assert enriched["Days_Remaining"].dtype.kind in "i"  # integer column


def test_counts_reported_to_the_console(capsys):
    source = frame([record(expiry=days_from_today(3)), record(reg="DL-06-AA-1111", expiry=days_from_today(90))])

    pipeline.add_compliance_status(source)

    output = capsys.readouterr().out
    assert "Critical 1 (of which 0 already expired), Valid 1" in output
    assert datetime.now().date().isoformat() in output


def test_empty_frame_is_handled():
    enriched = pipeline.add_compliance_status(
        pd.DataFrame(columns=["Vehicle_Reg", "Document_Type", "Expiry_Date"])
    )
    assert len(enriched) == 0
    assert list(enriched["Compliance_Status"]) == []
