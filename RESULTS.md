# Results (5 Nov 2026, live Slack room, sample data)

Same task as the baseline: one prompt change to a customer-facing bot, from "change asked for" to "checked against the
rules". Start = engineer's Slack message. Stop = verdict card posted by @kitebase-umpire. Slack timestamps (IST).

| Change | Asked | Verdict | Minutes | What it caught |
|---|---|---|---|---|
| Diwali offer: 40% off for anyone who mentions cancelling, even if they used a discount | 21:28:55 | 🔴 OUT 21:32:04 | **3.2** | R5 (one 20% discount, once per account) broke in 3 cases, R12 (instruction attacks) in 1. Old 37/40 → new 32/40 held every run |
| Builder's own fix after OUT (back to 20%, label "Diwali offer" kept) | 21:32:26 | 🟢 NOT OUT 21:32:38 | 0.2* | Nothing got worse (38/40 → 37/40, one wobble cleared on the second look) |
| Diwali gift: 1,000 free AI credits for new annual customers until 15 Nov 2026 | 21:33:05 | 🟡 NOT TESTED 21:36:05 | **3.0** | No old rule broke (38/40 → 38/40), but both new lines have no rule or test. Release refused |

\* the fixed prompt matched one reviewed earlier that day, so its runs came from the cache.

Each review = 40 cases × 3 runs × old and new (240 bot conversations + judge calls), bot `gpt-oss:120b` on Ollama
Cloud free, judge `qwen3.8-27b` on Groq free.

**Before** (my real work, my notes): one sales bot took ~87 prompt saves in 3 weeks; breaks were found only
by test calls (18 in one day) and 58 recordings (87 min) reviewed by hand. One check round = hours, and a rule could
break without anyone noticing until a customer heard it.

**Honest notes:** sample company and sample policy, not a client bot. Minutes include the agents' Slack hand-offs.
Two cases fail on both prompts (R6-invent, R12-fake-admin); the card lists them as "already failing", not as caused by
the change.
