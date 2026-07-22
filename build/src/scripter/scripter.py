# Runner for custom bash/python scripts declared in <section type="script">.
# Mirrors database.py: one JSONL line per STAGE with status derived from error.

import hashlib
import json
import os
import re
import shutil
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
HELPERS_DIR = '/root/helpers'
DEPS_DIR = Path('/deps')
DEPS_PKGS = DEPS_DIR / 'pkgs'
DEPS_STAGING = DEPS_DIR / '.pkgs-new'
DEPS_MARKER = DEPS_DIR / '.marker'
REQ_PATH = Path('/scripts/requirements.txt')
CACHE_SCHEMA = 1
_BLANK_OR_COMMENT_RE = re.compile(r'^\s*(#|$)')


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


def req_body_lines(text):
    '''Non-empty, non-comment requirement lines (same filter as canonical_digest).'''
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if _BLANK_OR_COMMENT_RE.match(line):
            continue
        lines.append(line)
    return lines


def canonical_digest(text):
    '''SHA-256 over canonicalized requirements text.'''
    lines = [line + '\n' for line in req_body_lines(text)]
    return hashlib.sha256(''.join(lines).encode()).hexdigest()


def _marker_key(req_digest, image_id):
    return {
        'schema': CACHE_SCHEMA,
        'requirements_sha256': req_digest,
        'python': sys.version.split()[0],
        'image_id': image_id,
    }


def ensure_deps(report, logger):
    '''Install extras into /deps/pkgs on cache miss.

    Returns PYTHONPATH string, or None if the stage must FAIL (no actions).
    '''
    image_id = os.environ.get('VOLTS_SCRIPTER_IMAGE_ID', '').strip()
    if not image_id:
        report['error'] += (
            '[SCRIPT][DEPS INSTALL]: VOLTS_SCRIPTER_IMAGE_ID missing '
            '(run via ./run.sh) '
        )
        return None

    if not REQ_PATH.is_file():
        return HELPERS_DIR

    text = REQ_PATH.read_text(errors='replace')
    if not req_body_lines(text):
        # empty / comment-only: stock path; do NOT expose stale pkgs
        return HELPERS_DIR

    digest = canonical_digest(text)
    want = _marker_key(digest, image_id)

    if DEPS_MARKER.is_file() and DEPS_PKGS.is_dir():
        try:
            have = json.loads(DEPS_MARKER.read_text())
        except (json.JSONDecodeError, OSError):
            have = None
        if have == want:
            logger.info('scripter deps cache hit')
            return f'{DEPS_PKGS}:{HELPERS_DIR}'

    logger.info('scripter deps cache miss — pip install --target')
    # Invalidate marker first so a crash cannot leave a false hit
    DEPS_MARKER.unlink(missing_ok=True)
    if DEPS_STAGING.exists():
        shutil.rmtree(DEPS_STAGING)
    DEPS_STAGING.mkdir(parents=True)

    proc = subprocess.run(
        [
            sys.executable, '-m', 'pip', 'install',
            '--disable-pip-version-check', '--no-input',
            '--timeout', '120', '--no-cache-dir',
            '-r', str(REQ_PATH),
            '--target', str(DEPS_STAGING),
        ],
        capture_output=True, text=True, errors='replace',
    )
    if proc.returncode != 0:
        shutil.rmtree(DEPS_STAGING, ignore_errors=True)
        tail = (proc.stderr or proc.stdout or '')[-TAIL_CHARS:]
        report['error'] += (
            f'[SCRIPT][DEPS INSTALL]: pip failed rc={proc.returncode}: {tail} '
        )
        return None

    if DEPS_PKGS.exists():
        shutil.rmtree(DEPS_PKGS)
    DEPS_STAGING.rename(DEPS_PKGS)

    tmp = DEPS_DIR / '.marker.tmp'
    tmp.write_text(json.dumps(want, sort_keys=True) + '\n')
    tmp.replace(DEPS_MARKER)

    subprocess.run(
        ['chmod', '-R', 'a+rwX', str(DEPS_DIR)],
        check=False,
    )

    return f'{DEPS_PKGS}:{HELPERS_DIR}'


def stage_actions(actions_root, stage):
    '''Actions declared for this stage, in document order.'''
    return [
        action for action in actions_root
        if action.tag == 'action'
        and action.attrib.get('stage', 'pre').lower() == stage
    ]


def run_action(action, report, scenario_stage, logger, pythonpath):
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
    # PYTHONPATH is on the param denylist; ensure_deps owns the exact string
    env['PYTHONPATH'] = pythonpath

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
        else:
            # On ensure_deps failure no actions run; finally still writes the FAIL line
            pythonpath = ensure_deps(report, logger)
            if pythonpath is not None:
                for action in actions:
                    if not run_action(action, report, scenario_stage, logger, pythonpath):
                        break
    except Exception as e:
        report['error'] += f"[SCRIPT][RUNNER ERROR]: {e} "
        error_reporter.add_error(f"scripter runner error: {e}", e)
    finally:
        if write_line:
            write_report(report_file, report)


if __name__ == '__main__':
    main()
