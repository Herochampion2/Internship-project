"""Shared fixtures for the pipeline test-suite.

The tests are organised the way the report describes its own testing (section
3.11): unit-level checks on hand-built frames, integration runs over the full
synthetic verification dataset, and induced-failure tests that remove files,
corrupt dates and disable the network.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fleet_compliance import pipeline  # noqa: E402
from tests.support import RecordingPost  # noqa: E402


@pytest.fixture
def post_200(monkeypatch: pytest.MonkeyPatch) -> RecordingPost:
    """Patch the HTTP client to answer HTTP 200."""
    recorder = RecordingPost(status_code=200)
    monkeypatch.setattr(pipeline.requests, "post", recorder)
    return recorder


@pytest.fixture
def post_500(monkeypatch: pytest.MonkeyPatch) -> RecordingPost:
    """Patch the HTTP client to answer HTTP 500 (report test T-09)."""
    recorder = RecordingPost(status_code=500, text="internal error")
    monkeypatch.setattr(pipeline.requests, "post", recorder)
    return recorder


@pytest.fixture
def post_network_error(monkeypatch: pytest.MonkeyPatch) -> RecordingPost:
    """Patch the HTTP client to fail at the transport level (report test T-08)."""
    import requests

    recorder = RecordingPost(error=requests.exceptions.ConnectionError("unreachable"))
    monkeypatch.setattr(pipeline.requests, "post", recorder)
    return recorder


@pytest.fixture(scope="session")
def verification_dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Materialise the 1,240-row synthetic verification dataset once per session."""
    from fleet_compliance import mock_data

    directory = tmp_path_factory.mktemp("data")
    csv_path = directory / "database_dump.csv"
    excel_path = directory / "manual_records.xlsx"
    mock_data.write_mock_csv(str(csv_path), reference_date=date.today())
    mock_data.write_mock_excel(str(excel_path), reference_date=date.today())
    return csv_path, excel_path
