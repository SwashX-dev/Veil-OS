"""Render Network Guard / audit results as text or JSON."""

from __future__ import annotations

import json
import time

from veil import __version__, common
from veil.netguard.checks import Check

ORDER = {"fail": 0, "warn": 1, "pass": 2, "info": 3, "skip": 4}


def summarize(checks: list[Check]) -> dict:
    counts = {k: 0 for k in ORDER}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    if counts["fail"]:
        verdict = "fail"
    elif counts["warn"]:
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "counts": counts}


def to_json(title: str, checks: list[Check]) -> str:
    osr = common.read_os_release()
    return json.dumps({
        "report": title,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "veil_version": osr.get("VEIL_VERSION", __version__),
        "tails_base": osr.get("VERSION"),
        "summary": summarize(checks),
        "checks": [c.to_dict() for c in checks],
    }, indent=2)


def to_text(title: str, checks: list[Check], verbose: bool = False) -> str:
    lines = [common.violet(common.bold(f"◆ {title}")), ""]
    cat = None
    for c in sorted(checks, key=lambda c: (c.category, ORDER.get(c.status, 9))):
        if c.category != cat:
            cat = c.category
            lines.append(common.dim(f"  {cat.upper()}"))
        lines.append(f"  {common.fmt_status(c.status)} {c.title}")
        lines.append(common.dim(f"         {c.detail}"))
        if verbose and c.data:
            for k, v in c.data.items():
                lines.append(common.dim(f"           {k}: {v}"))
    s = summarize(checks)
    lines.append("")
    n = s["counts"]
    lines.append(f"  Result: {common.fmt_status(s['verdict'])}  "
                 f"{n['pass']} passed · {n['warn']} warnings · {n['fail']} failed · "
                 f"{n['info']} info · {n['skip']} skipped")
    return "\n".join(lines)
