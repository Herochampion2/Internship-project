"""Automated Fleet Compliance and Expiry Alert Pipeline.

Purpose
    Backend utility that ingests heterogeneous commercial-fleet compliance data
    (an automated CSV database dump plus a manual Excel operations register),
    sanitizes it into a single reconciled dataset, classifies every document
    against the current operational date, serialises the result as a JSON data
    contract for the operations dashboard and dispatches Critical alerts to a
    REST webhook.

Pipeline (three tiers, five stages, eight functions)
    ingestion      -> ``load_csv_file``, ``load_excel_file``
    sanitization   -> ``merge_and_clean``
    logic engine   -> ``add_compliance_status``
    serialization  -> ``export_json``
    alerting       -> ``append_critical_to_txt``, ``dispatch_api_alert``
    orchestration  -> ``main``

Design principles
    * Fail safe, never silent: a record that cannot be evaluated is classified
      Critical (sentinel ``-999``) and every rejection is counted and printed.
    * No single malformed value or failed network call may abort the batch.
    * The order of the sanitization gates is a correctness property: rows are
      dropped for missing identifiers *before* any type coercion.

Author      Yash Aggarwal (Enrollment No. 10015002724)
Role        Technology Intern, Backend Engineering
Organisation Lawyered (Sproutech Solutions Private Limited), Gurugram
Duration    8 June 2026 - 27 July 2026 (7 weeks)
Reference   Summer Training Report, "Automated Fleet Compliance and Expiry Alert
            Pipeline", Chapter 3 (Technical Details & Implementation).
Environment Python 3.12, pandas 2.x, openpyxl 3.x, requests 2.31
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import requests

from fleet_compliance.mock_data import ensure_mock_inputs

# --------------------------------------------------------------------------- #
# Data contract constants (report Tables 3.4, 3.6 and 3.7)
# --------------------------------------------------------------------------- #

#: Columns that must be present in every source; a missing column aborts
#: sanitization with a descriptive ``KeyError``.
MANDATORY_COLUMNS: tuple[str, ...] = ("Vehicle_Reg", "Document_Type", "Expiry_Date")

#: Columns whose absence rejects the whole record (null gate + hygiene gate).
CRITICAL_COLUMNS: tuple[str, ...] = ("Vehicle_Reg", "Expiry_Date")

#: Applied to the critical columns so that values such as "  DL-01-1234  "
#: compare correctly against each other and against the placeholder filters.
HYGIENE_COLUMNS: tuple[str, ...] = ("Vehicle_Reg", "Document_Type", "Expiry_Date")

#: The single canonical date representation produced by the sanitizer.
DATE_FORMAT: str = "%Y-%m-%d"

#: Documents with this many days (inclusive) or fewer of residual validity are
#: Critical, which gives the operations team a full working week of lead time.
CRITICAL_THRESHOLD_DAYS: int = 7

#: Fail-safe marker for a record the logic engine could not parse.
UNPARSEABLE_SENTINEL: int = -999

#: Used in the alert payload when the register carries no operator name.
DEFAULT_OPERATOR_NAME: str = "Unknown RTO Carrier"

#: Bound on every network call, so a dead endpoint cannot hang the batch.
HTTP_TIMEOUT_SECONDS: int = 10

#: Exact field set, order and typing of the JSON data contract (report Table 3.7).
#: Anything else the sources carried (export metadata, clerk initials, free-text
#: remarks) stays out of the artefact the dashboard consumes.
CONTRACT_FIELDS: tuple[str, ...] = (
    "Vehicle_Reg",
    "Document_Type",
    "Expiry_Date",
    "Operator_Name",
    "Regional_RTO",
    "Days_Remaining",
    "Compliance_Status",
)

#: Fields exported for each alert object in the webhook payload.
ALERT_FIELDS: tuple[str, ...] = (
    "Vehicle_Reg",
    "Document_Type",
    "Expiry_Date",
    "Operator_Name",
    "Regional_RTO",
    "Days_Remaining",
    "Compliance_Status",
)

#: Project root, used to resolve the relative default paths regardless of the
#: directory the utility is launched from.
PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PipelineConfig:
    """Run-time configuration of the pipeline (report Table 3.10).

    The defaults below are the values used during the training project. Every
    path may be absolute or relative to the project root.
    """

    input_csv_path: str = "data/database_dump.csv"
    input_excel_path: str = "data/manual_records.xlsx"
    output_json_path: str = "output/clean_fleet_data.json"
    output_alert_log: str = "output/high_risk_alerts.txt"
    operations_webhook: str = "https://api.lawyered.in/v1/compliance/webhook"
    critical_threshold_days: int = CRITICAL_THRESHOLD_DAYS
    http_timeout_seconds: int = HTTP_TIMEOUT_SECONDS
    generate_mock_inputs: bool = field(default=True)

    def resolved(self) -> PipelineConfig:
        """Return a copy of the configuration with absolute paths."""
        return PipelineConfig(
            input_csv_path=_resolve(self.input_csv_path),
            input_excel_path=_resolve(self.input_excel_path),
            output_json_path=_resolve(self.output_json_path),
            output_alert_log=_resolve(self.output_alert_log),
            operations_webhook=self.operations_webhook,
            critical_threshold_days=self.critical_threshold_days,
            http_timeout_seconds=self.http_timeout_seconds,
            generate_mock_inputs=self.generate_mock_inputs,
        )


def _resolve(path: str) -> str:
    """Resolve ``path`` against the project root when it is relative."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(PROJECT_ROOT, path))


