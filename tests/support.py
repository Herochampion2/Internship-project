"""Helper builders shared by the test-suite (not fixtures, plain functions)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd


def days_from_today(offset: int) -> str:
    """Return an ISO-8601 date string ``offset`` days from today."""
    return (datetime.now().date() + timedelta(days=offset)).isoformat()


def frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a DataFrame from record dicts, preserving key order."""
    return pd.DataFrame(rows)


def record(
    reg: str = "DL-01-AB-1234",
    document: str = "Insurance",
    expiry: object | None = None,
    operator: str = "Speedway Fleet Services",
    rto: str = "Delhi RTO (DL-01)",
    **extra: object,
) -> dict[str, object]:
    """Return one source record with sensible defaults.

    ``expiry`` defaults to a comfortably valid date; pass a string to exercise
    the placeholder and mixed-format paths.
    """
    payload: dict[str, object] = {
        "Vehicle_Reg": reg,
        "Document_Type": document,
        "Expiry_Date": expiry if expiry is not None else days_from_today(30),
        "Operator_Name": operator,
        "Regional_RTO": rto,
    }
    payload.update(extra)
    return payload


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class RecordingPost:
    """Capture ``requests.post`` calls and reply with a canned outcome."""

    def __init__(
        self, status_code: int = 200, error: Exception | None = None, text: str = "ok"
    ) -> None:
        self.status_code = status_code
        self.error = error
        self.text = text
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.status_code, self.text)

    @property
    def sent(self) -> bool:
        return bool(self.calls)

    @property
    def bodies(self) -> list[bytes]:
        """Raw request bodies, in call order."""
        return [call["data"] for call in self.calls if isinstance(call.get("data"), bytes)]
