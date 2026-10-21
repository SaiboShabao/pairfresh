# PairFresh

Check whether two market-data snapshots are fresh enough to use together.

[中文说明](README.zh-CN.md) · Python 3.10+ · MIT · No runtime dependencies

PairFresh is a small, offline Python library and JSONL command-line tool for
research pipelines that compare two instruments or venues. A recent cached
quote can survive a failed fetch; both legs must carry successful fetch status
and match the evaluation cycle, as well as pass timestamp checks.

**Status:** new project, version 0.1.0. Examples are synthetic. Production use,
exchange integrations, adoption and performance at scale are not established.

## Quick start

Clone or download this repository, then run from its directory:

```bash
python pairfresh.py examples/demo.jsonl --as-of 2026-10-21T04:00:10Z
```

The fixture has six candidates: one passes; five fail for a previous cycle,
expired data, a failed fetch, excessive timestamp skew, or a future timestamp.
The process intentionally exits with code **1** because some candidates fail.

Each input record produces a JSON object on stdout. A summary goes to stderr:

```json
{"mode": "replay", "total": 6, "accepted": 1, "rejected": 5}
```

For current data, omit `--as-of`:

```bash
python pairfresh.py your-snapshots.jsonl --max-age-ms 5000 --max-skew-ms 1000
```

Without `--as-of`, the tool reads the current UTC wall clock for **each**
candidate. `--as-of` explicitly selects historical replay; replay acceptance
does not establish that a quote is fresh now. Every result identifies its mode.

Optional local installation, from the repository directory:

```bash
python -m pip install .
pairfresh examples/demo.jsonl --as-of 2026-10-21T04:00:10Z
```

This source is not yet published on PyPI. Installation uses setuptools as a
build dependency; running the single Python file needs only Python itself.

## Input contract

One JSON object per line, with **exactly two** legs:

```json
{
  "pair": "DEMO/USDT",
  "research_cycle": "cycle-002",
  "legs": [
    {
      "name": "spot",
      "research_cycle": "cycle-002",
      "fetch_ok": true,
      "timestamp": "2026-10-21T04:00:08Z"
    },
    {
      "name": "perp",
      "research_cycle": "cycle-002",
      "fetch_ok": true,
      "timestamp": "2026-10-21T04:00:08.500Z"
    }
  ]
}
```

The formatted object above must occupy one line in an actual JSONL file.

| Field | Meaning |
| --- | --- |
| `pair` | Nonempty identifier for the comparison |
| Top-level `research_cycle` | The current evaluation cycle, supplied by the caller |
| `legs[].name` | Nonempty, distinct leg names; names need not be spot/perp |
| `legs[].research_cycle` | The cycle that successfully obtained this snapshot |
| `legs[].fetch_ok` | JSON boolean `true` only after a successful fetch in that cycle |
| `legs[].timestamp` | Original quote or snapshot timestamp, ISO 8601 with `Z` or an explicit UTC offset |

Cycle IDs can be nonempty strings or integers. Types must match: `2` and `"2"`
are different IDs. Boolean and floating-point IDs are rejected.

The tool checks supplied metadata. Your collector must supply the **actual
current evaluation cycle**, retain original timestamps and fetch results, and
avoid assigning a new cycle ID or timestamp to an old cache entry. Prefer the
venue's underlying quote/snapshot timestamp; a local fetch completion time
cannot reveal an already stale upstream quote. PairFresh does not independently
verify provenance, successful network operations, prices or clock accuracy.

## Acceptance rules

All of these must hold:

1. The record has a nonempty pair ID, a valid evaluation cycle and two named legs.
2. Both fetches succeeded and both leg cycles exactly match the evaluation cycle.
3. Each timestamp has a UTC offset and satisfies `0 <= age_ms < max_age_ms`.
4. The absolute timestamp difference is `<= max_skew_ms`.

The default maximum age is 5,000 ms, and maximum skew is 1,000 ms. These are
example defaults, not thresholds calibrated for a venue or strategy. Future
timestamps are rejected; synchronize collector clocks before relying on them.

Rejection reasons include:

| Reason | Interpretation |
| --- | --- |
| `legs[0]:cycle_mismatch` | First leg came from another collection cycle |
| `legs[1]:fetch_not_successful` | Second fetch failed or successful status is missing/invalid |
| `legs[0]:stale` | First leg reached or exceeded its age limit |
| `legs:skew_exceeded` | Leg timestamps are too far apart |
| `legs[0]:future_timestamp` | First timestamp is after the evaluation time |
| `legs[0]:invalid_timestamp` | Missing, invalid or timezone-ambiguous timestamp |
| `legs:expected_exactly_two` | Missing, invalid or incomplete pair |
| `record:invalid_json` | Malformed JSON, duplicate keys, nonstandard constants or decoder recursion errors |

Results also include `evaluated_at`, each parsed leg's `age_ms`, and `skew_ms`
when both timestamps could be parsed. Other missing/invalid schema fields have
specific reasons. Malformed lines are rejected while later lines are checked.
Blank lines are ignored. UTF-8 files with or without a BOM are supported.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | At least one candidate was read and all passed |
| `1` | At least one candidate was rejected, or input contained no candidates |
| `2` | Invalid command arguments or an I/O/encoding error; processing may be incomplete |

Use `-` as the input path to read standard input. A command exits with code 1
when any candidate fails; consumers may still inspect individual accepted
records. Never interpret incomplete output from an I/O error as a complete audit.

## Python integration

Import `check_pair` and pass your normalized candidate dictionary:

```python
from pairfresh import check_pair

decision = check_pair(candidate, max_age_ms=5000, max_skew_ms=1000)
if not decision["accepted"]:
    print(decision["reasons"])
```

This is an integration fragment; `candidate` comes from your collector. Invalid
candidate data returns a rejection. Invalid checker configuration raises
`ValueError`. The function does not mutate its input or access the network.

For replay, pass a timezone-aware `datetime` as `as_of`. For a live check, omit
`as_of` and do not reuse a previously accepted decision after its evaluation time.

An accepted result means only that the provided metadata passed these checks.
It does not establish matching instruments, atomic snapshots, executable
liquidity, valid order books, realistic costs, or a profitable trading signal.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests cover cached prior-cycle data, one stale leg, strict age boundaries,
skew, failed fetches, future timestamps, timezones, malformed records, replay,
live evaluation, empty input and command exit codes. A GitHub Actions workflow
is provided for Python 3.10, 3.12 and 3.13; remote results will only exist after
the repository is published and the workflow runs.

Contributions should include a minimal synthetic fixture for a concrete
failure case and a regression test. Do not submit API keys, account identifiers,
private trading records or credentials. Keep the checker offline and separate
from trading execution.
