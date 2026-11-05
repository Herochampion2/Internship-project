"""Unit-level checks for Module 4 - serialization and the JSON data contract.

The contract is verified the way section 4.5 verified it by hand: record count,
field set and ordering, date format, integer days remaining and a closed status
domain.
"""

from __future__ import annotations

import json

from fleet_compliance import pipeline
from tests.support import days_from_today, frame, record


def clean_frame():
    """Return a sanitized three-record frame, as the serializer receives it."""
    source = frame(
        [
            record(reg="DL-07-AA-0001", expiry=days_from_today(3)),
            record(reg="MH-12-BB-0002", document="PUC", expiry=days_from_today(200)),
            record(reg="HR-26-CC-0003", document="Permit", expiry=days_from_today(-5)),
        ]
    )
    cleaned = pipeline.merge_and_clean(source, None)
    assert cleaned is not None
    return pipeline.add_compliance_status(cleaned)


def test_contract_written_and_readable(tmp_path):
    target = tmp_path / "clean_fleet_data.json"

    assert pipeline.export_json(clean_frame(), str(target)) is True

    records = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(records, list) and len(records) == 3


def test_contract_field_set_and_order(tmp_path):
    """Table 3.7: stable names and ordering across executions."""
    target = tmp_path / "clean_fleet_data.json"

    pipeline.export_json(clean_frame(), str(target))

    records = json.loads(target.read_text(encoding="utf-8"))
    assert list(records[0].keys()) == list(pipeline.CONTRACT_FIELDS)


def test_contract_types_and_domains(tmp_path):
    target = tmp_path / "clean_fleet_data.json"

    pipeline.export_json(clean_frame(), str(target))

    records = json.loads(target.read_text(encoding="utf-8"))
    for entry in records:
        assert isinstance(entry["Vehicle_Reg"], str)
        assert isinstance(entry["Expiry_Date"], str)
        assert len(entry["Expiry_Date"]) == 10  # YYYY-MM-DD
        assert isinstance(entry["Days_Remaining"], int)
        assert entry["Compliance_Status"] in {"Critical", "Valid"}


def test_expired_record_keeps_negative_days(tmp_path):
    target = tmp_path / "clean_fleet_data.json"

    pipeline.export_json(clean_frame(), str(target))

    records = json.loads(target.read_text(encoding="utf-8"))
    assert records[2]["Days_Remaining"] == -5
    assert records[2]["Compliance_Status"] == "Critical"


def test_source_specific_columns_are_excluded(tmp_path):
    """Extra register columns stay out of the dashboard contract."""
    source = frame([record(Source_System="TMS-Export v4.2", Remarks="quote pending")])
    enriched = pipeline.add_compliance_status(pipeline.merge_and_clean(source, None))
    target = tmp_path / "clean_fleet_data.json"

    pipeline.export_json(enriched, str(target))

    record_object = json.loads(target.read_text(encoding="utf-8"))[0]
    assert "Source_System" not in record_object
    assert "Remarks" not in record_object


def test_parent_directory_is_created(tmp_path):
    target = tmp_path / "nested" / "deeper" / "clean_fleet_data.json"

    assert pipeline.export_json(clean_frame(), str(target)) is True
    assert target.exists()


def test_export_failure_is_reported_not_raised(tmp_path):
    """A blocked output path must not abort the batch (report section 3.7.1)."""
    blocker = tmp_path / "a-file"
    blocker.write_text("blocking", encoding="utf-8")
    target = blocker / "clean_fleet_data.json"

    assert pipeline.export_json(clean_frame(), str(target)) is False
