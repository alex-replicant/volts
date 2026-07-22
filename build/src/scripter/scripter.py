# Runner for custom bash/python scripts declared in <section type="script">.
# Mirrors database.py: one JSONL line per STAGE with status derived from error.

import json
import os
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, '/root/common')
from logger import setup_logger, get_log_level, ErrorReporter
from script_params import PARAM_NAME_RE, is_reserved_param

INTERPRETERS = {'.py': 'python3', '.sh': 'bash'}
TAIL_CHARS = 500


def read_tail(temp_file, n=TAIL_CHARS):
    '''Return the last n characters of a TemporaryFile without loading it all.'''
    try:
        temp_file.flush()
        temp_file.seek(0, os.SEEK_END)
        size = temp_file.tell()
        temp_file.seek(max(0, size - n))
        return temp_file.read().strip()
    except Exception:
        return ''


def write_report(filename, report):
    report['status'] = "PASS" if not report.get('error') else "FAIL"
    with open(f"/output/{filename}", "a") as f:
        f.write(json.dumps(report) + "\n")


def run_action(action, report, scenario_stage, logger):
    '''Returns False if the remaining actions must be skipped (continue_on_error=false).'''
    script_ref = action.attrib.get('script', '')
    display = action.attrib.get('label') or script_ref
    script_path = Path('/scripts') / script_ref
    interpreter = INTERPRETERS.get(script_path.suffix)
    continue_on_error = action.attrib.get('continue_on_error', 'false').lower() in ('true', 'on', '1')

    try:
        timeout = int(action.attrib.get('timeout', 60))
        if timeout <= 0:
            raise ValueError(timeout)
    except ValueError:
        report['error'] += f"[SCRIPT][VALIDATION ERROR]: bad timeout for {script_ref} "
        return continue_on_error

    if script_ref != os.path.basename(script_ref) or not interpreter or not script_path.is_file():
        report['error'] += (
            f"[SCRIPT][VALIDATION ERROR]: {script_ref} missing, not a basename, or unsupported type "
        )
        return continue_on_error

    params, seen = {}, set()
    for p in action.findall('param'):
        name = p.attrib.get('name', '')
        value = p.attrib.get('value') if p.attrib.get('value') is not None else (p.text or '')
        upper = name.upper()
        if (not PARAM_NAME_RE.match(name) or is_reserved_param(name) or upper in seen):
            report['error'] += (
                f"[SCRIPT][VALIDATION ERROR]: param <{name}> invalid/reserved/duplicate for {display} "
            )
            return continue_on_error
        seen.add(upper)
        params[upper] = value

    env = os.environ.copy()
    env.update(params)
    env['VOLTS_PARAMS_JSON'] = json.dumps(params)

    with tempfile.TemporaryFile(mode='w+', errors='replace') as f_out, \
         tempfile.TemporaryFile(mode='w+', errors='replace') as f_err:
        try:
            proc = subprocess.Popen(
                [interpreter, str(script_path)],
                env=env,
                stdout=f_out,
                stderr=f_err,
                start_new_session=True,
            )
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            proc.wait()
            report['error'] += f"[SCRIPT][TIMEOUT]: {display} exceeded {timeout}s "
            return continue_on_error

        logger.info(f"{display} ({script_ref}) stage={scenario_stage} rc={proc.returncode}")
        if proc.returncode != 0:
            tail = read_tail(f_err) or read_tail(f_out)
            report['error'] += f"[SCRIPT][FAILED]: {display} rc={proc.returncode}: {tail} "
            return continue_on_error
    return True


# SCRIPT START
scenario_name = os.environ.get("SCENARIO")
scenario_stage = os.environ.get("STAGE", "pre")
report_file = os.environ.get("RESULT_FILE", "script.jsonl")
logger = setup_logger(__name__, get_log_level())
error_reporter = ErrorReporter(logger)

scenario_file = f"/xml/{scenario_name}.xml"
if not os.path.exists(scenario_file):
    sys.exit(0)

report = {'scenario': scenario_name, 'stage': scenario_stage, 'error': ''}
try:
    actions = ET.parse(scenario_file).getroot()[0]
    for action in actions:
        if action.tag != 'action':
            continue
        if action.attrib.get('stage', 'pre').lower() != scenario_stage:
            continue
        if not run_action(action, report, scenario_stage, logger):
            break
except Exception as e:
    report['error'] += f"[SCRIPT][RUNNER ERROR]: {e} "
    error_reporter.add_error(f"scripter runner error: {e}", e)
finally:
    write_report(report_file, report)