# --------------------------------------------------------------------------- #
# Module 1 - Data ingestion
# --------------------------------------------------------------------------- #


def load_csv_file(file_path: str) -> pd.DataFrame | None:
    """Load the automated database export (CSV) into a DataFrame.

    Args:
        file_path: Path to ``database_dump.csv``.

    Returns:
        The raw DataFrame, or ``None`` when the file is missing or unreadable.
        Ingestion failures are reported and converted into an inspectable
        outcome instead of an unhandled exception (report section 3.4).
    """
    if not os.path.exists(file_path):
        print(f"[INGEST] CSV source not found: {file_path} -- source skipped.")
        return None
    try:
        frame = pd.read_csv(file_path)
    except Exception as exc:  # deliberate: any read-time corruption degrades to None
        print(f"[INGEST] CSV source unreadable: {file_path} ({exc}) -- source skipped.")
        return None
    print(
        f"[INGEST] Loaded CSV export      : {os.path.basename(file_path)} "
        f"({len(frame):,} rows, {len(frame.columns)} columns)"
    )
    return frame


def load_excel_file(file_path: str) -> pd.DataFrame | None:
    """Load the manual operations register (Excel ``.xlsx``) into a DataFrame.

    ``pandas`` delegates the actual workbook parsing to the ``openpyxl``
    engine, which is declared explicitly so the dependency is unambiguous.

    Args:
        file_path: Path to ``manual_records.xlsx``.

    Returns:
        The raw DataFrame, or ``None`` when the file is missing or unreadable.
    """
    if not os.path.exists(file_path):
        print(f"[INGEST] Excel source not found: {file_path} -- source skipped.")
        return None
    try:
        frame = pd.read_excel(file_path, engine="openpyxl")
    except Exception as exc:  # deliberate: any read-time corruption degrades to None
        print(f"[INGEST] Excel source unreadable: {file_path} ({exc}) -- source skipped.")
        return None
    print(
        f"[INGEST] Loaded Excel register  : {os.path.basename(file_path)} "
        f"({len(frame):,} rows, {len(frame.columns)} columns)"
    )
    return frame


# --------------------------------------------------------------------------- #
# Module 2 - Sanitization and normalization
# --------------------------------------------------------------------------- #

#: Year-first layouts: ``2026-11-07``, ``2026/08/10``, ``2026.08.10``, with an
#: optional time part so genuine Excel date cells pass through unchanged.
_YEAR_FIRST_DATE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T].*)?$")


def parse_expiry_dates(values: pd.Series) -> pd.Series:
    """Coerce a mixed-format expiry column to ``datetime64`` (``NaT`` if unparseable).

    Two passes are needed because the two conventions cannot share one flag.
    Day-first values (``15/07/2026``, ``10-Aug-2026``) need ``dayfirst=True``;
    year-first values are unambiguous and must *not* be rotated by it. pandas
    3.x applies ``dayfirst`` to year-first strings as well, turning
    ``2026-11-07`` into 11 July 2026 and ``2026.08.10`` into 8 October 2026, so
    the reference line (pandas 2.x) and the current line disagree. Parsing each
    family with its own rule makes the gate behave identically on both.

    Args:
        values: The raw ``Expiry_Date`` column, as read from either source.

    Returns:
        A ``datetime64[ns]`` series aligned to ``values``; unparseable entries
        are ``NaT`` and are rejected by the caller.
    """
    text = values.astype("string").str.strip()
    day_first = pd.to_datetime(text, errors="coerce", dayfirst=True, format="mixed")
    year_first = pd.to_datetime(text, errors="coerce", format="mixed")
    year_first_layout = text.str.match(_YEAR_FIRST_DATE).fillna(False).astype(bool)
    return year_first.where(year_first_layout.to_numpy(), day_first)


