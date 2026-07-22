# Read-only accessors over the suite JSONL result files in /output.
# Baked into the scripter image at /root/helpers so custom scripts can either
#   python:  from volts_results import vp_tests, jsonl_lines
#   bash:    volts-result vp --label "Call to 11111" --get callid
# No status aggregation here - that is report.py's job. This module only
# answers "give me the test entities / raw lines of my scenario".

import argparse
import json
import os
import sys

# kind -> (env var carrying the filename, default filename)
KIND_FILES = {
    'vp': ('VP_RESULT_FILE', 'voip_patrol.jsonl'),
    'database': ('D_RESULT_FILE', 'database.jsonl'),
    'media': ('M_RESULT_FILE', 'media_check.jsonl'),
    'sipp': ('SIPP_RESULT_FILE', 'sipp.jsonl'),
    'script': ('RESULT_FILE', 'script.jsonl'),
}
OUTPUT_DIR = '/output'


def _result_path(kind):
    env_var, default = KIND_FILES[kind]
    return os.path.join(OUTPUT_DIR, os.environ.get(env_var) or default)


def _iter_entries(path):
    '''Yield parsed dicts from a JSONL file; ValueError on malformed lines.'''
    if not os.path.exists(path):
        return
    with open(path, errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: malformed JSONL: {e}") from e
            if isinstance(entry, dict):
                yield entry


def _marker_name(marker):
    name = marker.get('name', '')
    if not isinstance(name, str):
        raise ValueError(f"malformed scenario marker: name is not a string: {name!r}")
    return os.path.basename(name).removesuffix('.xml')


def vp_tests(scenario=None, label=None, all_runs=False):
    '''Ordered voip_patrol test-entity dicts belonging to a scenario.

    voip_patrol.jsonl interleaves scenario start/end markers, per-test lines
    (single key like "1/1") and {"vp_exit": ...} lines. If the scenario was
    run more than once, the LATEST block wins unless all_runs=True.
    Empty result in the pre stage is expected (no calls made yet).
    '''
    scenario = scenario or os.environ.get('SCENARIO', '')
    blocks, current = [], None
    for entry in _iter_entries(_result_path('vp')):
        if 'vp_exit' in entry:
            continue
        marker = entry.get('scenario')
        if isinstance(marker, dict):
            if marker.get('state') == 'start' and _marker_name(marker) == scenario:
                current = []
                blocks.append(current)
            else:
                current = None
            continue
        if current is None or len(entry) != 1:
            continue
        test = next(iter(entry.values()))
        if isinstance(test, dict):
            current.append(test)
    tests = [t for block in blocks for t in block] if all_runs else (blocks[-1] if blocks else [])
    if label is not None:
        tests = [t for t in tests if t.get('label') == label]
    return tests


def jsonl_lines(kind, scenario=None):
    '''Raw parsed lines of a result file, filtered to the scenario where the
    line format carries a plain "scenario" key (database/media/script do,
    voip_patrol lines pass through unfiltered).'''
    if kind not in KIND_FILES:
        raise ValueError(f"unknown kind <{kind}>, expected one of {sorted(KIND_FILES)}")
    scenario = scenario or os.environ.get('SCENARIO', '')
    lines = []
    for entry in _iter_entries(_result_path(kind)):
        entry_scenario = entry.get('scenario')
        if scenario and isinstance(entry_scenario, str) and entry_scenario != scenario:
            continue
        lines.append(entry)
    return lines


def _print_records(records, as_json):
    if as_json:
        print(json.dumps(records))
        return
    for record in records:
        if isinstance(record, str):
            print(record)
        else:
            print(json.dumps(record))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='volts-result',
        description='Query VOLTS JSONL result files (exit 0=match, 1=no match, 2=error)',
    )
    parser.add_argument('kind', choices=sorted(KIND_FILES))
    parser.add_argument('--scenario', default=None, help='defaults to $SCENARIO')
    parser.add_argument('--label', default=None, help='vp only: filter tests by label')
    parser.add_argument('--get', default=None, metavar='FIELD',
                        help='print this field of each match instead of the full record')
    parser.add_argument('--all-runs', action='store_true',
                        help='vp only: include every run of the scenario, not just the latest')
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument('--all', action='store_true', help='all matches (default)')
    selector.add_argument('--first', action='store_true', help='only the first match')
    selector.add_argument('--last', action='store_true', help='only the last match')
    parser.add_argument('--json', action='store_true', help='output a single JSON array')
    args = parser.parse_args(argv)

    if args.kind != 'vp' and (args.label is not None or args.all_runs):
        parser.error('--label/--all-runs only apply to kind vp')

    try:
        if args.kind == 'vp':
            records = vp_tests(scenario=args.scenario, label=args.label, all_runs=args.all_runs)
        else:
            records = jsonl_lines(args.kind, scenario=args.scenario)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    if args.get is not None:
        records = [r[args.get] for r in records if isinstance(r, dict) and args.get in r]
    if args.first:
        records = records[:1]
    elif args.last:
        records = records[-1:]
    if not records:
        return 1
    _print_records(records, args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
