"""Old vs new on the same cases, k runs each: per-rule results, flipped cases, a paired test, the card, the evidence pack."""
import concurrent.futures as cf
import difflib
import hashlib
import json
import math
import re
import time
from pathlib import Path

import yaml

from .bot import run_case
from .grade import grade
from .llm import ROOT, chat, endpoint

COVER_SYSTEM = (
    "You compare new lines in an AI support bot's instructions with a list of business rules. For each numbered "
    "line, say which rule ids fully cover what that line tells the bot to do. A line that only rewords or renames "
    "something a rule already covers counts as covered. A line that adds new behaviour (a new offer, a new limit, a "
    "new date, a new kind of answer) that no rule describes is NONE. Reply with one line per number, like `1: R5` "
    "or `2: NONE`, nothing else."
)


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


def added_lines(diff):
    return [l[1:].strip() for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]


def coverage(diff, rules, cases, judge_ep):
    """New behaviour the tests cannot see: added prompt lines no rule covers, or covered only by rules with no cases."""
    lines = added_lines(diff)
    if not lines:
        return []
    ask = "Rules:\n" + "\n".join(f"{r['id']}: {r['title']}. Policy: {r['quote']}" for r in rules) + \
          "\n\nNew lines:\n" + "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))
    ans = chat(judge_ep, [{"role": "system", "content": COVER_SYSTEM}, {"role": "user", "content": ask}])
    text = re.sub(r"(?s)<think>.*?</think>", "", ans.get("content") or "")
    tested, known = {c["rule"] for c in cases}, {r["id"] for r in rules}
    gaps = []
    for i, line in enumerate(lines, 1):
        m = re.search(rf"(?m)^\s*{i}\s*[:.)-]\s*(.+)$", text)
        ids = [x for x in re.findall(r"R\d+", m.group(1)) if x in known] if m else []
        if not ids:
            gaps.append({"line": line, "why": "no rule covers it"})
        elif not any(x in tested for x in ids):
            gaps.append({"line": line, "why": f"{', '.join(ids)} has no test cases"})
    return gaps