def merge_and_clean(
    df_csv: pd.DataFrame | None,
    df_excel: pd.DataFrame | None,
) -> pd.DataFrame | None:
    """Merge both sources and push them through the three sanitization gates.

    The gates run in a fixed order, and that order is itself a correctness
    property (report section 3.5.1): dropping nulls before ``astype(str)``
    prevents genuine nulls from being converted into the literal string
    ``"nan"`` and surviving the filters.

    1. null gate          -- ``dropna`` on the critical columns;
    2. hygiene gate       -- strip headers/cells, reject empty strings and the
                             ``"nan"`` artefact left behind by coercion;
    3. date-coercion gate -- ``parse_expiry_dates`` (year-first and day-first
                             layouts parsed separately) then re-emit as ``YYYY-MM-DD``.

    Args:
        df_csv: Raw CSV frame, or ``None`` when that source was unavailable.
        df_excel: Raw Excel frame, or ``None`` when that source was unavailable.

    Returns:
        The cleaned frame, or ``None`` when neither source could be ingested.

    Raises:
        KeyError: If a mandatory column is absent from the merged data.
    """
    if df_csv is None and df_excel is None:
        print("[SANITIZE] Ingestion failure: no source available. Pipeline aborted.")
        return None
    if df_excel is None:
        print("[SANITIZE] Proceeding with the CSV export alone (Excel source absent).")
        combined = df_csv.copy()
    elif df_csv is None:
        print("[SANITIZE] Proceeding with the Excel register alone (CSV source absent).")
        combined = df_excel.copy()
    else:
        combined = pd.concat([df_csv, df_excel], ignore_index=True)
        print(
            f"[SANITIZE] Merged both sources vertically: {len(combined):,} raw rows "
            f"({len(df_csv):,} CSV + {len(df_excel):,} Excel)."
        )

    combined.columns = combined.columns.str.strip()
    missing = [column for column in MANDATORY_COLUMNS if column not in combined.columns]
    if missing:
        raise KeyError(
            "[SANITIZE] Merged dataset is missing mandatory column(s): "
            f"{', '.join(missing)}. Available columns: {', '.join(map(str, combined.columns))}."
        )

    rows_entering = len(combined)

    # -- Gate 1: null gate (before any coercion) --------------------------- #
    cleaned = combined.dropna(subset=list(CRITICAL_COLUMNS)).copy()
    rejected_null = rows_entering - len(cleaned)

    # -- Header and cell hygiene ------------------------------------------- #
    for column in HYGIENE_COLUMNS:
        cleaned[column] = cleaned[column].astype(str).str.strip()

    # -- Gate 2: empty-string / "nan" artefact gate ------------------------ #
    rows_entering = len(cleaned)
    invalid = pd.Series(False, index=cleaned.index)
    for column in CRITICAL_COLUMNS:
        invalid |= cleaned[column].isin(["", "nan", "NaN", "None", "null"])
    cleaned = cleaned.loc[~invalid].copy()
    rejected_hygiene = rows_entering - len(cleaned)

    # -- Gate 3: date normalization ---------------------------------------- #
    rows_entering = len(cleaned)
    parsed = parse_expiry_dates(cleaned["Expiry_Date"])
    unparseable = parsed.isna()
    cleaned = cleaned.loc[~unparseable].copy()
    cleaned["Expiry_Date"] = parsed.loc[~unparseable].dt.strftime(DATE_FORMAT)
    rejected_dates = rows_entering - len(cleaned)

    total_rejected = len(combined) - len(cleaned)
    yield_pct = (100.0 * len(cleaned) / len(combined)) if len(combined) else 0.0
    print(
        f"[SANITIZE] Gate 1 null gate        : {rejected_null:,} row(s) rejected "
        f"(missing critical identifier)"
    )
    print(
        f"[SANITIZE] Gate 2 hygiene gate     : {rejected_hygiene:,} row(s) rejected "
        f"(empty string / coercion artefact)"
    )
    print(
        f"[SANITIZE] Gate 3 date-coercion    : {rejected_dates:,} row(s) rejected "
        f"(placeholder or unparseable date)"
    )
    print(
        f"[SANITIZE] Sanitization complete   : {len(cleaned):,} retained, "
        f"{total_rejected:,} rejected, yield {yield_pct:.1f}% "
        f"(all dates normalised to ISO-8601)"
    )
    return cleaned.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Module 3 - Temporal compliance logic engine
