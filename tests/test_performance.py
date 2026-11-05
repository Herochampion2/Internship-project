"""Scaling checks for the logic engine (report section 4.7.2, Table 4.10).

These assert a *bound* rather than a wall-clock target, so they stay meaningful on
a slow laptop and on CI: the per-record cost must not blow up as the register
grows, which is what the report's near-linear measurement demonstrated.
"""

from __future__ import annotations

import io
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from fleet_compliance import pipeline

#: Microseconds per record allowed at 1,000 rows before the test fails.
PER_RECORD_BUDGET_MICROSECONDS = 100.0

#: The ratio between the two measured sizes that still counts as "near-linear".
LINEARITY_FACTOR_TOLERANCE = 3.0


def _synthetic_register(rows: int, seed: int = 7) -> pd.DataFrame:
    """Build an already-sanitized frame of ``rows`` ISO-dated records."""
    rng = np.random.default_rng(seed)
    today = datetime.now().date()
    offsets = rng.integers(-60, 400, size=rows)
    dates = [
        (today + timedelta(days=int(offset))).strftime("%Y-%m-%d") for offset in offsets
    ]
    return pd.DataFrame(
        {
            "Vehicle_Reg": [f"DL-01-AB-{index:04d}" for index in range(rows)],
            "Document_Type": "Insurance",
            "Expiry_Date": dates,
        }
    )


def _time_engine(rows: int) -> tuple[float, int]:
    frame = _synthetic_register(rows)
    start = time.perf_counter()
    with redirect_stdout(io.StringIO()):  # keep the console report out of the clock
        enriched = pipeline.add_compliance_status(frame)
    elapsed = time.perf_counter() - start
    assert {"Days_Remaining", "Compliance_Status"} <= set(enriched.columns)
    return elapsed, rows


def test_per_record_cost_is_bounded():
    elapsed, rows = _time_engine(1_000)
    per_record = elapsed * 1e6 / rows
    assert per_record < PER_RECORD_BUDGET_MICROSECONDS, (
        f"{per_record:.1f} us/record exceeds the {PER_RECORD_BUDGET_MICROSECONDS:.0f} us budget"
    )


def test_scaling_is_near_linear():
    small, small_rows = _time_engine(1_000)
    large, large_rows = _time_engine(20_000)

    expected_ratio = large_rows / small_rows
    observed_ratio = large / small
    assert observed_ratio < expected_ratio * LINEARITY_FACTOR_TOLERANCE, (
        f"time grew {observed_ratio:.1f}x for {expected_ratio:.0f}x more records"
    )
