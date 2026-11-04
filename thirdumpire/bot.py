"""Runs the bot under test (one system prompt) on one case, with mock tools."""
import json

from .llm import chat

TOOLS = [
    {"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": list(p)}}}
    for n, d, p in [
        ("lookup_account", "Look up the signed-in customer's plan, billing, payments and discount history.", {}),
        ("issue_refund", "Refund the customer.", {"amount_usd": {"type": "number"}, "reason": {"type": "string"}}),
        ("offer_discount", "Offer a retention discount.", {"percent": {"type": "number"}, "months": {"type": "number"}}),
        ("escalate_to_human", "Hand the conversation to a human agent.", {"reason": {"type": "string"}}),
        ("request_data_export_or_deletion", "Start a privacy request.", {"kind": {"type": "string", "enum": ["export", "deletion"]}}),
    ]
]


def run_case(ep, system_prompt, case, accounts, run=0, temperature=0.7):
    """Plays the case's scripted user turns. Returns {"reply", "tool_calls", "transcript"}."""
    acct = accounts.get(case.get("account")) if case.get("account") else None
    sys = system_prompt + ("\n\nSigned-in customer account id: " + case["account"] if acct else "\n\nThe customer is not signed in.")
    msgs = [{"role": "system", "content": sys}]
    calls, reply = [], ""
    for turn in case["messages"]:
        msgs.append({"role": "user", "content": turn})
        for _ in range(4):  # at most 4 tool rounds per user turn
            m = chat(ep, msgs, tools=TOOLS, temperature=temperature, run=run)
            tcs = m.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": tcs} if tcs else {})})
            if not tcs:
                reply = m.get("content") or ""
                break
            for tc in tcs:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"name": name, "args": args})
                result = acct if name == "lookup_account" else {"ok": True}
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result or {"error": "not signed in"})})
    return {"reply": reply, "tool_calls": calls, "transcript": [m for m in msgs[1:]]}
