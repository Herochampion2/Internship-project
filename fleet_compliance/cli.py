"""Command-line interface for the pipeline (the flags users actually type).

Kept separate from ``pipeline.py`` so the utility itself stays a plain sequence of
stages, and so ``python run_pipeline.py`` and ``python -m fleet_compliance`` behave
identically.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fleet_compliance.pipeline import PipelineConfig, main

#: Shown by ``--help``.
DESCRIPTION = (
    "Automated Fleet Compliance and Expiry Alert Pipeline: merge the CSV database "
    "dump with the Excel operations register, normalize expiry dates, classify every "
    "document against today, write the dashboard JSON contract and alert on records "
    "expiring within the criticality window."
)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for a single pipeline run."""
    parser = argparse.ArgumentParser(prog="fleet-compliance", description=DESCRIPTION)
    parser.add_argument(
        "--csv",
        help="path to the automated database dump (default: data/database_dump.csv)",
    )
    parser.add_argument(
        "--excel",
        help="path to the manual operations register (default: data/manual_records.xlsx)",
    )
    parser.add_argument(
        "--json-out",
        help="path of the dashboard data contract (default: output/clean_fleet_data.json)",
    )
    parser.add_argument(
        "--alert-log",
        help="path of the append-only alert log (default: output/high_risk_alerts.txt)",
    )
    parser.add_argument(
        "--webhook",
        help="operations webhook URL to POST Critical alerts to",
    )
    parser.add_argument(
        "--no-webhook",
        action="store_true",
        help="skip the remote dispatch; write the local artefacts only",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="criticality threshold in days, inclusive (report default: 7)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="webhook timeout in seconds (report default: 10)",
    )
    parser.add_argument(
        "--no-mock",
        action="store_true",
        help="do not generate demo inputs when a source file is missing",
    )
    return parser


def build_config(args: argparse.Namespace) -> PipelineConfig:
    """Translate parsed options into a :class:`PipelineConfig`."""
    overrides: dict[str, object] = {"generate_mock_inputs": not args.no_mock}
    for option, field_name in (
        ("csv", "input_csv_path"),
        ("excel", "input_excel_path"),
        ("json_out", "output_json_path"),
        ("alert_log", "output_alert_log"),
        ("webhook", "operations_webhook"),
        ("threshold", "critical_threshold_days"),
        ("timeout", "http_timeout_seconds"),
    ):
        value = getattr(args, option)
        if value is not None:
            overrides[field_name] = value
    if args.no_webhook:
        overrides["operations_webhook"] = ""
    return PipelineConfig(**overrides)


def run(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` (or ``sys.argv``), run one pipeline pass, return its exit code."""
    args = build_parser().parse_args(argv)
    return main(build_config(args))
