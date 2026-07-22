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
import requirements_drift

INTERPRETERS = {'.py': 'python3', '.sh': 'bash'}
TAIL_CHARS = 500
HELPERS_DIR = '/root/helpers'
REBUILD_HINT = 'rebuild: ./build.sh -r scripter'


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


def check_requirements(report, logger):
    '''Requirements drift check. Returns False if the whole stage must fail
    (no actions run): the mounted scripts/requirements.txt and the deps baked
    into the image must match, otherwise python scripts would misbehave in
    confusing ways - fail loudly and ask for a local rebuild instead.'''
    baked, mounted = None, None
    baked_path = Path(requirements_drift.BAKED_DIGEST_FILE)
    mounted_path = Path(requirements_drift.MOUNTED_REQUIREMENTS)
    if baked_path.is_file():
        baked = baked_path.read_text().strip()
    if mounted_path.is_file():
        mounted = requirements_drift.canonical_digest(
            mounted_path.read_text(errors='replace')
        )

    verdict = requirements_drift.drift_verdict(mounted, baked)
    if verdict == requirements_drift.FAIL_MOUNTED_ONLY:
        report['error'] += (
            f"[SCRIPT][REQUIREMENTS DRIFT]: scripts/requirements.txt present but "
            f"the image was built without it - {REBUILD_HINT} "
        )
        return False
    if verdict == requirements_drift.FAIL_DIFFER:
        report['error'] += (
            f"[SCRIPT][REQUIREMENTS DRIFT]: scripts/requirements.txt changed since "
            f"the image was built - {REBUILD_HINT} "
        )
        return False
    if verdict == requirements_drift.WARN_BAKED_ONLY:
        logger.warning(
            f"image contains extra baked deps from a previous scripts/requirements.txt "
            f"that no longer exists; for a clean image, {REBUILD_HINT}"
        )
    return True


def stage_actions(actions_root, stage):
    '''Actions declared for this stage, in document order.'''
    return [
        action for action in actions_root
        if action.tag == 'action'
        and action.attrib.get('stage', 'pre').lower() == stage
    ]


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
    # Baked helpers (volts_results) importable from user python scripts;
    # PYTHONPATH is on the param denylist so params can never override this
    existing_pythonpath = env.get('PYTHONPATH')
    env['PYTHONPATH'] = (
        f"{HELPERS_DIR}:{existing_pythonpath}" if existing_pythonpath else HELPERS_DIR
    )

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


def main():
    scenario_name = os.environ.get("SCENARIO")
    scenario_stage = os.environ.get("STAGE", "pre")
    report_file = os.environ.get("RESULT_FILE", "script.jsonl")
    logger = setup_logger(__name__, get_log_level())
    error_reporter = ErrorReporter(logger)

    scenario_file = f"/xml/{scenario_name}.xml"
    if not os.path.exists(scenario_file):
        sys.exit(0)

    report = {'scenario': scenario_name, 'stage': scenario_stage, 'error': ''}
    write_line = True
    try:
        actions = stage_actions(ET.parse(scenario_file).getroot()[0], scenario_stage)
        if not actions:
            # Nothing declared for this stage - stay silent, no JSONL line
            write_line = False
        # On a failed requirements check no actions run; finally still writes the FAIL line
        elif check_requirements(report, logger):
            for action in actions:
                if not run_action(action, report, scenario_stage, logger):
                    break
    except Exception as e:
        report['error'] += f"[SCRIPT][RUNNER ERROR]: {e} "
        error_reporter.add_error(f"scripter runner error: {e}", e)
    finally:
        if write_line:
            write_report(report_file, report)


if __name__ == '__main__':
    main()
