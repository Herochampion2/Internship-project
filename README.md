# Automated Fleet Compliance and Expiry Alert Pipeline

[![CI](https://github.com/Herochampion2/Internship-project/actions/workflows/ci.yml/badge.svg)](https://github.com/Herochampion2/Internship-project/actions/workflows/ci.yml)

A backend Python utility that stops commercial-fleet documents from lapsing silently.
It merges a messy automated database dump (`.csv`) with a messy manual operations
register (`.xlsx`), normalises every expiry date, classifies every document against
today's date, writes a clean JSON data contract for the operations dashboard, and
pushes every *Critical* record to a REST webhook **and** to a local append-only alert
log.

Built during a seven-week summer training at **Lawyered** (Sproutech Solutions Private
Limited), 8 June 2026 – 27 July 2026, as the subject of the summer training report of
the same name.

| | |
|---|---|
| **Author** | Yash Aggarwal — B.Tech CSE (2nd year), Guru Gobind Singh Indraprastha University, New Delhi |
| **Host** | Lawyered · backend engineering · mentor: Mr. Himanshu Gupta |
| **Stack** | Python 3.10+ · pandas · openpyxl · requests · stdlib `datetime` / `json` / `os` |
| **Scale handled** | 1,240 raw rows → 1,187 clean records classified in ~0.07 s; linear to 100 k+ rows |
| **License** | MIT |

---

## Contents

- [The problem it solves](#the-problem-it-solves)
- [What one run produces](#what-one-run-produces)
- [Quick start](#quick-start)
- [How to use it](#how-to-use-it)
- [Input data you must supply](#input-data-you-must-supply)
- [Outputs and contracts](#outputs-and-contracts)
- [Configuration](#configuration)
- [Testing](#testing)
- [Repository layout](#repository-layout)
- [How the pipeline works](#how-the-pipeline-works)
- [Failure handling](#failure-handling)
- [Known limitations and roadmap](#known-limitations-and-roadmap)
- [Frequently asked questions](#frequently-asked-questions)

---

## The problem it solves

India's mobility sector runs on more than 40 crore registered vehicles, and every
commercial vehicle carries its own set of statutory documents — third-party insurance,
fitness certificate, national/state permit, PUC certificate, road tax — each with its
own validity window. In most fleets the register that tracks all of it is a
spreadsheet, and expiry detection is a person comparing a date column against today.
The predictable consequences are silent lapses, fine leakage and vehicle detentions.

Every one of those failure modes is mechanical, not judgmental:

* date formats differ between sources;
* placeholder literals (`N/A`, `TBD`) sit in rows nobody finished typing;
* blank cells conflate "not applicable" with "not yet recorded";
* review frequency rises and falls with workload.

This pipeline replaces that process with a deterministic one: every retained date is
compared with the run date on every execution, anything unparseable is rejected **and
counted**, and every record inside the alerting window is dispatched to the operations
channel before the penalty is issued.

> The governing design principle: a false negative costs a detention and a penalty, a
> false positive costs an operator a few minutes. So the pipeline errs toward alerting —
> even a record whose date cannot be parsed is flagged Critical rather than skipped.

## What one run produces

Verified numbers from a full run over the bundled 1,240-row synthetic register set
(the same figures reported in Chapter 4 of the training report):

| Measure | Value |
|---|---|
| Raw rows ingested | 1,240 (812 CSV + 428 Excel) |
| Rejected, by gate | 36 null · 3 hygiene · 14 unparseable date = **53 counted** |
| Retained, clean & normalised | 1,187 (95.7 % yield) |
| Classified Critical (≤ 7 days) | **165** — 128 inside the window, 37 already expired |
| Classified Valid | 1,022 |
| `clean_fleet_data.json` | 1,187 record objects, ~0.29 MB |
| `high_risk_alerts.txt` | 165 timestamped entries appended |
| Webhook | one POST, 165 alert objects, HTTP 200 |
| Unhandled exceptions | 0 (incl. all induced-failure tests) |

---

## Quick start

```bash
git clone <your-fork-url> Internship-project
cd Internship-project
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run_pipeline.py --no-webhook
```

That is a complete, self-contained demonstration: `data/` is empty in a fresh clone, so
the pipeline generates the synthetic registers from the report, runs all five stages and
writes its artefacts to `output/`. Expected transcript (your timings will differ):

```text
==============================================================================
AUTOMATED FLEET COMPLIANCE AND EXPIRY ALERT PIPELINE
Lawyered (Sproutech Solutions Pvt. Ltd.) -- backend utility
==============================================================================
[INGEST] Mock database export generated at .../data/database_dump.csv (812 rows)
[INGEST] Mock operations register generated at .../data/manual_records.xlsx (428 rows)
[INGEST] Loaded CSV export      : database_dump.csv (812 rows, 6 columns)
[INGEST] Loaded Excel register  : manual_records.xlsx (428 rows, 7 columns)
[SANITIZE] Merged both sources vertically: 1,240 raw rows (812 CSV + 428 Excel).
[SANITIZE] Gate 1 null gate        : 36 row(s) rejected (missing critical identifier)
[SANITIZE] Gate 2 hygiene gate     : 3 row(s) rejected (empty string / coercion artefact)
[SANITIZE] Gate 3 date-coercion    : 14 row(s) rejected (placeholder or unparseable date)
[SANITIZE] Sanitization complete   : 1,187 retained, 53 rejected, yield 95.7% (all dates normalised to ISO-8601)
[LOGIC]    Evaluated 1,187 record(s) against 2026-11-05 (threshold 7 days)
[LOGIC]    Classification          : Critical 165 (of which 37 already expired), Valid 1,022
[EXPORT]   Wrote 1,187 record object(s) -> .../output/clean_fleet_data.json (292.9 KB)
[ALERT]    Appended 165 critical entry/entries -> .../output/high_risk_alerts.txt
[ALERT]    No webhook endpoint configured -- dispatch skipped; the local alert log still carries every entry.
------------------------------------------------------------------------------
RUN SUMMARY
  Records evaluated     : 1,187
  Critical (<=7 days)   : 165
      within the window : 128 (0-7 days remaining)
      already expired   : 37
  Valid                 : 1,022
  JSON data contract    : written
  Local alert log       : 165 new entries
  Webhook dispatch      : not accepted / skipped
------------------------------------------------------------------------------
STAGE-WISE EXECUTION PROFILE
  Stage              Time (ms)     Share
  Ingestion               35.0       49%
  Sanitization            13.6       19%
  Logic engine             5.5        8%
  Serialization            2.5        4%
  Alerting                14.9       21%
  TOTAL                   71.5      100%
==============================================================================
```

Then look at what it decided:

```bash
head -3 output/high_risk_alerts.txt          # the alert queue the operations team reads
python -c "import json;d=json.load(open('output/clean_fleet_data.json'));print(len(d));print(d[0])"
```

### Container (optional)

```bash
docker build -t fleet-compliance .
docker run --rm fleet-compliance                                  # the same demo, inside the image
docker run --rm -v "$PWD/output:/app/output" fleet-compliance     # …with the artefacts written back out
```

Dependencies are installed in their own layer so a code-only change rebuilds in
a second, and the process runs as an unprivileged user (`appuser`, uid 10001)
with write access to `data/` and `output/` only. The image is a convenience for
reviewers: this project has **not** run it in a team's deployment pipeline, and
the compose/registry/secret-management side of containerisation is not claimed.

---

## How to use it

### 1. Run it on your own registers (the normal case)

Put your two files where the configuration expects them, then run:

```bash
cp /path/to/database_dump.csv  data/database_dump.csv
cp /path/to/manual_records.xlsx data/manual_records.xlsx
python run_pipeline.py --webhook https://your-channel.example/hooks/compliance
```

or point the flags anywhere you like instead:

```bash
python run_pipeline.py \
  --csv   /data/tms/export-2026-07-01.csv \
  --excel /data/ops/register-july.xlsx \
  --json-out /srv/dashboard/clean_fleet_data.json \
  --alert-log /var/log/fleet/high_risk_alerts.txt \
  --webhook https://your-channel.example/hooks/compliance \
  --threshold 7 --timeout 10
```

`data/` and `output/` are created automatically and are git-ignored, so no register data
is ever committed by accident.

### 2. Command-line switches

| Switch | Default | Meaning |
|---|---|---|
| `--csv PATH` | `data/database_dump.csv` | automated database export (CSV) |
| `--excel PATH` | `data/manual_records.xlsx` | manual operations register (Excel) |
| `--json-out PATH` | `output/clean_fleet_data.json` | dashboard data contract |
| `--alert-log PATH` | `output/high_risk_alerts.txt` | append-only local alert log |
| `--webhook URL` | report endpoint | where Critical alerts are POSTed |
| `--no-webhook` | off | skip the dispatch; local artefacts only |
| `--threshold N` | `7` | "days or fewer remaining ⇒ Critical" |
| `--timeout N` | `10` | seconds bound on the webhook call |
| `--no-mock` | off | never invent demo data; fail if a source is missing |

Identical invocations: `python run_pipeline.py …`, `python -m fleet_compliance …`, and —
after `pip install -e .` — `fleet-compliance …`.

Exit codes: `0` batch completed (even when a webhook call failed — that is reported, not
fatal), `1` nothing could be ingested or the schema was unusable.

### 3. Verify the alerting path locally, offline

Chapter 4 exercised the dispatch against a controlled endpoint; `tools/mock_webhook_server.py`
is that endpoint. Nothing leaves your machine and you get to see the exact payload.

```bash
# terminal 1
python tools/mock_webhook_server.py --port 8080
# terminal 2
python run_pipeline.py --no-mock --webhook http://127.0.0.1:8080/v1/compliance/webhook
```

To rehearse the failure matrix instead of the happy path:

```bash
python tools/mock_webhook_server.py --port 8080 --status 500   # endpoint misbehaves
python tools/mock_webhook_server.py --port 8080 --hang         # client timeout fires
python run_pipeline.py --webhook http://127.0.0.1:9/nothing    # unreachable host
```

In all three cases the JSON contract and the alert log are still written and the run still
exits `0`.

### 4. Use it as a library

Each stage is importable, which is how the unit tests drive them and how you can wire the
logic engine into a larger service without the file layer:

```python
import pandas as pd
from fleet_compliance import pipeline

df_csv = pipeline.load_csv_file("data/database_dump.csv")
df_excel = pipeline.load_excel_file("data/manual_records.xlsx")

clean = pipeline.merge_and_clean(df_csv, df_excel)          # merged + ISO dates
enriched = pipeline.add_compliance_status(clean, critical_threshold_days=14)

print(enriched.query("Compliance_Status == 'Critical'")[["Vehicle_Reg", "Document_Type", "Days_Remaining"]])

pipeline.export_json(enriched, "output/clean_fleet_data.json")
pipeline.append_critical_to_txt(enriched, "output/high_risk_alerts.txt")
pipeline.dispatch_api_alert(enriched, "https://your-channel.example/hooks/compliance")
```

### 5. Schedule it

The utility is idempotent per run (the alert log is deliberately append-only, which is its
history layer), so a daily cron entry is all that production needs:

```cron
# 07:05 every day, after the TMS export lands
5 7 * * * cd /srv/fleet && /srv/fleet/.venv/bin/python run_pipeline.py \
  --webhook https://your-channel.example/hooks/compliance >> /var/log/fleet/pipeline.log 2>&1
```

---

## Input data you must supply

Two tabular files with header rows. Column names are matched after stripping whitespace.

| Column | Required | Treatment |
|---|---|---|
| `Vehicle_Reg` | **yes** | coerced to string, stripped; null/empty ⇒ row rejected |
| `Document_Type` | **yes** | coerced to string, stripped |
| `Expiry_Date` | **yes** | coerced to string, stripped, parsed leniently (`dayfirst=True`), normalized to `YYYY-MM-DD`; unparseable ⇒ row rejected |
| `Operator_Name` | no | preserved; defaulted to `Unknown RTO Carrier` in the **alert payload** when blank |
| `Regional_RTO` | no | preserved unchanged |
| anything else | no | carried through sanitization, excluded from the JSON contract |

The date parser accepts, and normalises, all of:

```text
15/11/2026   → 2026-11-15        2026.11.15   → 2026-11-15
15-Nov-2026  → 2026-11-15        2026-11-15   → 2026-11-15   (already ISO)
native Excel date cells          → normalised too
N/A, NA, TBD, None, " ", 31-02-2026, free text → rejected and counted
```

A minimal legal CSV:

```csv
Vehicle_Reg,Document_Type,Expiry_Date,Operator_Name,Regional_RTO
DL-01-AB-1234,Insurance,15/11/2026,Speedway Fleet Services,Delhi RTO (DL-01)
HR-26-CD-4321,Fitness,2026.12.01,Ganesh Cargo Movers,Haryana RTO (HR-26)
MH-12-EF-7788,PUC,01/11/2026,,Maharashtra RTO (MH-12)
```

Need a sample of the real thing? `python -c "from fleet_compliance import mock_data as m; m.write_mock_csv('sample.csv'); m.write_mock_excel('sample.xlsx')"`

---

## Outputs and contracts

Full specification of all three artefacts: [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md).

**`output/clean_fleet_data.json`** — one object per retained record, fixed field set and
order, the unified data contract for the analytics dashboard:

```json
{
  "Vehicle_Reg": "DL-39-MV-1435",
  "Document_Type": "Road Tax",
  "Expiry_Date": "2026-11-11",
  "Operator_Name": "Chetak Distribution",
  "Regional_RTO": "Delhi RTO (DL-39)",
  "Days_Remaining": 6,
  "Compliance_Status": "Critical"
}
```

`Days_Remaining` is negative when the document has already expired and is `-999` when the
date could not be parsed (fail-safe; the status is `Critical` in that case).

**`output/high_risk_alerts.txt`** — append-only, one line per Critical record per run:

```text
[2026-11-05 09:39:00] CRITICAL | Vehicle_Reg=DL-39-MV-1435 | Document_Type=Road Tax | Days_Remaining=6 | Expiry_Date=2026-11-11
```

**Webhook payload** — one POST per run, only when Critical records exist:

```json
{ "alert_timestamp": "2026-11-05T09:39:00", "total_critical": 165, "alerts": [ { "...": "one object per Critical record" } ] }
```

---

## Configuration

Defaults are the values declared for the project (report Table 3.10) and live in one
place — `PipelineConfig` in `fleet_compliance/pipeline.py`:

| Parameter | Default | Purpose |
|---|---|---|
| `input_csv_path` | `data/database_dump.csv` | automated database export |
| `input_excel_path` | `data/manual_records.xlsx` | manual operations register |
| `output_json_path` | `output/clean_fleet_data.json` | dashboard data contract |
| `output_alert_log` | `output/high_risk_alerts.txt` | local alert log |
| `operations_webhook` | `https://api.lawyered.in/v1/compliance/webhook` | operations alert endpoint |
| `critical_threshold_days` | `7` | classification boundary (inclusive) |
| `http_timeout_seconds` | `10` | bound on the webhook call |
| `generate_mock_inputs` | `True` | create demo registers when a source is absent |

Relative paths resolve against the project root, so the run works from any working
directory. For anything more dynamic than flags (per-tenant config, secrets from the
environment), build a `PipelineConfig` in your own wrapper — that externalisation is
Phase 1 of the roadmap below.

> The default webhook is the endpoint named in the training report. It is **not** a live
> consumer of your alerts: from a sandbox or a corporate network it will simply report a
> network error and the batch will continue. Set `--webhook` (your channel), `--no-webhook`
> (local only) or point it at `tools/mock_webhook_server.py` (verification).

---

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest              # 76 checks, ~1 second
python -m ruff check .        # style/lint (configuration in pyproject.toml)
```

The suite mirrors the report's three test levels: unit checks per function on hand-built
and deliberately malformed frames, integration runs of the whole `main()` over the
1,240-row dataset, and induced-failure tests (missing files, corrupt workbooks, dead
endpoints, un-writable outputs). `--help` for nothing; `docs/TESTING.md` maps every report
test case (T-01 … T-09) to the check that covers it and shows how to reproduce the
report's tables end to end.

---

## Repository layout

```text
Internship-project/
├── run_pipeline.py                    # entry point: python run_pipeline.py [--flags]
├── fleet_compliance/
│   ├── pipeline.py                    # the eight documented functions (the utility)
│   ├── mock_data.py                   # self-generating synthetic verification dataset
│   ├── cli.py                         # argparse layer shared by all entry points
│   ├── __main__.py                    # python -m fleet_compliance
│   └── __init__.py                    # public API re-exports
├── tools/
│   └── mock_webhook_server.py         # controlled local endpoint for the dispatch tests
├── tests/                             # 76 unit / integration / induced-failure checks
├── docs/
│   ├── ARCHITECTURE.md                # tiers, gates, failure surface, scaling
│   ├── DATA_CONTRACT.md               # input schema, JSON contract, log and payload
│   ├── TESTING.md                     # how to run, coverage map, reproducing Table 4.x
│   └── TRACEABILITY.md                # report section → file/function
├── .github/workflows/ci.yml           # CI: lint, test matrix, real webhook round trip
├── data/                              # your registers (generated on demand, git-ignored)
├── output/                            # artefacts (git-ignored)
├── Dockerfile / .dockerignore         # slim, non-root image running the demo by default
├── requirements.txt / requirements-dev.txt / pyproject.toml
└── README.md
```

## How the pipeline works

Three tiers, five stages, eight functions — the short version (details in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):

1. **Ingestion** — `load_csv_file` / `load_excel_file` guard every filesystem read: a
   missing or corrupt file becomes a reported `None`, never a traceback.
2. **Sanitization** — `merge_and_clean` stacks the frames (`pd.concat(ignore_index=True)`),
   strips headers and cells, then runs three *counted* gates: null gate → hygiene gate →
   date-coercion gate. Gate order is a correctness property: dropping nulls **before**
   `astype(str)` is what stops genuine nulls from becoming the literal string `"nan"`.
3. **Logic engine** — `add_compliance_status` captures `datetime.now().date()` once, computes
   `(expiry − today).days` per record and applies `delta ≤ 7 ⇒ Critical`.
4. **Serialization** — `export_json` writes exactly the seven contract fields as a
   record-oriented array with `orient="records", indent=2`.
5. **Alerting** — `append_critical_to_txt` and `dispatch_api_alert` publish the Critical subset
   to two independent channels; `main` orchestrates the sequence, times each stage and
   prints the run summary.

## Failure handling

| Condition | What happens |
|---|---|
| one source missing/corrupt | reason printed; run continues with the other source |
| both sources missing | `[SANITIZE] Ingestion failure …`; exit `1` |
| mandatory column absent | descriptive `KeyError` reported by `main`; exit `1` |
| placeholder / malformed date | row dropped at Gate 3, counted in the printout |
| date that only the logic engine trips on | record classified `Critical`, `Days_Remaining = -999` |
| JSON export blocked | `export_json` returns `False`; batch continues |
| webhook non-200 / timeout / DNS failure | outcome printed; artefacts kept; exit `0` |
| zero Critical records | dispatch bypassed with an informational message |
| already-expired documents | negative delta ⇒ `Critical` (this is how 37 lapsed documents surfaced in verification) |

## Known limitations and roadmap

Honest boundaries of this iteration, carried from the report's Tables 5.1 and 5.2:

* failures are reported on the console, not raised or paged — an operator must read the run
  output;
* configuration lives in code (the CLI covers the common knobs; there is no config file yet);
* no persistence layer — history is the append-only log, not a database;
* the verification dataset is synthetic (production registers contain customer-identifiable
  data), and duplicate rows across sources are not de-duplicated;
* no retry or queueing for the webhook; single-node, on-demand execution only.

| Phase | Scope |
|---|---|
| 1 | externalise configuration (file/env vars) + levelled logging |
| 2 | cron scheduling + database backend for the compliance register |
| 3 | wrap as a REST microservice with role-based access for the dashboard |
| 4 | live sources (VAHAN / Parivahan, insurer feeds) to remove manual entry |
| 5 | webhook retry/queueing + predictive lead-time alerts before the 7-day window |

## Frequently asked questions

**Do I need the mock data?** No. It exists so the utility is self-testing on a fresh clone
and so the report's tables can be reproduced on demand. Use `--no-mock` in production, where
inventing a register would be wrong.

**Why two alert channels?** The webhook reaches people in real time; the flat file guarantees a
durable, offline-readable record of every detection. They are independent by design, so a dead
endpoint never means "no record".

**Why is the threshold inclusive?** So a document expiring in exactly seven days is Critical:
the operations team gets a full working week of lead time.

**My CSV has `N/A` placeholders — where are they counted?** At Gate 1, not Gate 3: pandas' default
NA vocabulary converts `N/A`, `NA`, `None` and `nan` into real nulls at *read* time. Gate 3 catches
the residue (`TBD`, `Pending from RTO`, `31-02-2026`, free text). Both are rejected and counted; only
the label differs. See `docs/DATA_CONTRACT.md` §1.

**Can it alert into Slack/Teams directly?** The payload is plain JSON, so put a formatter (API
Gateway + Lambda, a small relay, or a Zapier-style webhook) in front of the chat channel rather than
pointing this at a Slack webhook URL directly.

**Nothing was dispatched but I have Critical records.** Check the printed outcome: `dispatch bypassed`
means zero Critical records, `No webhook endpoint configured` means `--no-webhook` (or an empty
`operations_webhook`) is in effect.

**Is any real customer data in this repo?** No. `data/` and `output/` are git-ignored and generated
locally; the bundled generator emits synthetic Indian registration numbers and invented operator names.

---

## About this project

Implementation of the project documented in *Summer Training Report — "Automated Fleet Compliance
and Expiry Alert Pipeline"* by Yash Aggarwal (Enrollment No. 10015002724), B.Tech Computer Science &
Engineering, Guru Gobind Singh Indraprastha University, submitted for the summer training completed at
Lawyered (Sproutech Solutions Private Limited), Gurugram, 8 June – 27 July 2026.

* "Lawyered", "LOTS247" and "ChallanPay" are the marks of Sproutech Solutions Private Limited; this
  repository is an academic training artefact and is not affiliated with or endorsed by the company.
* All statistics about the sector quoted above are reproduced from the report; all input data in this
  repository is synthetic.

Licensed under the MIT License — see [LICENSE](LICENSE).
