"""
Lenovo Warranty Lookup — server-side (GitHub Actions) version.

Reads data/inventory.csv, looks up warranty info for every Lenovo device
directly against pcsupport.lenovo.com (no browser, no CORS proxy — this runs
on GitHub's servers with a normal outbound connection), and writes
data/results.json for the static site to display.

Deliberately paced to be a good citizen of an unofficial endpoint:
  - requests within a batch are spaced out with randomized delays
  - after each batch, it pauses for several minutes (also randomized)
  - failed requests get a few retries with backoff before being logged as failed

This script has no time pressure — it's meant to run unattended in the
background for however long it takes (typically 45–70 minutes for ~350
devices). It is triggered automatically by the GitHub Actions workflow
whenever data/inventory.csv changes.
"""

import csv
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config — tune these if you ever need to
# ---------------------------------------------------------------------------

BATCH_SIZE = 25                # devices per batch (= up to 50 HTTP requests)
BATCH_PAUSE_RANGE = (150, 220)  # seconds to pause between batches (2.5–3.7 min)
REQUEST_DELAY_RANGE = (1.0, 2.2)  # seconds between individual requests, jittered
MAX_ATTEMPTS = 3               # retries per request before giving up
RETRY_BACKOFF_BASE = 3         # seconds; grows with each retry attempt

INPUT_CSV = Path("data/inventory.csv")
OUTPUT_JSON = Path("data/results.json")

WANTED_COLS = [
    "Device name", "Enrollment date", "Last check-in", "OS version",
    "Azure AD registered", "Serial number", "Manufacturer", "Model",
    "Primary user email address", "Primary user display name",
    "Device state", "Intune registered", "SkuFamily", "ProcessorArchitecture",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html",
}

session = requests.Session()
session.headers.update(HEADERS)


def jitter_sleep(lo, hi):
    time.sleep(random.uniform(lo, hi))


def find_column(fieldnames, *candidates):
    lower = {f.lower(): f for f in fieldnames}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    for c in candidates:
        for f in fieldnames:
            if c.lower() in f.lower():
                return f
    return None


def request_with_retries(method, url, **kwargs):
    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = session.request(method, url, timeout=20, **kwargs)
            if r.status_code == 200:
                return r
            last_exc = RuntimeError(f"HTTP {r.status_code}")
        except requests.RequestException as e:
            last_exc = e
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_BASE * attempt + random.uniform(0, 1.5))
    raise last_exc


def lookup_warranty(serial: str) -> dict:
    out = {"status": "", "start": "", "end": "", "model": "", "note": ""}

    try:
        r = request_with_retries(
            "GET",
            "https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts",
            params={"productId": serial},
        )
        data = r.json()
    except Exception as e:
        out["note"] = f"lookup step failed: {e}"
        return out

    if not data:
        out["note"] = "no product match"
        return out

    product_id = data[0].get("id", "")
    if not product_id:
        out["note"] = "no product id returned"
        return out

    jitter_sleep(*REQUEST_DELAY_RANGE)

    try:
        r2 = request_with_retries(
            "GET",
            f"https://pcsupport.lenovo.com/us/en/products/{product_id}/warranty",
        )
        html = r2.text
    except Exception as e:
        out["note"] = f"warranty page fetch failed: {e}"
        return out

    def grab(pattern):
        m = re.search(pattern, html)
        return m.group(1).strip() if m else ""

    out["model"] = grab(r'"Name":"(.*?)"')
    out["status"] = grab(r'"StatusV2":"(.*?)"') or grab(r'"warrantystatus":"(.*?)"')
    out["start"] = grab(r'"Start":"(.*?)"')
    out["end"] = grab(r'"End":"(.*?)"')
    if not out["end"]:
        out["note"] = "fetched page but no end date found"

    return out


def years_between(date_str: str):
    if not date_str:
        return None
    try:
        # handles "YYYY-MM-DD..." style strings (with or without time component)
        cleaned = date_str[:10]
        d = datetime.strptime(cleaned, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    return (now - d).days / 365.25


def main():
    if not INPUT_CSV.exists():
        print(f"No {INPUT_CSV} found — nothing to do.")
        sys.exit(0)

    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    resolved_wanted = [find_column(fieldnames, w) for w in WANTED_COLS]
    serial_col = find_column(fieldnames, "serial number", "serial")
    mfg_col = find_column(fieldnames, "manufacturer")
    enroll_col = find_column(fieldnames, "enrollment date", "enrolled")

    if not serial_col:
        print("Could not find a 'Serial number' column in the CSV. Aborting.")
        sys.exit(1)

    lenovo_rows = [r for r in rows if not mfg_col or "lenovo" in (r.get(mfg_col) or "").lower()]
    print(f"{len(rows)} total rows, {len(lenovo_rows)} Lenovo rows to process.")

    results = []
    failures = 0
    total = len(lenovo_rows)

    for batch_start in range(0, total, BATCH_SIZE):
        batch = lenovo_rows[batch_start: batch_start + BATCH_SIZE]
        print(f"\n--- Batch {batch_start // BATCH_SIZE + 1} "
              f"({batch_start + 1}-{min(batch_start + len(batch), total)} of {total}) ---")

        for row in batch:
            serial = (row.get(serial_col) or "").strip()
            out_row = {}
            for wanted, actual in zip(WANTED_COLS, resolved_wanted):
                out_row[wanted] = row.get(actual, "") if actual else ""

            if not serial:
                out_row.update({
                    "Warranty Status": "", "Warranty Start": "", "Warranty End": "",
                    "Age (years)": "", "Age Basis": "", "4+ Years Old": "",
                    "Note": "no serial",
                })
                results.append(out_row)
                continue

            print(f"  {serial} ...", end=" ", flush=True)
            w = lookup_warranty(serial)

            anchor = w["start"]
            basis = "Warranty Start (Lenovo)"
            if not anchor and enroll_col:
                anchor = row.get(enroll_col, "")
                basis = "Enrollment date (estimate)"
            if not anchor:
                basis = ""

            yrs = years_between(anchor)
            is_old = "Yes" if (yrs is not None and yrs >= 4) else ("No" if yrs is not None else "")

            out_row.update({
                "Warranty Status": w["status"] or ("" if w["note"] else "unknown"),
                "Warranty Start": w["start"],
                "Warranty End": w["end"],
                "Age (years)": f"{yrs:.1f}" if yrs is not None else "",
                "Age Basis": basis,
                "4+ Years Old": is_old,
                "Note": w["note"],
            })
            if w["note"]:
                failures += 1
            results.append(out_row)
            print(w["end"] or w["note"] or "?")

            jitter_sleep(*REQUEST_DELAY_RANGE)

        if batch_start + BATCH_SIZE < total:
            pause = random.uniform(*BATCH_PAUSE_RANGE)
            print(f"  Pausing {pause:.0f}s before next batch...")
            time.sleep(pause)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_rows": len(rows),
        "lenovo_devices": total,
        "failed_lookups": failures,
        "rows": results,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. {total - failures}/{total} succeeded. Wrote {OUTPUT_JSON}.")


if __name__ == "__main__":
    main()
