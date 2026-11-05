"""Unit-level checks for Module 1 - data ingestion (report tests T-01 - T-03)."""

from __future__ import annotations

import pandas as pd

from fleet_compliance import pipeline
from tests.support import frame, record


def test_both_sources_load(tmp_path, capsys):
    """T-01: both files present -> both load, row counts are reported."""
    csv_path = tmp_path / "database_dump.csv"
    excel_path = tmp_path / "manual_records.xlsx"
    frame([record(), record(reg="DL-01-AB-1235")]).to_csv(csv_path, index=False)
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        frame([record(reg="HR-26-CD-4321")]).to_excel(writer, index=False)

    df_csv = pipeline.load_csv_file(str(csv_path))
    df_excel = pipeline.load_excel_file(str(excel_path))

    assert df_csv is not None and len(df_csv) == 2
    assert df_excel is not None and len(df_excel) == 1
    assert "rows" in capsys.readouterr().out


def test_missing_csv_returns_none(tmp_path, capsys):
    """T-02: an absent CSV is reported and converted into a ``None`` outcome."""
    assert pipeline.load_csv_file(str(tmp_path / "not-here.csv")) is None
    assert "not found" in capsys.readouterr().out


def test_missing_excel_returns_none(tmp_path, capsys):
    assert pipeline.load_excel_file(str(tmp_path / "not-here.xlsx")) is None
    assert "not found" in capsys.readouterr().out


def test_corrupt_input_degrades_to_none(tmp_path, capsys):
    """A file that exists but cannot be parsed must not raise (report Table 3.9)."""
    broken = tmp_path / "manual_records.xlsx"
    broken.write_text("this is not a workbook", encoding="utf-8")

    assert pipeline.load_excel_file(str(broken)) is None
    assert "unreadable" in capsys.readouterr().out


def test_loaders_report_row_counts(tmp_path, capsys):
    csv_path = tmp_path / "dump.csv"
    frame([record(), record(reg="DL-02-AA-0001"), record(reg="DL-03-AA-0002")]).to_csv(
        csv_path, index=False
    )

    pipeline.load_csv_file(str(csv_path))
    assert "3 rows" in capsys.readouterr().out
