"""ThirdUmpire CLI. The Switch agents call these commands; people approve them in the room.

  python -m thirdumpire review [--k 3] [--cases R2-break,R5-used]
  python -m thirdumpire release --by <name> [--waiver "R2: reason"]
  python -m thirdumpire rollback --by <name>
  python -m thirdumpire rules-confirm --by <name>
"""
import argparse
import hashlib
import json
import shutil
import sys
import time

import yaml

from .llm import ROOT
from .review import review, save_pack


def _bot(company):
    return ROOT / "sample" / company / "bot"


def _latest():
    name = (ROOT / "runs" / "latest").read_text().strip()
    d = ROOT / "runs" / name
    return d, json.loads((d / "evidence.json").read_text())


def _rules_sha(rules, company):
    # compliance signs the rules AND their test cases: nobody can weaken a test without a new sign-off
    cases = (ROOT / "sample" / company / "cases.yaml").read_text()
    return hashlib.sha256((json.dumps(rules["rules"], sort_keys=True) + cases).encode()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(prog="thirdumpire")
    p.add_argument("--company", default="kitebase")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--k", type=int, default=3)
    r.add_argument("--cases", default="")
    r.add_argument("--workers", type=int, default=4)
    rel = sub.add_parser("release")
    rel.add_argument("--by", required=True)
    rel.add_argument("--waiver", default="")
    rb = sub.add_parser("rollback")
    rb.add_argument("--by", required=True)
    rc = sub.add_parser("rules-confirm")
    rc.add_argument("--by", required=True)
    a = p.parse_args(argv)

    if a.cmd == "review":
        run_dir, pack = review(a.company, k=a.k, workers=a.workers, cases_filter=set(filter(None, a.cases.split(","))))
        print((run_dir / "card.md").read_text())
        print(f"\nEvidence: {run_dir.relative_to(ROOT)}/evidence.json")
        return 0

    if a.cmd == "release":
        run_dir, pack = _latest()
        bot = _bot(a.company)
        cand = (bot / "candidate/system.md").read_text()
        rules = yaml.safe_load((ROOT / "sample" / a.company / "rules.yaml").read_text())
        ok = rules.get("confirmed_by") or {}
        if ok.get("rules_sha256") != _rules_sha(rules, a.company):
            print("Refused: compliance has not confirmed the current rules and tests. Ask @" + a.company + "-rules.")
            return 2
        if hashlib.sha256(cand.encode()).hexdigest() != pack["change"]["candidate_sha256"]:
            print("Refused: the candidate changed after the last review. Run a new review first.")
            return 2
        if pack["verdict"] == "NO RESULT":
            print("Refused: the last review has no result (model errors). Run it again.")
            return 2
        if pack["verdict"] == "OUT" and not a.waiver:
            print("Refused: the review is OUT. Fix the change, or approve with --waiver \"<rule>: <reason>\".")
            return 2
        if pack["verdict"] == "NOT TESTED" and not a.waiver:
            print("Refused: the review is NOT TESTED (new behaviour with no rule or tests). Add it to the policy and "
                  "confirm the new rule and tests, or approve with --waiver \"NEW: <reason>\".")
            return 2
        (bot / "history").mkdir(exist_ok=True)
        hist = sorted((bot / "history").glob("v*.md"))
        n = len(hist) + 1
        shutil.copy(bot / "live/system.md", bot / f"history/v{n}.md")
        shutil.copy(bot / "candidate/system.md", bot / "live/system.md")
        pack["approvals"].append({"role": "release", "by": a.by, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "waiver": a.waiver or None, "previous_live": f"history/v{n}.md"})
        save_pack(run_dir, pack)
        print(f"Released. Approved by {a.by}{' with waiver: ' + a.waiver if a.waiver else ''}. Previous version saved as history/v{n}.md. Evidence sha256 {pack['sha256'][:16]}…")
        return 0

    if a.cmd == "rollback":
        bot = _bot(a.company)
        hist = sorted((bot / "history").glob("v*.md"), key=lambda f: int(f.stem[1:]))
        if not hist:
            print("Nothing to roll back to.")
            return 2
        shutil.copy(hist[-1], bot / "live/system.md")
        hist[-1].unlink()
        print(f"Rolled back to {hist[-1].name}, by {a.by}.")
        return 0

    if a.cmd == "rules-confirm":
        f = ROOT / "sample" / a.company / "rules.yaml"
        text = f.read_text()
        data = yaml.safe_load(text)
        data["confirmed_by"] = {"by": a.by, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rules_sha256": _rules_sha(data, a.company)}
        f.write_text(text.split("rules:", 1)[0].rsplit("confirmed_by:", 1)[0] + "confirmed_by: " + json.dumps(data["confirmed_by"]) + "\nrules:" + text.split("rules:", 1)[1])
        n = sum(1 for _ in yaml.safe_load((ROOT / "sample" / a.company / "cases.yaml").read_text())["cases"])
        print(f"Rules v{data['version']} ({len(data['rules'])} rules, {n} test cases) confirmed by {a.by}.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
