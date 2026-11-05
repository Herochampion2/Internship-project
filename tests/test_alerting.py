"""Unit-level checks for Module 5 - alerting and notification.

Covers report tests T-08 (endpoint unreachable) and T-09 (endpoint returns a
non-200 code), the dispatch outcome matrix of Table 3.8, and the append-only
behaviour of the local alert log.
"""

from __future__ import annotations

import json

from fleet_compliance import pipeline
from tests.support import days_from_today, frame, record


def enriched_with(expiry_values: list[object], operator: object = "Speedway Fleet Services"):
    """Sanitize + classify a hand-built register and return the enriched frame."""
    source = frame(
        [
            record(reg=f"DL-08-AA-{index:04d}", expiry=value, operator=operator)
            for index, value in enumerate(expiry_values)
        ]
    )
    cleaned = pipeline.merge_and_clean(source, None)
    assert cleaned is not None
    return pipeline.add_compliance_status(cleaned)


# --------------------------------------------------------------------------- #
# Local alert log
# --------------------------------------------------------------------------- #


def test_log_receives_one_line_per_critical_record(tmp_path):
    log_path = tmp_path / "high_risk_alerts.txt"

    appended = pipeline.append_critical_to_txt(
        enriched_with([days_from_today(2), days_from_today(-9), days_from_today(90)]),
        str(log_path),
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert appended == 2
    assert len(lines) == 2
    assert "CRITICAL" in lines[0]
    assert "Days_Remaining=2" in lines[0]
    assert "Days_Remaining=-9" in lines[1]


def test_log_is_append_only(tmp_path):
    """Re-execution appends, so history accumulates across runs (section 3.8.2)."""
    log_path = tmp_path / "high_risk_alerts.txt"
    data = enriched_with([days_from_today(1)])

    pipeline.append_critical_to_txt(data, str(log_path))
    pipeline.append_critical_to_txt(data, str(log_path))

    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 2


def test_no_critical_records_leaves_log_untouched(tmp_path, capsys):
    log_path = tmp_path / "high_risk_alerts.txt"

    result = pipeline.append_critical_to_txt(
        enriched_with([days_from_today(90)]), str(log_path)
    )

    assert result == 0
    assert not log_path.exists()
    assert "No Critical records" in capsys.readouterr().out


def test_unwritable_log_is_reported_not_raised(tmp_path, capsys):
    """An output path that cannot be opened degrades to a reported failure."""
    data = enriched_with([days_from_today(1)])

    assert pipeline.append_critical_to_txt(data, str(tmp_path)) == 0
    assert "FAILED to open" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Webhook dispatch
# --------------------------------------------------------------------------- #


def test_payload_shape_headers_and_timeout(post_200, capsys):
    ok = pipeline.dispatch_api_alert(
        enriched_with([days_from_today(4), days_from_today(-2)]),
        "http://example.invalid/webhook",
    )

    assert ok is True
    assert len(post_200.calls) == 1
    call = post_200.calls[0]
    assert call["url"] == "http://example.invalid/webhook"
    assert call["headers"] == {"Content-Type": "application/json"}
    assert call["timeout"] == pipeline.HTTP_TIMEOUT_SECONDS

    payload = json.loads(post_200.bodies[0].decode("utf-8"))
    assert payload["total_critical"] == 2
    assert "alert_timestamp" in payload
    assert len(payload["alerts"]) == 2
    assert payload["alerts"][0]["Compliance_Status"] == "Critical"
    assert payload["alerts"][0]["Days_Remaining"] in {4, -2}
    assert "HTTP 200" in capsys.readouterr().out


def test_valid_records_are_never_dispatched(post_200):
    pipeline.dispatch_api_alert(
        enriched_with([days_from_today(45), days_from_today(46)]), "http://example.invalid/hook"
    )

    assert not post_200.sent


def test_missing_operator_defaults_in_payload(post_200):
    """Table 3.4: an absent operator becomes 'Unknown RTO Carrier'."""
    pipeline.dispatch_api_alert(
        enriched_with([days_from_today(1)], operator=None), "http://example.invalid/hook"
    )

    payload = json.loads(post_200.bodies[0].decode("utf-8"))
    assert payload["alerts"][0]["Operator_Name"] == pipeline.DEFAULT_OPERATOR_NAME


def test_non_200_is_reported_and_batch_continues(post_500, capsys):
    """T-09: the status code is reported, and nothing raises."""
    result = pipeline.dispatch_api_alert(
        enriched_with([days_from_today(5)]), "http://example.invalid/hook"
    )

    assert result is False
    assert "non-200 status (500)" in capsys.readouterr().out


def test_network_error_is_reported_and_batch_continues(post_network_error, capsys):
    """T-08: an unreachable endpoint is reported, the batch still completes."""
    result = pipeline.dispatch_api_alert(
        enriched_with([days_from_today(5)]), "http://unreachable.invalid/hook"
    )

    assert result is False
    assert "network error" in capsys.readouterr().out


def test_zero_critical_records_bypasses_dispatch(post_200, capsys):
    result = pipeline.dispatch_api_alert(
        enriched_with([days_from_today(90)]), "http://example.invalid"
    )

    assert result is False
    assert not post_200.sent
    assert "dispatch bypassed" in capsys.readouterr().out


def test_unconfigured_endpoint_skips_dispatch(post_200, capsys):
    """``--no-webhook`` keeps the run fully local."""
    result = pipeline.dispatch_api_alert(enriched_with([days_from_today(1)]), "")

    assert result is False
    assert not post_200.sent
    assert "No webhook endpoint configured" in capsys.readouterr().out


def test_alert_log_survives_a_failed_dispatch(tmp_path, post_network_error):
    """The two channels are independent: one failing cannot suppress the other."""
    data = enriched_with([days_from_today(1), days_from_today(3)])
    log_path = tmp_path / "high_risk_alerts.txt"

    pipeline.append_critical_to_txt(data, str(log_path))
    pipeline.dispatch_api_alert(data, "http://unreachable.invalid/hook")

    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 2


def test_dispatch_timeout_can_be_overridden(post_200):
    """The bound is explicit, so a dead endpoint cannot hang the batch."""
    pipeline.dispatch_api_alert(
        enriched_with([days_from_today(1)]), "http://example.invalid/hook", timeout_seconds=1
    )

    assert post_200.calls[0]["timeout"] == 1
    assert pipeline.HTTP_TIMEOUT_SECONDS == 10
