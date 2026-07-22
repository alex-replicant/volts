#!/bin/bash
# Params: HOST (required), COUNT (optional, default 3)
# Standard vars (always set by the runner):
#   SCENARIO, STAGE           - always populated
#   TEST_START_TIME           - always populated (set just before the pre stage)
#   TEST_END_TIME             - populated in post stage only; empty string in pre
# Contract: exit 0 = PASS; non-zero = FAIL, stderr becomes s_error text
set -u
: "${HOST:?param host is required}"
COUNT="${COUNT:-3}"

echo "[${SCENARIO}][${STAGE}] ping check for ${HOST} (test started: '${TEST_START_TIME}', ended: '${TEST_END_TIME:-n/a in pre}')"

if ping -c "${COUNT}" -W 2 "${HOST}" > /dev/null; then
    echo "ping ${HOST} OK"
else
    echo "[${SCENARIO}][${STAGE}] ping ${HOST} failed after ${COUNT} attempts" >&2
    exit 1
fi