# --------------------------------------------------------------------------- #


def add_compliance_status(
    df: pd.DataFrame,
    critical_threshold_days: int = CRITICAL_THRESHOLD_DAYS,
) -> pd.DataFrame:
    """Compute ``Days_Remaining`` and ``Compliance_Status`` for every record.

    The current date is captured once, so every record in the batch is
    evaluated against the same reference point. The threshold is inclusive: a
    document expiring in exactly ``critical_threshold_days`` days is Critical,
    and already-expired documents carry a negative delta and fall under the
    same rule automatically (report Table 3.6).

    A date that fails to parse here can only originate upstream; such a record
    is classified Critical with the sentinel ``-999`` rather than skipped --
    an unverifiable record must be surfaced, never silently passed.

    Args:
        df: Cleaned frame with an ISO-8601 ``Expiry_Date`` column.
        critical_threshold_days: Days-remaining boundary at or below which a
            record is Critical.

    Returns:
        A copy of ``df`` enriched with ``Days_Remaining`` (integer) and
        ``Compliance_Status`` (``"Critical"`` / ``"Valid"``).
    """
    today = datetime.now().date()
    days_remaining: list[int] = []
    status: list[str] = []

    for raw_expiry in df["Expiry_Date"]:
        try:
            expiry = datetime.strptime(str(raw_expiry).strip(), DATE_FORMAT).date()
        except (ValueError, TypeError):
            days_remaining.append(UNPARSEABLE_SENTINEL)
            status.append("Critical")
            continue
        delta = (expiry - today).days
        days_remaining.append(delta)
        status.append("Critical" if delta <= critical_threshold_days else "Valid")

    enriched = df.copy()
    enriched["Days_Remaining"] = days_remaining
    enriched["Compliance_Status"] = status

    critical = status.count("Critical")
    expired = sum(1 for delta in days_remaining if 0 > delta != UNPARSEABLE_SENTINEL)
    print(
        f"[LOGIC]    Evaluated {len(enriched):,} record(s) against {today.isoformat()} "
        f"(threshold {critical_threshold_days} days)"
    )
    print(
        f"[LOGIC]    Classification          : Critical {critical:,} "
        f"(of which {expired:,} already expired), Valid {len(enriched) - critical:,}"
    )
    return enriched


# --------------------------------------------------------------------------- #
# Module 4 - Serialization and the JSON data contract
# --------------------------------------------------------------------------- #


def export_json(df: pd.DataFrame, output_path: str) -> bool:
    """Write the enriched dataset as the dashboard's JSON data contract.

    Only the seven contract fields (report Table 3.7) are written, in a fixed
    order, so the artefact stays small and its shape is stable across runs;
    source-specific columns carried through the sanitizer are deliberately
    dropped. ``orient="records"`` produces the record-oriented array a dashboard
    consumes, and ``indent=2`` keeps it human-readable during review.

    Args:
        df: Enriched frame to serialise.
        output_path: Destination file (parent directories are created).

    Returns:
        ``True`` when the artefact was written, ``False`` when serialization
        failed -- in which case the orchestrator continues rather than aborts.
    """
    columns = [column for column in CONTRACT_FIELDS if column in df.columns]
    contract = df.loc[:, columns].copy()
    # Days_Remaining is part of the contract as an integer; pandas would otherwise
    # widen it to float once a source column introduced nulls.
    contract["Days_Remaining"] = contract["Days_Remaining"].astype("int64")

    try:
        _ensure_parent_dir(output_path)
        contract.to_json(output_path, orient="records", indent=2, force_ascii=False)
        size_kb = os.path.getsize(output_path) / 1024.0
    except Exception as exc:  # deliberate: a failed export must not abort the batch
        print(f"[EXPORT]   JSON export FAILED: {exc}")
        return False
    print(
        f"[EXPORT]   Wrote {len(contract):,} record object(s) -> {output_path} "
        f"({size_kb:,.1f} KB)"
    )
    return True


# --------------------------------------------------------------------------- #
# Module 5 - Alerting and notification
# --------------------------------------------------------------------------- #


