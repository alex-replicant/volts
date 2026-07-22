# Provenance check between the mounted scripts/requirements.txt and the digest
# baked into the image at build time (see Dockerfile.scripter). Guarantees a
# forgotten local rebuild fails loudly instead of half-working.

import hashlib
import re

BAKED_DIGEST_FILE = '/root/requirements.baked.sha256'
MOUNTED_REQUIREMENTS = '/scripts/requirements.txt'

# Verdicts
OK = 'ok'
FAIL_MOUNTED_ONLY = 'fail-mounted-only'
FAIL_DIFFER = 'fail-differ'
WARN_BAKED_ONLY = 'warn-baked-only'

_BLANK_OR_COMMENT_RE = re.compile(r'^\s*(#|$)')


def canonical_digest(text):
    '''SHA-256 over the canonicalized requirements text. Must mirror the
    Dockerfile pipeline: strip CR + trailing whitespace per line, drop
    blank/comment lines, keep one trailing newline per remaining line.'''
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if _BLANK_OR_COMMENT_RE.match(line):
            continue
        lines.append(line + '\n')
    return hashlib.sha256(''.join(lines).encode()).hexdigest()


def drift_verdict(mounted_digest, baked_digest):
    '''Compare digests (either may be None when the file is absent).'''
    if mounted_digest is None and baked_digest is None:
        return OK
    if baked_digest is None:
        return FAIL_MOUNTED_ONLY
    if mounted_digest is None:
        return WARN_BAKED_ONLY
    return OK if mounted_digest == baked_digest else FAIL_DIFFER
