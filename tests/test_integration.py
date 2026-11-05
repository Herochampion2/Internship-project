"""Verification tests for the synthetic dataset and the end-to-end pipeline.

These reproduce Chapter 4 of the report: the dataset composition of Table 4.1,
the attrition of Table 4.2, the classification of Table 4.4, the document-type
breakdown of Table 4.5 and the artefact checks of Table 4.6, followed by the
induced-failure paths of Table 4.8.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from fleet_compliance import mock_data, pipeline
from fleet_compliance.pipeline import PipelineConfig

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- #
# Dataset composition
# --------------------------------------------------------------------------- #


def test_row_counts_match_table_4_1(verification_dataset):
    csv_path, excel_path = verification_dataset

    assert len(pd.read_csv(csv_path)) == mock_data.CSV_ROW_COUNT == 812
    assert len(pd.read_excel(excel_path)) == mock_data.EXCEL_ROW_COUNT == 428


def test_every_seeded_placeholder_is_unparseable():
    """The dataset may only claim a date-gate rejection it really triggers."""
    coerced = pd.to_datetime(
        pd.Series(list(mock_data.UNPARSEABLE_DATES)),
        errors="coerce",
        dayfirst=True,
        format="mixed",
    )
    assert coerced.isna().all()


def test_document_type_mix_adds_up():
    totals = {key: value[0] for key, value in mock_data.DOC_TYPE_MIX.items()}

    assert sum(totals.values()) == 1187
    assert sum(value[1] for value in mock_data.DOC_TYPE_MIX.values()) == 165
    assert sum(value[2] for value in mock_data.DOC_TYPE_MIX.values()) == 37


# --------------------------------------------------------------------------- #
# End-to-end run over the verification dataset
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def full_run(verification_dataset, tmp_path_factory):
    """Execute the pipeline once over the 1,240-row dataset and keep the outputs."""
    from tests.support import RecordingPost

    recorder = RecordingPost(status_code=200)
    original_post = pipeline.requests.post
    pipeline.requests.post = recorder  # patched for the duration of this one run

    output_dir = tmp_path_factory.mktemp("output")
    csv_path, excel_path = verification_dataset
    config = PipelineConfig(
        input_csv_path=str(csv_path),
        input_excel_path=str(excel_path),
        output_json_path=str(output_dir / "clean_fleet_data.json"),
        output_alert_log=str(output_dir / "high_risk_alerts.txt"),
        operations_webhook="http://127.0.0.1:8080/v1/compliance/webhook",
        generate_mock_inputs=False,
    )

    try:
        exit_code = pipeline.main(config)
    finally:
        pipeline.requests.post = original_post

    json_path = Path(config.output_json_path)
    log_path = Path(config.output_alert_log)
    return {
        "exit_code": exit_code,
        "records": json.loads(json_path.read_text(encoding="utf-8")),
        "alert_lines": log_path.read_text(encoding="utf-8").splitlines(),
        "payload": json.loads(recorder.bodies[0].decode("utf-8")),
        "recorder": recorder,
    }


def test_pipeline_completes_cleanly(full_run):
    assert full_run["exit_code"] == 0


def test_sanitization_attrition_matches_table_4_2(full_run):
    assert len(full_run["records"]) == 1187


def test_classification_matches_table_4_4(full_run):
    frame = pd.DataFrame(full_run["records"])

    assert (frame["Compliance_Status"] == "Critical").sum() == 165
    assert (frame["Compliance_Status"] == "Valid").sum() == 1022
    assert ((frame["Days_Remaining"] >= 0) & (frame["Days_Remaining"] <= 7)).sum() == 128
    assert (frame["Days_Remaining"] < 0).sum() == 37


def test_document_type_breakdown_matches_table_4_5(full_run):
    frame = pd.DataFrame(full_run["records"])
    retained = frame["Document_Type"].value_counts().to_dict()
    critical = (
        frame[frame["Compliance_Status"] == "Critical"]["Document_Type"].value_counts().to_dict()
    )

    assert retained == {
        "Fitness": 322,
        "Insurance": 301,
        "Permit": 268,
        "PUC": 174,
        "Road Tax": 122,
    }
    assert critical == {
        "Insurance": 51,
        "Fitness": 44,
        "Permit": 33,
        "PUC": 24,
        "Road Tax": 13,
    }


def test_json_contract_validation_matches_table_4_6(full_run):
    records = full_run["records"]

    assert len(records) == 1187
    assert all(list(entry) == list(pipeline.CONTRACT_FIELDS) for entry in records)
    assert all(DATE_PATTERN.match(str(entry["Expiry_Date"])) for entry in records)
    assert all(isinstance(entry["Days_Remaining"], int) for entry in records)
    assert {entry["Compliance_Status"] for entry in records} == {"Critical", "Valid"}


def test_alert_channels_both_carried_all_critical_records(full_run):
    assert len(full_run["alert_lines"]) == 165
    assert full_run["payload"]["total_critical"] == 165
    assert len(full_run["payload"]["alerts"]) == 165
    assert len(full_run["recorder"].calls) == 1


def test_webhook_payload_size_is_in_the_reported_range(full_run):
    """Section 4.6.2 measured roughly 23 KB for 165 alert objects."""
    size_kb = len(full_run["recorder"].bodies[0]) / 1024.0
    assert 15.0 < size_kb < 60.0


# --------------------------------------------------------------------------- #
# Induced-failure paths (Table 4.8)
# --------------------------------------------------------------------------- #


def test_both_inputs_missing_aborts_with_exit_code_1(tmp_path, capsys):
    config = PipelineConfig(
        input_csv_path=str(tmp_path / "gone.csv"),
        input_excel_path=str(tmp_path / "gone.xlsx"),
        output_json_path=str(tmp_path / "out" / "clean_fleet_data.json"),
        output_alert_log=str(tmp_path / "out" / "high_risk_alerts.txt"),
        operations_webhook="",
        generate_mock_inputs=False,
    )

    assert pipeline.main(config) == 1
    assert "Ingestion failure" in capsys.readouterr().out


def test_missing_input_degrades_to_the_other_source(tmp_path, verification_dataset, post_200):
    csv_path, excel_path = verification_dataset

    config = PipelineConfig(
        input_csv_path=str(tmp_path / "deleted.csv"),
        input_excel_path=str(excel_path),
        output_json_path=str(tmp_path / "clean_fleet_data.json"),
        output_alert_log=str(tmp_path / "high_risk_alerts.txt"),
        operations_webhook="http://example.invalid/hook",
        generate_mock_inputs=False,
    )

    assert pipeline.main(config) == 0
    assert len(json.loads(Path(config.output_json_path).read_text(encoding="utf-8"))) < 1187
    assert csv_path.exists()  # the absent file was never created by the run


def test_network_failure_still_writes_both_artefacts(tmp_path, verification_dataset, post_network_error):
    csv_path, excel_path = verification_dataset
    config = PipelineConfig(
        input_csv_path=str(csv_path),
        input_excel_path=str(excel_path),
        output_json_path=str(tmp_path / "clean_fleet_data.json"),
        output_alert_log=str(tmp_path / "high_risk_alerts.txt"),
        operations_webhook="http://unreachable.invalid/hook",
        generate_mock_inputs=False,
    )

    assert pipeline.main(config) == 0
    assert Path(config.output_json_path).exists()
    assert len(Path(config.output_alert_log).read_text(encoding="utf-8").splitlines()) == 165


def test_mock_inputs_are_self_generated(tmp_path, post_200):
    """A fresh clone has no data/ directory, so main() creates the demo inputs."""
    config = PipelineConfig(
        input_csv_path=str(tmp_path / "data" / "database_dump.csv"),
        input_excel_path=str(tmp_path / "data" / "manual_records.xlsx"),
        output_json_path=str(tmp_path / "output" / "clean_fleet_data.json"),
        output_alert_log=str(tmp_path / "output" / "high_risk_alerts.txt"),
        operations_webhook="",
        generate_mock_inputs=True,
    )

    assert pipeline.main(config) == 0
    assert Path(config.input_csv_path).exists()
    assert Path(config.input_excel_path).exists()
    assert len(json.loads(Path(config.output_json_path).read_text(encoding="utf-8"))) == 1187