def append_critical_to_txt(df: pd.DataFrame, log_path: str) -> int:
    """Append every Critical record to the local, append-only alert log.

    The flat file guarantees a durable, offline-accessible history of every
    critical detection; re-running the pipeline appends instead of overwriting.

    Args:
        df: Enriched frame.
        log_path: Destination file (parent directories are created).

    Returns:
        Number of entries appended (``0`` when nothing is critical).
    """
    critical = df[df["Compliance_Status"] == "Critical"]
    if critical.empty:
        print("[ALERT]    No Critical records -- local alert log untouched.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        (
            f"[{stamp}] CRITICAL | Vehicle_Reg={row['Vehicle_Reg']} | "
            f"Document_Type={row['Document_Type']} | "
            f"Days_Remaining={int(row['Days_Remaining'])} | "
            f"Expiry_Date={row['Expiry_Date']}"
        )
        for _, row in critical.iterrows()
    ]
    try:
        _ensure_parent_dir(log_path)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"[ALERT]    Local alert log FAILED to open {log_path}: {exc}")
        return 0
    print(f"[ALERT]    Appended {len(lines):,} critical entry/entries -> {log_path}")
    return len(lines)


def dispatch_api_alert(
    df: pd.DataFrame,
    webhook_url: str,
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS,
) -> bool:
    """POST the Critical subset to the operations webhook.

    The payload carries the alert timestamp, the total critical count and one
    alert object per Critical record. The call is bound by a timeout and every
    transport outcome (200 / non-200 / network error) is reported, so no
    dispatch result can abort the batch (report Table 3.8).

    Args:
        df: Enriched frame.
        webhook_url: Operations alert endpoint.
        timeout_seconds: Hard bound on the HTTP call.

    Returns:
        ``True`` when the endpoint answered with HTTP 200, otherwise ``False``
        (which also covers the nothing-to-dispatch case).
    """
    critical = df[df["Compliance_Status"] == "Critical"]
    if critical.empty:
        print("[ALERT]    Zero critical records -- webhook dispatch bypassed.")
        return False
    if not webhook_url:
        print(
            "[ALERT]    No webhook endpoint configured -- dispatch skipped; the "
            "local alert log still carries every entry."
        )
        return False

    payload = {
        "alert_timestamp": datetime.now().isoformat(timespec="seconds"),
        "total_critical": len(critical),
        "alerts": [
            {
                field_name: (
                    _default_operator(row.get("Operator_Name"))
                    if field_name == "Operator_Name"
                    else row.get(field_name)
                )
                for field_name in ALERT_FIELDS
            }
            for _, row in critical.iterrows()
        ],
    }
    body = json.dumps(payload, ensure_ascii=False, default=_json_fallback)

    try:
        response = requests.post(
            webhook_url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
    except requests.exceptions.RequestException as exc:
        print(
            f"[ALERT]    Webhook dispatch FAILED (network error): {type(exc).__name__}. "
            f"Batch continues; the local alert log remains the durable record."
        )
        return False

    if response.status_code == 200:
        print(
            f"[ALERT]    Webhook dispatch OK    : HTTP 200 for {len(critical):,} alert "
            f"object(s), {len(body) / 1024.0:.1f} KB payload"
        )
        return True
    print(
        f"[ALERT]    Webhook returned non-200 status ({response.status_code}) for "
        f"{len(critical):,} alert object(s). Batch continues."
    )
    return False


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def main(config: PipelineConfig | None = None) -> int:
    """Run the complete pipeline once and report what it did.

    The function creates the data and output directories when absent, declares
    the configuration, ensures the input files exist (documented mock files are
    generated in their absence so the utility is self-testing) and executes the
    five stages in order, timing each one.

    Args:
        config: Optional configuration; the training-project defaults declared
            below are used when it is omitted.

    Returns:
        ``0`` when the batch completed, ``1`` when ingestion produced nothing
        usable. No other condition raises.
    """
    # -- Configuration (report Table 3.10) --------------------------------- #
    if config is None:
        config = PipelineConfig(
            input_csv_path="data/database_dump.csv",
            input_excel_path="data/manual_records.xlsx",
            output_json_path="output/clean_fleet_data.json",
            output_alert_log="output/high_risk_alerts.txt",
            operations_webhook="https://api.lawyered.in/v1/compliance/webhook",
            critical_threshold_days=7,
            http_timeout_seconds=10,
            generate_mock_inputs=True,
        )
    config = config.resolved()

    print("=" * 78)
    print("AUTOMATED FLEET COMPLIANCE AND EXPIRY ALERT PIPELINE")
    print("Lawyered (Sproutech Solutions Pvt. Ltd.) -- backend utility")
    print("=" * 78)

    os.makedirs(os.path.dirname(config.input_csv_path), exist_ok=True)
    os.makedirs(os.path.dirname(config.output_json_path), exist_ok=True)

    profile: list[tuple[str, float]] = []

    # -- Stage 1: ingestion ------------------------------------------------ #
    if config.generate_mock_inputs:
        # One-off scaffolding for a fresh clone; kept outside the measured
        # stages so the profile below reflects pipeline work only.
        ensure_mock_inputs(config.input_csv_path, config.input_excel_path)
    start = time.perf_counter()
    df_csv = load_csv_file(config.input_csv_path)
    df_excel = load_excel_file(config.input_excel_path)
    profile.append(("Ingestion", time.perf_counter() - start))

    # -- Stage 2: sanitization and normalization --------------------------- #
    start = time.perf_counter()
    try:
        clean = merge_and_clean(df_csv, df_excel)
    except KeyError as exc:
        print(f"[FATAL]    {exc}")
        return 1
    profile.append(("Sanitization", time.perf_counter() - start))
    if clean is None:
        return 1

    # -- Stage 3: temporal compliance logic -------------------------------- #
    start = time.perf_counter()
    enriched = add_compliance_status(clean, config.critical_threshold_days)
    profile.append(("Logic engine", time.perf_counter() - start))

    # -- Stage 4: serialization -------------------------------------------- #
    start = time.perf_counter()
    export_ok = export_json(enriched, config.output_json_path)
    profile.append(("Serialization", time.perf_counter() - start))

    # -- Stage 5: alerting (two independent channels) ---------------------- #
    start = time.perf_counter()
    appended = append_critical_to_txt(enriched, config.output_alert_log)
    dispatched = dispatch_api_alert(
        enriched, config.operations_webhook, config.http_timeout_seconds
    )
    profile.append(("Alerting", time.perf_counter() - start))

    _print_run_summary(enriched, profile, appended, dispatched, config, export_ok)
    return 0


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _ensure_parent_dir(path: str) -> None:
    """Create the parent directory of ``path`` when it does not exist yet."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _default_operator(value: object) -> str:
    """Return the operator name, or the documented default when it is absent."""
    if value is None:
        return DEFAULT_OPERATOR_NAME
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return DEFAULT_OPERATOR_NAME
    return text


def _json_fallback(value: object) -> object:
    """Serialize pandas/NumPy scalars that :mod:`json` does not know."""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    return str(value)


def _print_run_summary(
    enriched: pd.DataFrame,
    profile: list[tuple[str, float]],
    appended: int,
    dispatched: bool,
    config: PipelineConfig,
    export_ok: bool,
) -> None:
    """Print the run summary and the stage-wise wall-clock profile."""
    critical_mask = enriched["Compliance_Status"] == "Critical"
    expired = (enriched["Days_Remaining"] < 0) & (
        enriched["Days_Remaining"] != UNPARSEABLE_SENTINEL
    )
    in_window = enriched["Days_Remaining"].between(0, config.critical_threshold_days)
    total = sum(seconds for _, seconds in profile)

    print("-" * 78)
    print("RUN SUMMARY")

    def report(label: str, value: object) -> None:
        print(f"  {label:<22}: {value}")

    report("Records evaluated", f"{len(enriched):,}")
    report(f"Critical (<={config.critical_threshold_days} days)", f"{int(critical_mask.sum()):,}")
    report(
        "    within the window",
        f"{int(in_window.sum()):,} (0-{config.critical_threshold_days} days remaining)",
    )
    report("    already expired", f"{int(expired.sum()):,}")
    report("Valid", f"{int((~critical_mask).sum()):,}")
    report("JSON data contract", "written" if export_ok else "NOT written")
    report("Local alert log", f"{appended:,} new entries")
    report(
        "Webhook dispatch",
        "accepted (HTTP 200)" if dispatched else "not accepted / skipped",
    )

    print("-" * 78)
    print("STAGE-WISE EXECUTION PROFILE")
    print(f"  {'Stage':<16}{'Time (ms)':>12}{'Share':>10}")
    for stage_name, seconds in profile:
        share = (100.0 * seconds / total) if total else 0.0
        print(f"  {stage_name:<16}{seconds * 1000:>12.1f}{share:>9.0f}%")
    print(f"  {'TOTAL':<16}{total * 1000:>12.1f}{100.0:>9.0f}%")
    print("=" * 78)


if __name__ == "__main__":  # pragma: no cover - convenience execution
    raise SystemExit(main())
