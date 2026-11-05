# Report → code traceability

Where each numbered section and table of *Summer Training Report — Automated Fleet
Compliance and Expiry Alert Pipeline* (Yash Aggarwal, GGIPU / Lawyered, June–July
2026) is realised in this repository.

| Report | Implementation |
|---|---|
| 2.8.2 Features 1–8 | `pipeline.py`: `load_csv_file`, `load_excel_file`, `merge_and_clean`, `add_compliance_status`, `export_json`, `append_critical_to_txt`, `dispatch_api_alert`, `main` |
| 2.9 System architecture, three tiers | `docs/ARCHITECTURE.md` §1; stage boundaries inside `main()` |
| 2.9.4 Pipeline stages / data contracts (Table 2.9) | `docs/ARCHITECTURE.md` §2, `docs/DATA_CONTRACT.md` |
| 3.1.2 Software stack (Table 3.2) | `requirements.txt`, `pyproject.toml` |
| 3.3 Module-to-function mapping (Table 3.3) | `docs/ARCHITECTURE.md` §3 |
| 3.4 Data ingestion, guarded loaders, `None` outcome | `load_csv_file`, `load_excel_file` |
| 3.4.3 Source schema (Table 3.4) | `MANDATORY_COLUMNS`, `CRITICAL_COLUMNS`, `HYGIENE_COLUMNS`, `DEFAULT_OPERATOR_NAME` |
| 3.5.1 Gate order as a correctness property | `merge_and_clean` (Gate 1 before coercion) + `tests/test_sanitization.py::test_nulls_are_not_turned_into_the_literal_string_nan` |
| 3.5.2 Null-source handling and concatenation | `merge_and_clean` head (both `None`, one `None`, `pd.concat(ignore_index=True)`) |
| 3.5.3 Header and cell hygiene | `combined.columns.str.strip()` + `astype(str).str.strip()` |
| 3.5.4 Row rejection and placeholder elimination | `merge_and_clean` Gates 1 and 2 |
| 3.5.5 Date normalization | `merge_and_clean` Gate 3 (`to_datetime(errors="coerce", dayfirst=True, format="mixed")` → `strftime("%Y-%m-%d")`) |
| 3.5.6 Edge cases (Table 3.5) | covered one-by-one in `tests/test_sanitization.py` and `tests/test_ingestion.py` |
| 3.6 Temporal logic engine, single reference date | `add_compliance_status` (`datetime.now().date()` captured once) |
| 3.6.3 Classification rules (Table 3.6) | same function; `UNPARSEABLE_SENTINEL = -999` |
| 3.7 Serialization and the JSON contract | `export_json`, `CONTRACT_FIELDS` |
| 3.7.2 Output contract (Table 3.7) | `docs/DATA_CONTRACT.md` §2 |
| 3.8.2 Local alert log | `append_critical_to_txt` (append mode, timestamped lines) |
| 3.8.3 Remote webhook dispatch | `dispatch_api_alert` (`json.dumps` + `requests.post`, 10 s timeout) |
| 3.8.4 Dispatch outcome matrix (Table 3.8) | `docs/DATA_CONTRACT.md` §4; asserted in `tests/test_alerting.py` |
| 3.9 Exception handling (Table 3.9) | `try/except` in every stage function; `docs/ARCHITECTURE.md` §6 |
| 3.10.1 Orchestration, directories, self-generating mock inputs | `main()` + `mock_data.ensure_mock_inputs()` |
| 3.10.2 Configuration parameters (Table 3.10) | `PipelineConfig` (defaults identical to the table) |
| 3.11 Testing methodology (Tables 3.11, 3.12) | `tests/`, `docs/TESTING.md` |
| 3.12 Code quality: PEP 8, module docstring, banner per phase | `fleet_compliance/pipeline.py` (module docstring states purpose, author, role, duration) |
| 4.1.1 Test dataset (Table 4.1) | `fleet_compliance/mock_data.py` |
| 4.2 / 4.3 / 4.4 measured outcomes | reproduced by the generator; see `docs/TESTING.md` "Reproducing the report's numbers" |
| 4.6 Alerting verification | `tools/mock_webhook_server.py` |
| 4.7 Performance analysis | stage profile printed by `main()`; `tests/test_performance.py` |
| 5.2 Limitations (Table 5.1) | unchanged on purpose — see README "Known limitations" |
| 5.3 Future scope (Table 5.2) | README "Roadmap" keeps the five phases as the agreed next steps |

## Deliberate deviations

Nothing here contradicts the report; three points go slightly beyond it, and each is
flagged so a reviewer can map the code back to the document:

1. **`PipelineConfig` dataclass.** The report declares the configuration "at the head
   of `main()`"; here `main()` still builds that exact configuration by default, but it
   accepts an optional `PipelineConfig` so the pipeline can be run against other paths
   (that is what makes the integration tests possible without touching the repo).
2. **`CONTRACT_FIELDS` projection in `export_json`.** Table 3.7 fixes the seven fields
   of the contract, so the exporter writes exactly those and drops source-specific
   columns instead of leaking them into the artefact the dashboard consumes.
3. **`docs/`, `tests/` and `tools/`.** The report describes the utility and its test
   results; the repository adds the runnable version of that test plan plus a local
   mock webhook endpoint so the dispatch path can be verified offline.
