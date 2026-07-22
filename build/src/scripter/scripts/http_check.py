#!/usr/bin/env python3
"""Params: URL (required), EXPECTED_STATUS (default 200), API_KEY (optional).
API_KEY demonstrates complex-value passing: it arrives byte-for-byte,
no shell escaping needed. Exit 0 = PASS, non-zero = FAIL.

Standard vars: SCENARIO, STAGE, TEST_START_TIME, TEST_END_TIME, VP_RESULT_FILE.
In the post stage, /output/$VP_RESULT_FILE (voip_patrol.jsonl) already exists,
so real-time data like SIP Call-IDs can be extracted from it.

IMPORTANT: never print params or VOLTS_PARAMS_JSON - on failure, stderr/stdout
ends up in script.jsonl and the final report (secret leak)."""
import json
import os
import sys

import requests

SCENARIO = os.environ["SCENARIO"]
STAGE = os.environ["STAGE"]


def get_sip_call_ids():
    """Return {test_label: callid} for every test line belonging to this scenario.

    voip_patrol.jsonl interleaves scenario start/end markers, per-test lines
    (single key like "1/1"), and {"vp_exit": ...} lines. Duplicate labels get
    numeric suffixes (label#2, label#3, ...) so no leg is lost.
    """
    call_ids, current = {}, None
    vp_file = f"/output/{os.environ.get('VP_RESULT_FILE', 'voip_patrol.jsonl')}"
    if not os.path.exists(vp_file):
        return call_ids
    with open(vp_file) as f:
        for line in f:
            if len(line) <= 1:
                continue
            entry = json.loads(line)
            if "vp_exit" in entry:
                continue
            marker = entry.get("scenario")
            if marker:
                name = os.path.basename(marker.get("name", "")).removesuffix(".xml")
                current = name if marker.get("state") == "start" else None
                continue
            if current != SCENARIO or len(entry) != 1:
                continue
            test = next(iter(entry.values()))
            if not isinstance(test, dict) or not test.get("callid"):
                continue
            base = test.get("label") or f"test-{len(call_ids) + 1}"
            label, n = base, 2
            while label in call_ids:
                label, n = f"{base}#{n}", n + 1
            call_ids[label] = test["callid"]
    return call_ids


call_ids = get_sip_call_ids()
print(
    f"[{SCENARIO}][{STAGE}] test window "
    f"'{os.environ.get('TEST_START_TIME')}' -> '{os.environ.get('TEST_END_TIME')}', "
    f"call-ids by label: {call_ids}"
)

if STAGE == "post" and not call_ids:
    print(f"[{SCENARIO}][{STAGE}] no call-ids found in voip_patrol results", file=sys.stderr)
    sys.exit(1)

url = os.environ["URL"]
expected = int(os.environ.get("EXPECTED_STATUS", "200"))
headers = {}
if os.environ.get("API_KEY"):
    headers["Authorization"] = f"Bearer {os.environ['API_KEY']}"

params = {
    "call_id": list(call_ids.values()),
    "from": os.environ.get("TEST_START_TIME"),
    "to": os.environ.get("TEST_END_TIME"),
}

resp = requests.get(url, headers=headers, params=params, timeout=10)
if resp.status_code != expected:
    print(
        f"[{SCENARIO}][{STAGE}] {url} returned {resp.status_code}, expected {expected}",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"{url} -> {resp.status_code} OK")
