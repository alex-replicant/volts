# Custom scripts developer guide

Scripts in this directory are baked into the optional `volts_scripter` image at
`/scripts`. Add a `.sh` or `.py` file here, append any pip deps to
[`../requirements.txt`](../requirements.txt), then rebuild:

```sh
./build.sh -s -r prepare,report,scripter
```

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
  `*_RESULT_FILE`, and anything starting with `VOLTS_`

## Environment contract

Every script receives:

| Variable | When set | Meaning |
| --- | --- | --- |
| `SCENARIO` | always | Scenario basename |
| `STAGE` | always | `pre` or `post` |
| `TEST_START_TIME` | always | Set at the start of the scenario (before database-pre) |
| `TEST_END_TIME` | post only | Empty string in pre |
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

## Exit-code contract

- Exit `0` → PASS for that action
- Non-zero → FAIL; the last ~500 chars of stderr (or stdout) become `s_error`
- Scripts never write JSONL themselves; the runner owns the
  `{"scenario","stage","error","status"}` line

`pre` and `post` are independent — there is no cleanup inversion like the
database section. A failed `pre` script fails the scenario in the report, but
`run.sh` still continues into voip/sipp/media/`post`.

## Reading real-time data (Call-IDs)

`post` scripts run after media, so `/output/voip_patrol.jsonl` already exists.
See [`http_check.py`](http_check.py) `get_sip_call_ids()` for the reference
implementation: it returns `{label: callid}` for every voip_patrol test line of
the current scenario (duplicate labels get `#2`, `#3`, … suffixes). SIPP JSONL
does not carry Call-IDs.

## Samples

- [`ping_host.sh`](ping_host.sh) — bash ping with `HOST` / `COUNT`
- [`http_check.py`](http_check.py) — HTTP GET with optional `API_KEY`, Call-ID
  extraction, and the test time window

## Escaping notes

- Literals typed in the scenario XML must XML-escape `< & "`
- Do **not** wrap Jinja in CDATA (autoescaped entities would stay literal)
- For multi-line values (PEM keys), use text-content `<param>`, not `value=`
