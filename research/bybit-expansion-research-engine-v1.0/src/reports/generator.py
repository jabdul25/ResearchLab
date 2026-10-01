from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any


REPORT_NAMES = (
    "01_expansion_anatomy.md",
    "02_failed_breakouts.md",
    "03_feature_separation.md",
    "04_leakage_audit.md",
    "05_candidate_population.md",
)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _percent(value: object) -> str:
    return "missing" if value is None else f"{float(value):.1%}"


def _provenance(payload: Mapping[str, Any]) -> str:
    provenance = payload["provenance"]
    bounds = payload.get("campaign_bounds", {})
    return "\n".join(
        [
            f"- campaign start: {bounds.get('start', 'not supplied')}",
            f"- campaign end: {bounds.get('end', 'not supplied')}",
            f"- run ID: {provenance['run_id']}",
            f"- git commit: {provenance['git_commit_hash']}",
            f"- configuration hash: {provenance['configuration_hash']}",
            f"- schema version: {provenance['schema_version']}",
            f"- schema checksums: `{json.dumps(provenance.get('schema_checksums', {}), sort_keys=True)}`",
            f"- database identity: `{json.dumps(provenance.get('database_identity', {}), sort_keys=True)}`",
        ]
    )


def _all_failure_rows(payload: Mapping[str, Any]) -> list[dict]:
    return payload["evidence"]["cohorts"]["all_failures"]["comparisons"]


def _anatomy(payload: Mapping[str, Any]) -> str:
    population = payload["population"]
    rows = _all_failure_rows(payload)
    oi = next((row for row in rows if row["feature"] == "oi_expansion"), None)
    oi_n = (oi or {}).get("winner_n", 0) + (oi or {}).get("failure_n", 0)
    timeline = "\n".join(
        f"- {name}: {coverage['available']}/{coverage['total']} candidates with data"
        for name, coverage in payload["timeline_coverage"].items()
    )
    summary = payload.get("expansion_summary", {})
    features = "\n".join(
        f"- {row['feature']}: winner {_percent(row.get('winner_frequency'))}; "
        f"coverage {row['winner_n']}/{population['winners']}"
        for row in rows if row["type"] == "boolean"
    )
    return f"""# Bybit Expansion Anatomy Report

Successful expansion characteristics among matched, point-in-time eligible breakout candidates.

- Winners: {population['winners']}
- Eligible labeled candidates: {population['eligible_labeled_candidates']}
- OI coverage: {oi_n}/{population['eligible_labeled_candidates']}
- Average winner MFE: {summary.get('average_mfe')}
- Major expansions (>=50% MFE): {summary.get('major_expansions', 0)}
- Large expansions (25%-50% MFE): {summary.get('large_expansions', 0)}

## Provenance

{_provenance(payload)}

## Decision-time feature frequencies

{features}

## Pre-breakout timeline coverage

{timeline}

## Regime snapshots

```json
{json.dumps(payload.get('regimes', {}), sort_keys=True, indent=2)}
```

## Timeframe snapshots

```json
{json.dumps(payload.get('timeframes', {}), sort_keys=True, indent=2)}
```
"""


def _failures(payload: Mapping[str, Any]) -> str:
    population = payload["population"]
    sections = []
    for cohort, evidence in payload["evidence"]["cohorts"].items():
        comparisons = "\n".join(
            f"- {row['feature']}: successful {_percent(row.get('winner_frequency'))}; "
            f"failed {_percent(row.get('failure_frequency'))}; difference {_percent(row.get('difference'))}"
            if row["type"] == "boolean"
            else f"- {row['feature']}: median difference {row.get('difference')}; "
                 f"coverage {row['winner_n']} winners/{row['failure_n']} failures"
            for row in evidence["comparisons"]
        )
        sections.append(f"### {cohort}\n\n{comparisons}")
    comparisons = "\n\n".join(sections)
    return f"""# Failed Breakout Report

Matched comparison of successful expansions and similar breakout attempts that failed.

- Reversals: {population['reversals']}
- Failed breakouts: {population['failed_breakouts']}
- Weak expansions: {population['weak_expansions']}

## Observed separation

{comparisons}

## Provenance

{_provenance(payload)}

These are historically associated conditions, not causal findings or trading rules.
"""


