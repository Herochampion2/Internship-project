# Architecture

> Companion document to Chapter 2 (Design) and Chapter 3 (Implementation) of the
> summer training report. Everything described here exists in
> `fleet_compliance/pipeline.py`.

## 1. Three tiers, five stages

The utility is a single-node, single-threaded batch program. There is no server,
no database and no queue: the first job was to prove the data pipeline correct and
deterministic before any infrastructure is wrapped around it (report section 2.8.1).

```
                       ┌───────────────────────────────────────────────┐
   TIER 1              │  data/database_dump.csv   (automated export)  │
   Ingestion &         │  data/manual_records.xlsx (operations register)│
   Sanitization        └───────────────┬───────────────────────────────┘
                                       │  raw DataFrames (or None)
                     ┌─────────────────▼──────────────────┐
                     │ merge_and_clean()                  │  3 counted gates
                     │ concat → header hygiene → null gate│
                     │ → hygiene gate → date coercion     │
                     └─────────────────┬──────────────────┘
                                       │  clean DataFrame, ISO-8601 dates
   TIER 2                              ▼
   Logic &          ┌────────────────────────────────────────┐
   Classification   │ add_compliance_status()                  │
                     │ delta = (expiry - today).days           │
                     │ delta <= 7 -> Critical, else Valid     │
                     └─────────────────┬──────────────────────┘
                                       │  enriched DataFrame
   TIER 3                              ▼
   Serialization  ┌──────────────────────────────┐  ┌────────────────────────────────┐
   & Alerting     │ export_json()                │  │ append_critical_to_txt()        │
                   │ → output/clean_fleet_data.json│  │ → output/high_risk_alerts.txt   │
                   └──────────────────────────────┘  │ dispatch_api_alert()            │
                                                      │ → POST operations webhook      │
                                                      └────────────────────────────────┘
```

The two Tier-3 channels are **independent**: a failed export does not suppress the
alert log, and a dead webhook does not suppress either artefact.

## 2. Stage contracts (report Table 2.9)

| Stage | Input contract | Output contract |
|---|---|---|
| Ingestion | files on disk | raw DataFrames — 812 CSV rows, 428 Excel rows on the demo dataset |
| Sanitization | merged raw frame (1,240 rows) | clean frame (1,187 rows), all dates `YYYY-MM-DD` |
| Logic | clean frame | frame enriched with `Days_Remaining`, `Compliance_Status` |
| Serialization | enriched frame | `output/clean_fleet_data.json` (record-oriented array) |
| Alerting | Critical subset | `output/high_risk_alerts.txt` lines + one webhook payload |

A contract boundary is a *checkpoint*: anything downstream can be tested by
handing it a frame that satisfies the boundary above, which is exactly how
`tests/` is organised.

## 3. Module → function map (report Table 3.3)

| Architectural module | Function | Contract |
|---|---|---|
| File ingestion | `load_csv_file(path)` | `DataFrame \| None`; missing/corrupt file → `None` + printed reason |
| File ingestion | `load_excel_file(path)` | as above, via the `openpyxl` engine |
| Sanitization | `merge_and_clean(csv, excel)` | `DataFrame \| None`; `KeyError` on a missing mandatory column |
| Logic engine | `add_compliance_status(df, threshold)` | enriched copy of `df` |
| Serialization | `export_json(df, path)` | `bool` — `False` means "reported, keep going" |
| Alerting | `append_critical_to_txt(df, path)` | `int` — number of log entries appended |
| Alerting | `dispatch_api_alert(df, url, timeout)` | `bool` — `True` only on HTTP 200 |
| Orchestration | `main(config)` | `int` — `0` batch completed, `1` nothing to evaluate |

`main()` is the only function that knows about paths, directories and the
configuration; every stage function takes data and returns data, so each one is
importable and testable on its own.

## 4. The sanitization gates, and why their order matters

```
merged frame
   │
   ├─ Gate 1  dropna(subset=["Vehicle_Reg", "Expiry_Date"])     ← BEFORE any coercion
   ├─ (hygiene) astype(str) + str.strip() on the critical columns
   ├─ Gate 2  reject "", "nan", "NaN", "None", "null"
   └─ Gate 3  to_datetime(errors="coerce", dayfirst=True, format="mixed") → drop NaT
                                                       → strftime("%Y-%m-%d")
```

Gate 1 must run before `astype(str)`. Coercing first turns a genuine null into the
four-character string `"nan"`, which then survives `dropna` — the defect that was
found and fixed during testing (report section 4.10). `tests/test_sanitization.py`
carries a regression guard for it.

Gate 2 is the second line of defence for values the readers do *not* treat as
missing: whitespace-only cells in an Excel register arrive as strings, and only
become recognisable as empty after stripping.

One nuance worth knowing when you read the gate counts: pandas' default NA
vocabulary already converts `N/A`, `NA`, `None`, `null` and `nan` into real nulls
at *read* time, so those placeholders are normally counted at Gate 1. Gate 3 sees
the residue — `TBD`, `Not Available`, `Pending from RTO`, `31-02-2026` and every
other value that is a string but not a date.

## 5. Fail-safe classification (report Table 3.6)

| Condition on `delta = (expiry - today).days` | `Days_Remaining` | `Compliance_Status` |
|---|---|---|
| `delta <= 7` (including negative, i.e. already expired) | `delta` | Critical |
| `delta > 7` | `delta` | Valid |
| date unparseable in the logic engine (upstream defect) | `-999` | Critical |

The asymmetry behind the last row is the single most important design principle in
the project: a false negative costs a penalty and a vehicle detention, a false
positive costs an operator a few minutes. The pipeline therefore always errs toward
alerting, and an unverifiable record is surfaced rather than skipped.

## 6. Defensive surface (report Table 3.9)

| Failure mode | Detection | Degradation behaviour |
|---|---|---|
| missing input file | `os.path.exists` | loader returns `None`; run continues with the other source, or exits `1` |
| corrupt / unreadable input | `try/except` around the read | loader returns `None` with a printed reason |
| missing critical column | explicit column check | `KeyError` with a descriptive message, caught in `main()` → exit `1` |
| unparseable date | `errors="coerce"` → `NaT` | row dropped and counted at Gate 3 |
| date parse failure in the logic engine | `try/except` per row | record classified Critical with `-999` |
| JSON export failure | `try/except` | reported; batch continues |
| webhook non-200 / network error | status check + `RequestException` | reported; batch continues |

What the design deliberately does **not** do: raise on any of the above (except a
structurally unusable schema), or retry a failed dispatch. Failures are reported on
the console, so a supervising operator has to read the run output — structured,
levelled logging and a retry policy are logged as future work (report Tables 5.1
and 5.2).

## 7. Performance characteristics

The logic engine is the only per-row loop in the pipeline; everything else is
vectorised. Measured on the demo dataset the whole run takes well under a second,
and the loop scales linearly with a small constant:

| Records | Logic-engine time (this repo, 4-core sandbox) | Per-record cost | Report figure (Table 4.10) |
|---|---|---|---|
| 1,000 | ~5 ms | ~4.8 µs | 26 ms / 26.0 µs |
| 10,000 | ~38 ms | ~3.8 µs | 258 ms / 25.8 µs |
| 100,000 | ~416 ms | ~4.2 µs | 2,580 ms / 25.8 µs |

So a 100,000-row register classifies in under half a second here, and in about
2.6 s on the training laptop, with no optimisation work at all. Timings are
machine-specific; every run prints its own stage-wise profile, and
`python -m pytest tests/test_performance.py` re-measures the scaling curve on yours.
