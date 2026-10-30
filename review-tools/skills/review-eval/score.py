#!/usr/bin/env python3
"""Scoring for /review-eval.

  score.py match  --cases DIR --runs DIR --out matches.json [--tolerance N]
  score.py report --cases DIR --runs DIR [--matches matches.json] --ledger FILE
                  --results-dir DIR --skill-hash HASH

`match` pairs each verified incident bug with candidate findings (same file),
and lists every Important finding on clean cases. A judge fills in `verdict`
on each entry. `report` turns judged matches plus the ledger into metrics,
saves them, and diffs against the previous result.
"""
import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

RESOLVED = ("fixed", "rejected", "intentional", "didnt-matter")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_cases(cases_dir):
    cases = {}
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        case = load_json(path)
        cases[case["id"]] = case
    return cases


def load_run(runs_dir, case_id):
    path = os.path.join(runs_dir, f"{case_id}.json")
    return load_json(path) if os.path.exists(path) else None


def norm(path):
    while path.startswith("./"):
        path = path[2:]
    return path


def overlaps(finding, lines, tolerance):
    start = finding.get("line") or 0
    end = finding.get("end_line") or start
    return start - tolerance <= lines[1] and end + tolerance >= lines[0]


def cmd_match(args):
    cases = load_cases(args.cases)
    out = {"incident": [], "clean": [], "missing_runs": []}
    for cid, case in cases.items():
        run = load_run(args.runs, cid)
        if run is None:
            out["missing_runs"].append(cid)
            continue
        findings = run.get("findings", [])
        if case["kind"] == "incident":
            for i, bug in enumerate(case["bugs"]):
                cands = []
                for f in findings:
                    if norm(f["file"]) != norm(bug["file"]):
                        continue
                    cands.append({**f, "near": overlaps(f, bug["lines"], args.tolerance)})
                cands.sort(key=lambda c: not c["near"])
                out["incident"].append({
                    "case": cid, "bug_index": i, "bug": bug, "candidates": cands,
                    # No same-file finding means a miss; nothing for the judge to decide.
                    "verdict": None if cands else "missed",
                })
        else:
            for f in findings:
                if f.get("label") == "Important":
                    out["clean"].append({"case": cid, "finding": f, "verdict": None})
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
    pending = sum(e["verdict"] is None for e in out["incident"] + out["clean"])
    print(f"wrote {args.out}: {len(out['incident'])} incident bugs, "
          f"{len(out['clean'])} clean-case findings, {pending} need a judge verdict, "
          f"{len(out['missing_runs'])} cases without a run")


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def p95(values):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]


def ledger_metrics(path):
    if not path or not os.path.exists(path):
        return None
    findings, first_cycle, cycles = {}, {}, defaultdict(list)
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = (row.get("repo"), row.get("branch"))
            if row.get("type") == "cycle":
                cycles[key].append(row)
            elif row.get("type") == "finding":
                # Later rows carry the updated outcome for the same finding;
                # the first row says when it was first reported.
                fkey = key + (row["id"],)
                first_cycle.setdefault(fkey, row)
                findings[fkey] = row

    outcomes = defaultdict(Counter)
    for row in findings.values():
        outcomes[row.get("label", "?")][row.get("outcome") or "open"] += 1
    by_label = {}
    for label, counts in outcomes.items():
        resolved = sum(counts[o] for o in RESOLVED)
        by_label[label] = {
            "counts": dict(counts),
            "accepted_pct": pct(counts["fixed"], resolved),
            "noise_pct": pct(resolved - counts["fixed"], resolved),
        }

    to_pass, max_cycles = [], []
    for rows in cycles.values():
        rows.sort(key=lambda r: r["cycle"])
        max_cycles.append(rows[-1]["cycle"])
        passed = [r["cycle"] for r in rows if r.get("verdict") in ("Ready", "Ready with nits")]
        if passed:
            to_pass.append(passed[0])

    later = [r for r in first_cycle.values() if r.get("cycle", 1) >= 2]
    return {
        "findings": len(findings),
        "outcomes_by_label": by_label,
        "branches": len(cycles),
        "branches_passed": len(to_pass),
        "cycles_to_pass_median": statistics.median(to_pass) if to_pass else None,
        "cycles_to_pass_p95": p95(to_pass),
        "branches_over_3_cycles": sum(c > 3 for c in max_cycles),
        "late_finding_pct": pct(sum(bool(r.get("late")) for r in later), len(later)),
    }


