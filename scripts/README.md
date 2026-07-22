# Custom scripts developer guide

Scripts in this directory are **mounted read-only** into the optional
`volts_scripter` container at `/scripts` on every run — they are NOT baked
into the image. Add or edit a `.sh` / `.py` file here and just re-run
`./run.sh`; no rebuild needed. The scripter container uses `--net=host`
(same as voip_patrol / opensips) so scripts can reach outbound HTTPS APIs.

```sh
# activate a shipped sample (samples are inert until copied):
cp scripts/ping_host.sh.sample scripts/ping_host.sh
```

Your scripts are **local-only**: everything in this directory except
`README.md` and `*.sample` files is gitignored, so `git pull` and
`docker pull` can never overwrite or delete them.
(Careful: `git clean -xdf` removes ignored files, including your scripts.)

To use the script feature, build the optional image once: `./build.sh -s`.

## What's in the image (no rebuild needed)

The published image is static and batteries-included:

- **bash tools**: `curl`, `jq`, `ping`, `dig` (dnsutils), `ca-certificates`
- **python**: 3.13 with `requests` (pinned)
- **result helpers**: the `volts_results` python module and the
  `volts-result` CLI (see below)

### Extra python deps (escape hatch, local rebuild)

Need more than `requests`? Copy [`requirements.txt.sample`](requirements.txt.sample)
to `scripts/requirements.txt`, add pinned deps, rebuild locally:
`./build.sh -r scripter`. The build bakes a digest of the file; at runtime the
scripter compares it against the mounted copy and **fails the script stage**
if they differ (forgotten rebuild) or if the file exists but the image was
built without it (a stock image with a mounted requirements.txt also fails -
the image provably lacks your deps). If the image has baked extras and the
file was deleted, you only get a warning. `./build.sh -s -p` refuses to push
such a customized image.

## Scenario config

```xml
<section type="script">
    <actions>
        <action script="ping_host.sh" label="SBC reachable" stage="pre">
            <param name="host" value="{{ c.domain }}"/>
            <param name="count" value="3"/>
        </action>
        <action script="http_check.py" label="CDR check" stage="post"
                continue_on_error="true" timeout="30">
            <param name="url" value="https://api.example.com/cdr"/>
            <param name="api_key" value="{{ c.api_key }}"/>
            <!-- multi-line values: use text content, flush against the tags -->
            <param name="signing_key">{{ c.signing_key }}</param>
        </action>
    </actions>
</section>
```

Multiple `<section type="script">` blocks per scenario are merged (document
order preserved) so a pre section can sit above voip_patrol and a post section
below it.

### Action attributes (closed set)

| Attribute | Default | Description |
| --- | --- | --- |
| `script` | (required) | Basename of a `.sh` / `.py` file in this directory |
| `stage` | `pre` | `pre` (before voip_patrol) or `post` (after media) |
| `continue_on_error` | `false` | Keep running later actions in this stage on failure |
| `timeout` | `60` | Seconds before the process tree is SIGKILL'd |
| `label` | script name | Human-readable name in logs and report error text |

Any other attribute is a prepare-time error. Params go in `<param>` children.

### Param rules

- Names must match `^[A-Za-z_][A-Za-z0-9_]*$` and are exported **UPPERCASE**
- Use `value=` **or** text content, not both
- Text content is passed literally (not trimmed) — write Jinja flush against tags
- Reserved / denied names (rejected at prepare and runtime): `PATH`, `HOME`,
  `LANG`, `PYTHONPATH`, `SHELL`, `USER`, `TZ`, `SCENARIO`, `STAGE`,
  `RESULT_FILE`, `LOG_LEVEL`, `TEST_START_TIME`, `TEST_END_TIME`,
  `VP_RESULT_FILE`, `D_RESULT_FILE`, `M_RESULT_FILE`, `SIPP_RESULT_FILE`,
  and anything starting with `VOLTS_`

## Environment contract

Every script receives:

| Variable | When set | Meaning |
| --- | --- | --- |
| `SCENARIO` | always | Scenario basename |
| `STAGE` | always | `pre` or `post` |
| `TEST_START_TIME` | always | Set at the start of the scenario (before database-pre) |
| `TEST_END_TIME` | always | Populated in post; empty string in pre |
| `LOG_LEVEL` | always | Suite log level |
| `VP_RESULT_FILE` | always | Filename under `/output` (default `voip_patrol.jsonl`) |
| `D_RESULT_FILE` / `M_RESULT_FILE` / `SIPP_RESULT_FILE` | always | Other JSONL filenames |
| `VOLTS_PARAMS_JSON` | always | All params as one JSON object |
| `<PARAM>` | per action | Each `<param name="x">` as env var `X` |

Params travel inside the mounted `script.xml` (not `docker --env` CLI), so
values with `$ \` " ' ; | %` and API keys round-trip byte-for-byte. They still
become environment variables inside the container process. Jinja/`config.yaml`
values are XML-escaped on render and unescaped when the runner parses them.

**Never print params or `VOLTS_PARAMS_JSON`** — on failure, stderr/stdout tails
land in `script.jsonl` and the final report.

`/scripts` is mounted read-only — write any artifacts to `/output` (it is the
shared `tmp/output` volume).

## Exit-code contract

- Exit `0` → PASS for that action
- Non-zero → FAIL; the last ~500 chars of stderr (or stdout) become `s_error`
- Scripts never write JSONL themselves; the runner owns the
  `{"scenario","stage","error","status"}` line

`pre` and `post` are independent — there is no cleanup inversion like the
database section. A failed `pre` script fails the scenario in the report, but
`run.sh` still continues into voip/sipp/media/`post`.

## Reading real-time data (Call-IDs and friends)

`post` scripts run after media, so `/output/voip_patrol.jsonl` already exists.
Baked-in helpers query it — do not parse JSONL by hand:

Python:

```python
from volts_results import vp_tests, jsonl_lines

tests = vp_tests()                                   # this scenario's test lines, in order
ids = [t["callid"] for t in vp_tests(label="Call to 11111")]  # list: call_count > 1 safe
media = jsonl_lines("media")                         # raw lines from media_check.jsonl
```

Bash:

```sh
volts-result vp --get callid                          # all call-ids, one per line
volts-result vp --label "Call to 11111" --get callid --first   # exactly one (exit 1 if none)
volts-result media --json                             # raw lines as one JSON array
```

Semantics: a test label can match several legs (`call_count > 1`) — you always
get **all** matches unless you pass `--first` / `--last`. For `vp` results, if
the scenario ran more than once the latest run wins (`--all-runs` for
everything; other kinds return raw lines and have no run notion). Exit codes:
`0` match, `1` no match, `2` malformed input/usage. In the `pre` stage the vp
file has no data for the current scenario yet — expect empty results there.
SIPP JSONL does not carry Call-IDs.

## Samples (copy to activate)

- [`ping_host.sh.sample`](ping_host.sh.sample) — bash ping with `HOST` / `COUNT`
- [`http_check.py.sample`](http_check.py.sample) — HTTP GET with optional
  `API_KEY`, Call-ID extraction via `volts_results`, and the test time window

## Escaping notes

- Literals typed in the scenario XML must XML-escape `< & "`
- Do **not** wrap Jinja in CDATA (autoescaped entities would stay literal)
- For multi-line values (PEM keys), use text-content `<param>`, not `value=`