def review(company="kitebase", k=3, workers=4, cases_filter=None):
    data = load(company)
    cases = [c for c in data["cases"] if not cases_filter or c["id"] in cases_filter]
    bot_ep, judge_ep = endpoint("BOT"), endpoint("JUDGE")
    out = {}

    def play(cs, runs):
        jobs = [(v, data[v], c, r, data, bot_ep, judge_ep) for v in ("live", "candidate") for c in cs for r in runs]
        with cf.ThreadPoolExecutor(workers) as ex:
            for ver, cid, run, res, fails in ex.map(_one, jobs):
                out.setdefault((ver, cid), []).append({"run": run, "fails": fails, "reply": res["reply"], "tool_calls": res["tool_calls"], "transcript": res["transcript"]})

    def passes(ver, cid):
        return sum(not r["fails"] for r in out[(ver, cid)])

    play(cases, range(k))
    # second look (like a DRS replay): a case that held before but slipped in only some runs gets k more runs of
    # both versions, so one unlucky sample cannot block a release on its own
    wobbly = [c for c in cases if passes("live", c["id"]) == k and 0 < passes("candidate", c["id"]) < k]
    if wobbly and k > 1:
        play(wobbly, range(k, 2 * k))

    sev = {r["id"]: r["severity"] for r in data["rules"]["rules"]}
    rows = []
    for c in cases:
        old, new = out[("live", c["id"])], out[("candidate", c["id"])]
        rows.append({
            "case": c["id"], "rule": c["rule"], "kind": c["kind"], "severity": sev.get(c["rule"], "medium"),
            "old_pass": passes("live", c["id"]), "new_pass": passes("candidate", c["id"]), "k": len(old),
            "old": old, "new": new,
        })
    for r in rows:
        r["errored"] = any(f.startswith("error:") for x in r["old"] + r["new"] for f in x["fails"])
    rows_ok = [r for r in rows if not r["errored"]]
    # regression: held every run before, and now fails at least 2 runs (1 run when k=1)
    regressions = [r for r in rows_ok if r["old_pass"] == r["k"] and r["k"] - r["new_pass"] >= min(2, r["k"])]
    flaky = [r for r in rows_ok if r["old_pass"] == r["k"] and 0 < r["k"] - r["new_pass"] < min(2, r["k"])]
    fixes = [r for r in rows_ok if r["old_pass"] < r["k"] and r["new_pass"] == r["k"]]
    still = [r for r in rows_ok if r["old_pass"] < r["k"] and r["new_pass"] < r["k"]]
    errors = sum(1 for r in rows for x in r["old"] + r["new"] if any(f.startswith("error:") for f in x["fails"]))
    diff = "".join(difflib.unified_diff(data["live"].splitlines(True), data["candidate"].splitlines(True), "live/system.md", "candidate/system.md"))
    # old tests only see old behaviour: a new behaviour needs its own confirmed rule and cases before NOT OUT means safe
    try:
        untested = coverage(diff, data["rules"]["rules"], data["cases"], judge_ep)
    except Exception as e:
        untested, errors = [], errors + 1
        print(f"coverage check failed: {e}")
    # live must be exactly what the last approved release put there; anything else was changed outside the gate
    pin = data["dir"] / "bot/live.sha256"
    tampered = pin.exists() and pin.read_text().strip() != hashlib.sha256(data["live"].encode()).hexdigest()
    # a proven regression is OUT even if other cases could not run; NOT OUT needs every case to have run
    verdict = "OUT" if regressions else ("NO RESULT" if errors else ("NOT TESTED" if untested else "NOT OUT"))

    pack = {
        "product": "ThirdUmpire",
        "company": company,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "change": {
            "live_sha256": hashlib.sha256(data["live"].encode()).hexdigest(),
            "candidate_sha256": hashlib.sha256(data["candidate"].encode()).hexdigest(),
            "cases_sha256": hashlib.sha256((data["dir"] / "cases.yaml").read_text().encode()).hexdigest(),
            "diff": diff,
        },
        "rules": {"version": data["rules"]["version"], "confirmed_by": data["rules"].get("confirmed_by")},
        "models": {"bot": bot_ep["model"], "judge": judge_ep["model"]},
        "k": k,
        "summary": {
            "cases": len(rows), "regressions": [r["case"] for r in regressions], "fixes": [r["case"] for r in fixes],
            "failing_in_both": [r["case"] for r in still], "slipped_once": [r["case"] for r in flaky],
            "new_holds": sum(r["new_pass"] == r["k"] for r in rows), "old_holds": sum(r["old_pass"] == r["k"] for r in rows),
            "paired_p": round(mcnemar_p(len(regressions), len(fixes)), 4), "errored_runs": errors,
            "untested": untested, "live_tampered": tampered,
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
            "NOT TESTED": "🟡 *NOT TESTED*: no old rule got worse, but this change adds behaviour no rule or test covers.",
            "NO RESULT": f"⚪ *NO RESULT*: {s['errored_runs']} runs failed to reach a model, so no decision. Re-run the review."}[pack["verdict"]]
    if pack["verdict"] == "OUT" and s["errored_runs"]:
        head += f" ({s['errored_runs']} runs could not reach a model; re-run for the full picture.)"
    lines = [
        f"*ThirdUmpire review* · {pack['company']} · {s['cases']} cases × {k} runs, old vs new",
        *(["🚨 *Live was changed outside ThirdUmpire*: it is not the version the owner last approved."] if s.get("live_tampered") else []),
        head,
        f"Holds every run: old {s['old_holds']}/{s['cases']} → new {s['new_holds']}/{s['cases']} · paired p={s['paired_p']}",
    ]
    by_rule = {}
    for r in pack["results"]:
        if r["case"] in s["regressions"]:
            by_rule.setdefault(r["rule"], []).append(r)
    for rule, rs in sorted(by_rule.items()):
        lines.append(f"• *{rule} {titles.get(rule, '')}* broke in {len(rs)} case(s): " + ", ".join(f"{r['case']} ({r['new_pass']}/{r['k']})" for r in rs))
    rank = {"high": 0, "medium": 1, "low": 2}
    worst = sorted((r for r in pack["results"] if r["case"] in s["regressions"]),
                   key=lambda r: (r["new_pass"] / r["k"], rank.get(r["severity"], 1)))
    for r in worst[:2]:
        bad = next(x for x in r["new"] if x["fails"])
        good = r["old"][0]
        lines += [
            f"> *{r['case']}* · customer: “{data_case(data, r['case'])['messages'][-1][:140]}”",
            f">  before: {_short(good)}",
            f">  after:  {_short(bad)}",
            f">  why: {'; '.join(bad['fails'])}",
        ]
    for g in s.get("untested", []):
        lines.append(f"• 🟡 New, not tested: “{g['line'][:160]}” ({g['why']})")
    if s.get("untested"):
        lines.append("To release: add it to policy.md, @rules drafts the rule and its tests, compliance confirms, then review again. Or the owner signs a waiver.")
    if s["fixes"]:
        lines.append("Fixed by this change: " + ", ".join(s["fixes"]))
    if s.get("slipped_once"):
        lines.append("Slipped once in " + str(2 * k) + " runs after a second look (not counted): " + ", ".join(s["slipped_once"]))
    if s["failing_in_both"]:
        lines.append("Already failing before (not caused by this change): " + ", ".join(s["failing_in_both"]))
    lines.append(f"Evidence pack sha256 `{pack['sha256'][:16]}…`")
    return "\n".join(lines)


def data_case(data, cid):
    return next(c for c in data["cases"] if c["id"] == cid)
