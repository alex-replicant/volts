# Shared script-section param rules for prepare (validate) and scripter (runtime).
import re

PARAM_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# Uppercased param names may never shadow runtime/OS or VOLTS-contract vars
PARAM_DENYLIST = {
    'PATH', 'HOME', 'LANG', 'PYTHONPATH', 'SHELL', 'USER', 'TZ',
    'SCENARIO', 'STAGE', 'RESULT_FILE', 'LOG_LEVEL',
    'TEST_START_TIME', 'TEST_END_TIME',
    'VP_RESULT_FILE', 'D_RESULT_FILE', 'M_RESULT_FILE', 'SIPP_RESULT_FILE',
}


def is_reserved_param(name):
    '''True if uppercased name is denied or uses the VOLTS_ prefix.'''
    upper = name.upper()
    return upper in PARAM_DENYLIST or upper.startswith('VOLTS_')
