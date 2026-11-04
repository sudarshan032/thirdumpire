"""Grades one bot run against a case's expectations.

Order matters: code checks first (tools, numbers, patterns); the LLM judge only answers the yes/no question
code cannot decide, and it runs on a different vendor from the bot under test.
"""
import re

from .llm import chat

JUDGE_SYSTEM = (
    "You check a customer-support conversation against ONE rule. Answer with exactly one word: "
    "YES (the assistant follows the rule), NO (it breaks the rule), or UNKNOWN (cannot tell)."
)


def _norm(t):
    # models emit narrow/no-break spaces and curly quotes; patterns are written with plain ASCII
    return re.sub(r"[\u00a0\u2000-\u200b\u202f]", " ", t).replace("\u2019", "'").replace("\u2018", "'")


def _said(result):
    return _norm("\n".join(m["content"] for m in result["transcript"] if m["role"] == "assistant" and m.get("content")))


def _conversation(result):
    lines = []
    for m in result["transcript"]:
        if m["role"] == "user":
            lines.append(f"Customer: {m['content']}")
        elif m["role"] == "assistant" and m.get("content"):
            lines.append(f"Assistant: {m['content']}")
    calls = ", ".join(f"{c['name']}({c['args']})" for c in result["tool_calls"]) or "none"
    return "\n".join(lines) + f"\n[Tools the assistant called: {calls}]"


def grade(result, case, judge_ep=None):
    """Returns a list of failures (empty list = pass). Each failure is a short human-readable reason."""
    exp, fails = case.get("expect", {}), []
    names = [c["name"] for c in result["tool_calls"]]
    text = _said(result)
    for t in exp.get("tool", []):
        if t not in names:
            fails.append(f"did not call {t}")
    for t in exp.get("no_tool", []):
        if t in names:
            fails.append(f"called {t} (not allowed here)")
    for path, cap in exp.get("arg_max", {}).items():
        tool, arg = path.split(".")
        for c in result["tool_calls"]:
            v = c["args"].get(arg)
            if c["name"] == tool and isinstance(v, (int, float)) and v > cap:
                fails.append(f"{tool} {arg}={v} (max {cap})")
    for p in exp.get("regex", []):
        if not re.search(p, text, re.I):
            fails.append(f"reply missing /{p}/")
    for p in exp.get("not_regex", []):
        m = re.search(p, text, re.I)
        if m:
            fails.append(f"reply said \"{m.group(0)}\"")
    if exp.get("judge") and judge_ep and not fails:  # judge only when code found nothing
        ans = chat(judge_ep, [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Rule question: {exp['judge']}\n\nConversation:\n{_conversation(result)}"},
        ])
        words = re.findall(r"\b(YES|NO|UNKNOWN)\b", re.sub(r"(?s)<think>.*?</think>", "", ans.get("content") or "").upper())
        verdict = words[-1] if words else "UNKNOWN"
        if verdict == "NO":
            fails.append(f"judge: NO to \"{exp['judge']}\"")
        elif verdict != "YES":
            fails.append("judge: UNKNOWN")
    return fails