def _separation(payload: Mapping[str, Any]) -> str:
    rows = _all_failure_rows(payload)
    ranked = "\n".join(
        f"{index}. {row['feature']} — difference {row.get('difference')}; "
        f"coverage {row['winner_n']} winners/{row['failure_n']} failures"
        for index, row in enumerate(rows, 1)
    )
    validation = payload["validation"]
    stability = "\n".join(
        f"- {name}: {validation[name]['status']}"
        for name in ("train", "validation", "test")
    )
    state = payload["validated_market_state"]
    return f"""# Feature Separation Report

Features are ranked as historically associated market conditions. No causal evidence is claimed.

## Provenance

{_provenance(payload)}

## Ranked matched-candidate separation

{ranked}

## Timeframe and regime slices

```json
{json.dumps({'timeframes': payload.get('timeframes', {}), 'regimes': payload.get('regimes', {})}, sort_keys=True, indent=2)}
```

## Fixed walk-forward stability

{stability}

- surviving features: {validation.get('surviving_features')}
- robustness: {validation.get('robustness')}

## Validated Market State

- name: {state['name']}
- status: {state['status']}
- interpretation: {state['interpretation']}
- conditions: {state.get('conditions', [])}
"""


def _leakage(payload: Mapping[str, Any]) -> str:
    leakage = payload["leakage"]
    checks = "\n".join(f"- {name}: {value}" for name, value in leakage.items())
    return f"""# Leakage Audit Report

{checks}

Point-in-time liquidity uses only the trailing 24 hours before the breakout close. Decision-time features use no later input. Future candles are used only to assign labels.

## Provenance

{_provenance(payload)}
"""


def _population(payload: Mapping[str, Any]) -> str:
    p = payload["population"]
    total = p["admitted_candidates"] + p["rejected_duplicates"]
    if p["total_breakout_candidates"] != total:
        raise ValueError("population accounting failed: raw signals must equal admitted plus rejected duplicates")
    return f"""# Candidate Population Report

- Total breakout candidates: {p['total_breakout_candidates']}
- Admitted candidates: {p['admitted_candidates']}
- Rejected duplicates: {p['rejected_duplicates']}
- Admitted + rejected duplicates: {p['admitted_candidates']} + {p['rejected_duplicates']} = {total}
- Liquidity excluded: {p['liquidity_excluded']}
- Complete liquidity windows: {p.get('complete_liquidity', 'not supplied')}
- Incomplete liquidity windows: {p.get('incomplete_liquidity', 'not supplied')}
- Pending outcomes: {p['pending_outcomes']}
- Eligible labeled candidates: {p['eligible_labeled_candidates']}
- Winners: {p['winners']}
- Reversals: {p['reversals']}
- Failed breakouts: {p['failed_breakouts']}
- Weak expansions: {p['weak_expansions']}

## Exclusions by reason, symbol, and timeframe

```json
{json.dumps(p.get('exclusions', []), sort_keys=True, indent=2)}
```

## Provenance

{_provenance(payload)}

This funnel exposes exclusions and prevents hidden selection of the analysis population.
"""


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_reports(
    output_dir: Path | str,
    payload: Mapping[str, Any],
    artifact_writer: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> list[Path]:
    """Atomically write exactly five approved reports and their shared JSON evidence."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    rendered = {
        REPORT_NAMES[0]: _anatomy(payload), REPORT_NAMES[1]: _failures(payload),
        REPORT_NAMES[2]: _separation(payload), REPORT_NAMES[3]: _leakage(payload),
        REPORT_NAMES[4]: _population(payload),
    }
    paths = []
    for name, content in rendered.items():
        path = directory / name
        _atomic_write(path, content)
        paths.append(path)
    json_path = directory / "research_results.json"
    _atomic_write(json_path, json.dumps(payload, sort_keys=True, indent=2, default=_json_default) + "\n")
    paths.append(json_path)
    if artifact_writer:
        artifact_writer("research_results", payload)
    return paths
