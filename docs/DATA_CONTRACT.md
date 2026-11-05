# Data contracts

Every boundary in the pipeline is defined by the data that crosses it. This file
states the three contracts precisely: what you must feed in, and what you get out.

---

## 1. Input contract — the two source registers

Both files must be tabular with a header row. Column names are matched after
`strip()`, so `" Vehicle_Reg "` is fine.

| Column | Mandatory | Type seen in the wild | How the pipeline treats it |
|---|---|---|---|
| `Vehicle_Reg` | **yes** | text, often padded | coerced to string, stripped, `null`/empty → row rejected |
| `Document_Type` | **yes** | text | coerced to string, stripped |
| `Expiry_Date` | **yes** | mixed: `15/07/2026`, `2026.08.10`, `2026-07-25`, `10-Aug-2026`, Excel date cells, `TBD`, `N/A` | coerced to string, stripped, parsed (`dayfirst=True`, `format="mixed"`), unparseable → row rejected, survivors re-emitted as `YYYY-MM-DD` |
| `Operator_Name` | no | text, sometimes blank | preserved unchanged; defaulted to `Unknown RTO Carrier` **in the alert payload only** |
| `Regional_RTO` | no | text | preserved unchanged |
| anything else | no | – | carried through the sanitizer, **excluded** from the JSON contract |

File formats: `.csv` (any dialect pandas can read) for the database dump and
`.xlsx` (read through `openpyxl`) for the operations register. Legacy `.xls` is not
part of the project's stack.

**What a valid minimal CSV looks like**

```csv
Vehicle_Reg,Document_Type,Expiry_Date,Operator_Name,Regional_RTO
DL-01-AB-1234,Insurance,15/11/2026,Speedway Fleet Services,Delhi RTO (DL-01)
HR-26-CD-4321,Fitness,2026.12.01,Ganesh Cargo Movers,Haryana RTO (HR-26)
MH-12-EF-7788,PUC,01/11/2026,,Maharashtra RTO (MH-12)
```

**What the pipeline does with junk**

| Junk in the source | Where it dies | Counted as |
|---|---|---|
| missing/blank `Vehicle_Reg` or `Expiry_Date` | Gate 1 (null gate) | `Gate 1 null gate : N row(s) rejected` |
| `N/A`, `NA`, `None`, `null`, `nan` in a critical column | at read time (pandas NA vocabulary), so also Gate 1 | Gate 1 |
| whitespace-only cell (`"   "`, `"\t"`) | Gate 2 (hygiene gate, after stripping) | `Gate 2 hygiene gate : N row(s) rejected` |
| `TBD`, `Pending from RTO`, `31-02-2026`, free text | Gate 3 (date-coercion gate) | `Gate 3 date-coercion : N row(s) rejected` |
| mixed date formats | normalised, kept | – |
| a whole mandatory column missing | hard stop | `KeyError` → exit code `1` |

Duplicates across sources are **not** de-duplicated: the report's gate accounting
(Tables 3.5 / 4.2) has no dedupe gate, so a record present in both registers is
evaluated twice. Add the gate in `merge_and_clean` if your registers overlap.

---

## 2. Output contract — `output/clean_fleet_data.json`

A record-oriented JSON array (one object per retained register row), written with
`orient="records"` and `indent=2`. Field set and field order are fixed
(report Table 3.7) — downstream code can rely on them.

```json
[
  {
    "Vehicle_Reg": "DL-39-MV-1435",
    "Document_Type": "Road Tax",
    "Expiry_Date": "2026-11-11",
    "Operator_Name": "Chetak Distribution",
    "Regional_RTO": "Delhi RTO (DL-39)",
    "Days_Remaining": 6,
    "Compliance_Status": "Critical"
  }
]
```

| Field | Type | Meaning |
|---|---|---|
| `Vehicle_Reg` | string | registration identifier of the vehicle |
| `Document_Type` | string | statutory document, e.g. `Insurance`, `Fitness`, `Permit`, `PUC`, `Road Tax` |
| `Expiry_Date` | string `YYYY-MM-DD` | normalised expiry date |
| `Operator_Name` | string | operator associated with the vehicle (may be `null` if the register was blank) |
| `Regional_RTO` | string | regional transport office prefix |
| `Days_Remaining` | integer | days until expiry; negative when already expired; `-999` = unparseable (fail-safe) |
| `Compliance_Status` | string | `"Critical"` or `"Valid"` |

Invariants the pipeline guarantees:

* one object per **retained** row — nothing is silently dropped after sanitization;
* every `Expiry_Date` matches `^\d{4}-\d{2}-\d{2}$`;
* `Days_Remaining` is always an integer (never a float, never a string);
* `Compliance_Status` is always one of the two values, and
  `Compliance_Status == "Critical"` ⟺ `Days_Remaining <= threshold or Days_Remaining == -999`.

---

## 3. Output contract — `output/high_risk_alerts.txt`

Append-only flat file, one line per Critical record per run, so the file accumulates
a durable history across executions:

```
[2026-11-05 09:41:07] CRITICAL | Vehicle_Reg=DL-39-MV-1435 | Document_Type=Road Tax | Days_Remaining=6 | Expiry_Date=2026-11-11
```

Order within a run is the dataset order; the timestamp is the run timestamp, repeated
per line so each entry is self-contained when the file is grepped or split.

---

## 4. Output contract — webhook payload

One `POST` per run, only when at least one Critical record exists, with
`Content-Type: application/json` and a bounded timeout (10 s by default):

```json
{
  "alert_timestamp": "2026-11-05T09:41:07",
  "total_critical": 165,
  "alerts": [
    {
      "Vehicle_Reg": "DL-39-MV-1435",
      "Document_Type": "Road Tax",
      "Expiry_Date": "2026-11-11",
      "Operator_Name": "Chetak Distribution",
      "Regional_RTO": "Delhi RTO (DL-39)",
      "Days_Remaining": 6,
      "Compliance_Status": "Critical"
    }
  ]
}
```

Notes for whoever implements the receiving endpoint:

* the array is **not** chunked — one request carries the whole Critical set;
* `Operator_Name` is guaranteed non-empty here (`Unknown RTO Carrier` is substituted),
  unlike in the JSON contract where it is passed through as recorded;
* the endpoint's response body is ignored; only HTTP `200` counts as delivered;
* to route the same payload to Slack/Teams/email, put a small formatter (an API
  Gateway + Lambda, or a `curl | jq` hop) between this endpoint and the chat channel
  — the pipeline deliberately speaks plain JSON, not a vendor-specific block format.

Outcomes and how the pipeline reacts (report Table 3.8):

| Outcome | Behaviour |
|---|---|
| HTTP 200 | `[ALERT] Webhook dispatch OK ...`, run exits `0` |
| non-200 | status code printed, batch completes, exit `0` |
| network error / timeout | error printed, batch completes, exit `0` |
| zero Critical records | dispatch bypassed with an informational message |
| no endpoint configured (`--no-webhook`, `operations_webhook=""`) | dispatch skipped, local artefacts still written |