def cmd_report(args):
    cases = load_cases(args.cases)
    if args.matches and os.path.exists(args.matches):
        matches = load_json(args.matches)
    else:
        matches = {"incident": [], "clean": [], "missing_runs": []}
    unjudged = [e for e in matches["incident"] + matches["clean"] if e["verdict"] is None]
    if unjudged:
        sys.exit(f"{len(unjudged)} entries in {args.matches} still have verdict=null; judge them first")

    bugs = matches["incident"]
    caught = [b for b in bugs if b["verdict"] == "caught"]
    cases_caught = {b["case"] for b in caught}
    incident_ids = {b["case"] for b in bugs}
    by_class = defaultdict(lambda: [0, 0])
    for b in bugs:
        cls = b["bug"].get("class", "unclassified")
        by_class[cls][1] += 1
        by_class[cls][0] += b["verdict"] == "caught"

    judged_clean = {e["case"] for e in matches["clean"]}
    clean_ids = [cid for cid, c in cases.items()
                 if c["kind"] == "clean" and (cid in judged_clean or load_run(args.runs, cid))]
    harmful_cases = {e["case"] for e in matches["clean"] if e["verdict"] == "harmful"}
    clean_findings = len(matches["clean"])

    result = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skill_hash": args.skill_hash,
        "incident": {
            "cases": len(incident_ids),
            "bugs": len(bugs),
            "bugs_caught": len(caught),
            "recall_pct": pct(len(caught), len(bugs)),
            "cases_caught_pct": pct(len(cases_caught), len(incident_ids)),
            "recall_by_class": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_class.items())},
            "missed": [f"{b['case']}#{b['bug_index']} {b['bug']['file']}:{b['bug']['lines'][0]}"
                       for b in bugs if b["verdict"] != "caught"],
        },
        "clean": {
            "cases": len(clean_ids),
            "important_findings": clean_findings,
            "important_per_pr": round(clean_findings / len(clean_ids), 2) if clean_ids else None,
            "harmful_pct": pct(len(harmful_cases), len(clean_ids)),
            "harmful_cases": sorted(harmful_cases),
        },
        "ledger": ledger_metrics(args.ledger),
        "missing_runs": matches.get("missing_runs", []),
    }

    os.makedirs(args.results_dir, exist_ok=True)
    previous = sorted(glob.glob(os.path.join(args.results_dir, "*.json")))
    stamp = result["ts"].replace(":", "").replace("-", "")[:15]
    out_path = os.path.join(args.results_dir, f"{stamp}-{args.skill_hash}.json")
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)

    if previous:
        prev = load_json(previous[-1])
        result["vs_previous"] = {
            "previous": os.path.basename(previous[-1]),
            "recall_pct": [prev["incident"]["recall_pct"], result["incident"]["recall_pct"]],
            "harmful_pct": [prev["clean"]["harmful_pct"], result["clean"]["harmful_pct"]],
            "important_per_pr": [prev["clean"]["important_per_pr"], result["clean"]["important_per_pr"]],
            "newly_missed": sorted(set(result["incident"]["missed"]) - set(prev["incident"]["missed"])),
        }
    result["saved_to"] = out_path
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match")
    m.add_argument("--cases", required=True)
    m.add_argument("--runs", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--tolerance", type=int, default=5)
    r = sub.add_parser("report")
    r.add_argument("--cases", required=True)
    r.add_argument("--runs", required=True)
    r.add_argument("--matches")
    r.add_argument("--ledger")
    r.add_argument("--results-dir", required=True)
    r.add_argument("--skill-hash", required=True)
    args = parser.parse_args()
    {"match": cmd_match, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
