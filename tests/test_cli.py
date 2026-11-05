"""Checks for the command-line layer (paths, threshold and webhook switches).

The CLI is thin on purpose, but it is what an operations engineer actually types,
so the wiring is verified here: flags must reach the configuration, and a run must
produce the artefacts in the places the flags name.
"""

from __future__ import annotations

import json

import pytest

from fleet_compliance import mock_data
from fleet_compliance.cli import build_config, build_parser, run
from fleet_compliance.pipeline import PipelineConfig


def parse(*argv: str):
    return build_parser().parse_args(list(argv))


def test_defaults_match_the_report_configuration():
    config = build_config(parse())

    assert isinstance(config, PipelineConfig)
    assert config.critical_threshold_days == 7
    assert config.http_timeout_seconds == 10
    assert config.input_csv_path == "data/database_dump.csv"
    assert config.input_excel_path == "data/manual_records.xlsx"
    assert config.output_json_path == "output/clean_fleet_data.json"
    assert config.output_alert_log == "output/high_risk_alerts.txt"
    assert "lawyered" in config.operations_webhook  # endpoint declared in Table 3.10
    assert config.generate_mock_inputs is True


def test_flags_reach_the_configuration():
    args = parse(
        "--csv", "a.csv",
        "--excel", "b.xlsx",
        "--json-out", "out.json",
        "--alert-log", "alerts.txt",
        "--webhook", "http://localhost:8080/hook",
        "--threshold", "14",
        "--timeout", "3",
        "--no-mock",
    )

    config = build_config(args)

    assert (config.input_csv_path, config.input_excel_path) == ("a.csv", "b.xlsx")
    assert (config.output_json_path, config.output_alert_log) == ("out.json", "alerts.txt")
    assert config.operations_webhook == "http://localhost:8080/hook"
    assert config.critical_threshold_days == 14
    assert config.http_timeout_seconds == 3
    assert config.generate_mock_inputs is False


def test_no_webhook_clears_the_endpoint():
    assert build_config(parse("--no-webhook")).operations_webhook == ""


def test_run_with_cli_flags_produces_both_artefacts(tmp_path, post_200):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_path = data_dir / "database_dump.csv"
    excel_path = data_dir / "manual_records.xlsx"
    mock_data.write_mock_csv(str(csv_path))
    mock_data.write_mock_excel(str(excel_path))
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "--csv", str(csv_path),
            "--excel", str(excel_path),
            "--json-out", str(out_dir / "clean_fleet_data.json"),
            "--alert-log", str(out_dir / "high_risk_alerts.txt"),
            "--webhook", "http://example.invalid/hook",
        ]
    )

    assert exit_code == 0
    records = json.loads((out_dir / "clean_fleet_data.json").read_text(encoding="utf-8"))
    assert len(records) == 1187
    assert len((out_dir / "high_risk_alerts.txt").read_text(encoding="utf-8").splitlines()) == 165
    assert post_200.sent


def test_run_reports_unknown_input_directory(tmp_path, capsys):
    exit_code = run(
        [
            "--csv", str(tmp_path / "missing.csv"),
            "--excel", str(tmp_path / "missing.xlsx"),
            "--json-out", str(tmp_path / "out.json"),
            "--alert-log", str(tmp_path / "alerts.txt"),
            "--no-webhook",
            "--no-mock",
        ]
    )

    assert exit_code == 1
    assert "Ingestion failure" in capsys.readouterr().out


def test_threshold_flag_changes_the_classification(tmp_path):
    """``--threshold`` must move the boundary, not just be accepted by the parser."""
    csv_path = tmp_path / "dump.csv"
    mock_data.write_mock_csv(str(csv_path))
    excel_path = tmp_path / "absent.xlsx"  # deliberately not created; --no-mock

    def critical_count(threshold: int) -> tuple[int, int]:
        json_path = tmp_path / f"threshold-{threshold}.json"
        exit_code = run(
            [
                "--csv", str(csv_path),
                "--excel", str(excel_path),
                "--json-out", str(json_path),
                "--alert-log", str(tmp_path / f"threshold-{threshold}.txt"),
                "--threshold", str(threshold),
                "--no-webhook",
                "--no-mock",
            ]
        )
        assert exit_code == 0
        records = json.loads(json_path.read_text(encoding="utf-8"))
        flagged = sum(1 for entry in records if entry["Compliance_Status"] == "Critical")
        return len(records), flagged

    narrow_total, narrow_critical = critical_count(7)
    wide_total, wide_critical = critical_count(120)

    assert narrow_total == wide_total == mock_data.CSV_CLEAN_ROWS  # same records kept
    assert wide_critical > narrow_critical > 0


def test_invalid_threshold_is_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit):
        run(["--threshold", "not-a-number", "--no-webhook"])
