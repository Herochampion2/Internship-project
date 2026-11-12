# Testing and verification

The project was tested the way Chapter 3.11 of the report describes it: unit-level
checks against small hand-built (and deliberately malformed) frames, integration
runs against the full synthetic verification dataset, and induced-failure tests that
remove files, corrupt dates and disable the network.

## Running the suite

```bash
pip install -r requirements-dev.txt
python -m pytest                     # everything (76 checks, ~1 s)
python -m pytest tests/test_sanitization.py -v
python -m pytest -k "webhook or dispatch" -v
python -m pytest -q -s                # show the pipeline's own console transcript
```

There is no `pytest.ini` gymnastics to worry about: `pyproject.toml` sets
`testpaths = ["tests"]` and the root `conftest.py` puts the project on `sys.path`.

CI (`.github/workflows/ci.yml`) runs the same two commands on every push and pull
request — `ruff check .` and `pytest -q` — across Python 3.10/3.11/3.12 and both
the pandas 2.x and pandas 3.x lines, plus an end-to-end run with no network and a
real HTTP round trip against `tools/mock_webhook_server.py`. The pandas matrix
earned its keep immediately: pandas 3.0 applies `dayfirst=True` to ISO-8601
strings, which rotated `2026-11-07` to 11 July and moved 14 records across the
Critical boundary. `parse_expiry_dates` now handles year-first and day-first
layouts separately, and `test_iso_dates_are_not_rotated_by_dayfirst` pins it.

## What is covered, and where

| Report test | Condition | Test |
|---|---|---|
| T-01 | both sources present, row counts reported | `test_ingestion.py::test_both_sources_load` |
| T-02 | CSV absent → Excel used alone | `test_ingestion.py::test_missing_csv_returns_none`, `test_sanitization.py::test_single_source_proceeds`, `test_integration.py::test_missing_input_degrades_to_the_other_source` |
| T-03 | both absent → ingestion failure reported | `test_ingestion.py::test_missing_excel_returns_none`, `test_sanitization.py::test_both_sources_absent_reports_failure`, `test_integration.py::test_both_inputs_missing_aborts_with_exit_code_1` |
| T-04 | null critical identifiers dropped and counted | `test_sanitization.py::test_null_critical_rows_are_dropped_and_counted` |
| T-05 | placeholder dates coerced to `NaT`, rows dropped | `test_sanitization.py::test_placeholder_dates_are_dropped` |
| T-06 | mixed date formats normalised | `test_sanitization.py::test_mixed_date_formats_are_normalised`, `test_dayfirst_resolves_the_indian_convention`, `test_iso_dates_are_not_rotated_by_dayfirst` |
| T-07 | already-expired → negative delta → Critical | `test_logic_engine.py::test_expired_documents_carry_a_negative_delta_and_are_critical` |
| T-08 | webhook unreachable → batch completes | `test_alerting.py::test_network_error_is_reported_and_batch_continues`, `test_integration.py::test_network_failure_still_writes_both_artefacts` |
| T-09 | webhook non-200 → batch completes | `test_alerting.py::test_non_200_is_reported_and_batch_continues` |
| — | gate ordering regression (`dropna` before coercion) | `test_sanitization.py::test_nulls_are_not_turned_into_the_literal_string_nan` |
| — | Table 3.7 contract (fields, order, types, domains) | `test_serialization.py`, `test_integration.py::test_json_contract_validation_matches_table_4_6` |
| — | Table 4.1/4.2/4.4/4.5 dataset and outcome figures | `test_integration.py::test_row_counts_match_table_4_1`, `test_sanitization_attrition_matches_table_4_2`, `test_classification_matches_table_4_4`, `test_document_type_breakdown_matches_table_4_5` |
| — | Table 3.8 dispatch matrix, payload size | `test_alerting.py`, `test_integration.py::test_webhook_payload_size_is_in_the_reported_range` |
| — | Section 4.7.2 scaling | `test_performance.py` |
| — | Table 3.10 configuration reachable from the CLI | `test_cli.py::test_defaults_match_the_report_configuration` |

Summary by file (the report's Table 3.12 recorded 9 unit / 5 integration /
6 induced-failure cases; here the same ideas are split by module):

| File | Cases | Focus |
|---|---|---|
| `test_ingestion.py` | 5 | loaders, absent files, corrupt workbook |
| `test_sanitization.py` | 14 | the three gates, gate order, schema errors |
| `test_logic_engine.py` | 14 | deltas, inclusive threshold, fail-safe sentinel |
| `test_serialization.py` | 7 | JSON contract shape, types, failure path |
| `test_alerting.py` | 13 | append-only log, dispatch matrix, payload |
| `test_integration.py` | 14 | full `main()` run **and** the induced-failure paths |
| `test_cli.py` | 7 | flag wiring, exit codes, threshold switch end to end |
| `test_performance.py` | 2 | per-record cost, near-linear scaling |
| **Total** | **76** | |

## Manual end-to-end verification

The transcription of section 4.1.2 — run the pipeline against a controlled local
endpoint so the dispatch path is exercised deterministically without touching a
real operations channel:

```bash
# terminal 1
python tools/mock_webhook_server.py --port 8080

# terminal 2
python run_pipeline.py --webhook http://127.0.0.1:8080/v1/compliance/webhook
```

Expected: `[ALERT] Webhook dispatch OK : HTTP 200 for 165 alert object(s)`, and in
terminal 1 one `POST /v1/compliance/webhook ... total_critical=165` line.

Induced failures, by hand (report Table 4.8):

```bash
python run_pipeline.py --webhook http://127.0.0.1:9/hook      # nothing listening
python tools/mock_webhook_server.py --port 8080 --status 500  # endpoint misbehaves
python tools/mock_webhook_server.py --port 8080 --hang         # exercises the 10 s timeout
python run_pipeline.py --no-mock                               # sources absent
```

In every case the JSON artefact and the alert log are still produced, and the exit
code stays `0`.

## Reproducing the report's numbers

`fleet_compliance/mock_data.py` is built so the demo dataset reproduces Chapter 4
exactly, whatever day you run it (expiry offsets are generated relative to *today*):

| Metric (report) | Expected | Produced by this repo |
|---|---|---|
| CSV rows (Table 4.1) | 812 | 812 |
| Excel rows (Table 4.1) | 428 | 428 |
| Raw merged rows | 1,240 | 1,240 |
| Gate 1 / 2 / 3 rejections (Table 4.2) | 36 / 3 / 14 | 36 / 3 / 14 |
| Retained records, yield | 1,187, 95.7 % | 1,187, 95.7 % |
| Critical / Valid (Table 4.4) | 165 / 1,022 | 165 / 1,022 |
| of which expired / in window | 37 / 128 | 37 / 128 |
| Document-type mix (Table 4.5) | 322/301/268/174/122 | identical |
| Unhandled exceptions | 0 | 0 |

Wall-clock timings (Tables 4.7 and 4.9) are machine-specific and are *not* asserted by
the tests; the run prints its own profile, and `test_performance.py` asserts bounds on
the per-record cost instead of absolute milliseconds.

## Linting

```bash
ruff check .          # configuration lives in pyproject.toml
```
