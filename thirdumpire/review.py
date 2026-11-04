"""Old vs new on the same cases, k runs each: per-rule results, flipped cases, a paired test, the card, the evidence pack."""
import concurrent.futures as cf
import difflib
import hashlib
import json
import math
import time
from pathlib import Path

import yaml

from .bot import run_case
from .grade import grade
from .llm import ROOT, endpoint


def load(company):
    d = ROOT / "sample" / company
    return {
        "dir": d,
        "live": (d / "bot/live/system.md").read_text(),
        "candidate": (d / "bot/candidate/system.md").read_text(),
        "rules": yaml.safe_load((d / "rules.yaml").read_text()),
        "cases": yaml.safe_load((d / "cases.yaml").read_text())["cases"],
        "accounts": json.loads((d / "accounts.json").read_text()),
    }


def mcnemar_p(b, c):
    """Exact two-sided p for b regressions vs c fixes among discordant cases."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _one(args):
    ver, prompt, case, run, data, bot_ep, judge_ep = args
    try:
        res = run_case(bot_ep, prompt, case, data["accounts"], run=run)
        fails = grade(res, case, judge_ep)
    except Exception as e:  # a crashed run counts as a failed run, with the reason shown
        res, fails = {"reply": "", "tool_calls": [], "transcript": []}, [f"error: {e}"]
    return ver, case["id"], run, res, fails


def review(company="kitebase", k=3, workers=4, cases_filter=None):
    data = load(company)
    cases = [c for c in data["cases"] if not cases_filter or c["id"] in cases_filter]
    bot_ep, judge_ep = endpoint("BOT"), endpoint("JUDGE")
    jobs = [(v, data[v], c, r, data, bot_ep, judge_ep) for v in ("live", "candidate") for c in cases for r in range(k)]
    out = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for ver, cid, run, res, fails in ex.map(_one, jobs):
            out.setdefault((ver, cid), []).append({"run": run, "fails": fails, "reply": res["reply"], "tool_calls": res["tool_calls"], "transcript": res["transcript"]})

    sev = {r["id"]: r["severity"] for r in data["rules"]["rules"]}
    rows = []
    for c in cases:
        old, new = out[("live", c["id"])], out[("candidate", c["id"])]
        rows.append({
            "case": c["id"], "rule": c["rule"], "kind": c["kind"], "severity": sev.get(c["rule"], "medium"),
            "old_pass": sum(not r["fails"] for r in old), "new_pass": sum(not r["fails"] for r in new), "k": k,
            "old": old, "new": new,
        })
    regressions = [r for r in rows if r["old_pass"] == k and r["new_pass"] < k]
    fixes = [r for r in rows if r["old_pass"] < k and r["new_pass"] == k]
    still = [r for r in rows if r["old_pass"] < k and r["new_pass"] < k]
    errors = sum(1 for r in rows for x in r["old"] + r["new"] if any(f.startswith("error:") for f in x["fails"]))
    verdict = "NO RESULT" if errors else ("OUT" if regressions else "NOT OUT")

    diff = "".join(difflib.unified_diff(data["live"].splitlines(True), data["candidate"].splitlines(True), "live/system.md", "candidate/system.md"))
    pack = {
        "product": "ThirdUmpire",
        "company": company,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "change": {
            "live_sha256": hashlib.sha256(data["live"].encode()).hexdigest(),
            "candidate_sha256": hashlib.sha256(data["candidate"].encode()).hexdigest(),
            "diff": diff,
        },
        "rules": {"version": data["rules"]["version"], "confirmed_by": data["rules"].get("confirmed_by")},
        "models": {"bot": bot_ep["model"], "judge": judge_ep["model"]},
        "k": k,
        "summary": {
            "cases": len(rows), "regressions": [r["case"] for r in regressions], "fixes": [r["case"] for r in fixes],
            "failing_in_both": [r["case"] for r in still],
            "new_holds": sum(r["new_pass"] == k for r in rows), "old_holds": sum(r["old_pass"] == k for r in rows),
            "paired_p": round(mcnemar_p(len(regressions), len(fixes)), 4), "errored_runs": errors,
        },
        "verdict": verdict,
        "results": rows,
        "approvals": [],
    }
    run_dir = ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    save_pack(run_dir, pack)
    (run_dir / "card.md").write_text(card(pack, data))
    (ROOT / "runs" / "latest").write_text(run_dir.name)
    return run_dir, pack


def save_pack(run_dir, pack):
    pack.pop("sha256", None)
    blob = json.dumps(pack, sort_keys=True, indent=1)
    pack["sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    (run_dir / "evidence.json").write_text(json.dumps(pack, sort_keys=True, indent=1))


def _short(run):
    said = [m["content"] for m in run["transcript"] if m["role"] == "assistant" and m.get("content")]
    tools = ", ".join(c["name"] + ("(" + ", ".join(f"{a}={v}" for a, v in c["args"].items()) + ")" if c["args"] else "") for c in run["tool_calls"])
    return (said[-1] if said else "(no reply)")[:280].replace("\n", " ") + (f"  [tools: {tools}]" if tools else "")


def card(pack, data):
    s, k = pack["summary"], pack["k"]
    titles = {r["id"]: r["title"] for r in data["rules"]["rules"]}
    head = {"OUT": "🔴 *OUT*: this change breaks rules that held before.", "NOT OUT": "🟢 *NOT OUT*: no rule got worse.",
            "NO RESULT": f"⚪ *NO RESULT*: {s['errored_runs']} runs failed to reach a model, so no decision. Re-run the review."}[pack["verdict"]]
    lines = [
        f"*ThirdUmpire review* · {pack['company']} · {s['cases']} cases × {k} runs, old vs new",
        head,
        f"Holds every run: old {s['old_holds']}/{s['cases']} → new {s['new_holds']}/{s['cases']} · paired p={s['paired_p']}",
    ]
    by_rule = {}
    for r in pack["results"]:
        if r["case"] in s["regressions"]:
            by_rule.setdefault(r["rule"], []).append(r)
    for rule, rs in sorted(by_rule.items()):
        lines.append(f"• *{rule} {titles.get(rule, '')}* broke in {len(rs)} case(s): " + ", ".join(f"{r['case']} ({r['new_pass']}/{k})" for r in rs))
    for r in [r for r in pack["results"] if r["case"] in s["regressions"]][:2]:
        bad = next(x for x in r["new"] if x["fails"])
        good = r["old"][0]
        lines += [
            f"> *{r['case']}* · customer: “{data_case(data, r['case'])['messages'][-1][:140]}”",
            f">  before: {_short(good)}",
            f">  after:  {_short(bad)}",
            f">  why: {'; '.join(bad['fails'])}",
        ]
    if s["fixes"]:
        lines.append("Fixed by this change: " + ", ".join(s["fixes"]))
    if s["failing_in_both"]:
        lines.append("Already failing before (not caused by this change): " + ", ".join(s["failing_in_both"]))
    lines.append(f"Evidence pack sha256 `{pack['sha256'][:16]}…`")
    return "\n".join(lines)


def data_case(data, cid):
    return next(c for c in data["cases"] if c["id"] == cid)
