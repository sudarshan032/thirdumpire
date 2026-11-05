# ThirdUmpire: DRS for your AI

**Every change to a customer-facing AI bot is tested against the business's own rules, and a named person approves it in Slack before customers see it. The agents do the work. Switch makes sure only the right person can say yes.**

![Track 01: Best Business Workflow](https://img.shields.io/badge/Track_01-Best_Business_Workflow-0a7) ![Chat: Slack](https://img.shields.io/badge/chat-Slack-4A154B) ![Built on Switch](https://img.shields.io/badge/built_on-Switch-111) ![Agents: Claude Code](https://img.shields.io/badge/agents-4×_Claude_Code-d97757) ![License: MIT](https://img.shields.io/badge/license-MIT-blue)

**[▶ Demo video (under 2 min)](VIDEO_LINK)** · Flip the Switch (WeMakeDevs × Switch) · [Results](RESULTS.md)

**Who it's for:** small teams and AI agencies that run a support or sales bot and change its prompt every week, with no ML team to check it.

**How it differs:** code-review gates check *code*. Red-team tools (like Switch Trust) attack a bot with generic hostile prompts. ThirdUmpire checks the *bot's behaviour* against *this business's own written policy*, on every change, old vs new, and a named person signs off in Slack.

![Verdict card in the Slack room: 🔴 OUT, the 40% Diwali offer breaks the 20% discount rule](assets/01-out-card.png)

## Proof

| | |
|---|---|
| **Real problem** | My day job: one trial-expiry sales bot (EN/HI/TE) needed **~87 prompt saves in 3 weeks** before it stopped breaking rules. Breaks were found only by test calls: 18 calls in one day, **58 recordings (87 min) reviewed by hand**, ~61 hands-on hours. |
| **After** | Same kind of change in the room: request → verdict card in **about 3 min** (40 cases × 3 runs, old vs new), instead of a day of test calls. The 40%-off "Diwali offer" was caught breaking the 20% discount rule before release. ([results](RESULTS.md)) |
| **Public precedent** | Air Canada was held liable for its chatbot's invented refund policy (Moffatt v. Air Canada, 2024). DPD's bot swore at a customer after an update (Jan 2024). |

## How Switch is used (remove Switch and it stops working)

- **One room, four agents, three people.** The room template [`switch/thirdumpire.template.yaml`](switch/thirdumpire.template.yaml) creates the Slack channel, its instructions and four Claude Code agents: `kitebase-builder`, `kitebase-umpire`, `kitebase-rules`, `kitebase-release`. It also sets two exclusive room roles (`release-manager`, `rules-keeper`), attaches this repo as a reference, and adds a room doc explaining the verdicts.
- **Hand-offs by name.** builder → umpire → (rules) → release, with @mentions in the thread. No agent can reach the others any other way.
- **Roles enforced by Switch, not by a prompt.** [`switch/setup.py`](switch/setup.py) sets a Switch *addressing policy* per agent, before any agent is greeted: builder + umpire answer only the engineer, rules only compliance, release only the owner. Switch checks it for every message **and every answer to a permission request**. When the engineer pressed "Allow" on a release, Switch refused it: `NOT_AUTHORIZED: You may not address this agent, so you may not answer it`.
- **Permission cards in Slack.** `rules` and `release` run with auto mode off, and [`.claude/settings.json`](.claude/settings.json) marks `release`, `rollback` and `rules-confirm` as *ask*, even on a machine that allows all shell commands. Each one becomes a Switch approval card in the thread that pauses the real command, recorded with the Slack handle that pressed it.
- **Only people can approve.** Switch ignores typed answers posted by apps or tokens, so a script can't approve on someone's behalf.
- **Without Switch** the agents can't see each other's work, can't hand over, and nothing stops the engineer from releasing their own change.

## What happens in the room

```mermaid
flowchart LR
  E["👩‍💻 Engineer"] -->|"@builder add a Diwali offer"| B["🤖 builder<br/>(Claude Code)"]
  B -->|"@umpire please review"| U["🤖 umpire<br/>(Claude Code)"]
  U -->|"🔴 OUT"| B
  U -->|"🟡 NOT TESTED"| C["🧑‍⚖️ Compliance"]
  C -->|"@rules draft rule + tests"| R["🤖 rules<br/>(Claude Code)"]
  R -->|"permission request"| C
  R -->|"rules confirmed, review again"| U
  U -->|"🟢 NOT OUT"| O["👔 Owner"]
  O -->|"approve"| L["🤖 release<br/>(Claude Code, auto off)"]
  L -->|"permission request"| O
  L --> X["✅ live + evidence hash"]
```

| 🟡 NOT TESTED: a new feature no rule covers | 🛑 Switch refuses the engineer's "Allow" on a release |
|---|---|
| ![NOT TESTED card](assets/02-not-tested-card.png) | ![NOT_AUTHORIZED refusal](assets/03-not-authorized.png) |

Four verdicts, like cricket's third umpire:

| Card | Means | What happens next |
|---|---|---|
| 🔴 **OUT** | An old rule that held before now breaks (≥ 2 of k runs; one wobble gets a second look) | Back to @builder, with before/after replies and the reason |
| 🟡 **NOT TESTED** | Nothing old broke, but the change adds behaviour **no confirmed rule or test covers** | Compliance adds it to the policy, @rules drafts the rule **and** its tests, compliance confirms, umpire re-runs |
| 🟢 **NOT OUT** | Every case ran; no rule got worse; every new line is covered | Owner says "approve"; @release asks the owner for permission, then ships |
| ⚪ **NO RESULT** | Some runs could not reach a model | Run again. Never counts as a pass |

**Release refuses** unless all of these hold:

- the review is NOT OUT (or the owner signs a written waiver);
- the review is complete: every case, at least 3 runs each;
- the candidate is the exact file that was reviewed (SHA-256), and the review was made against today's live version;
- the review was never released before, so a rolled-back change can't go live again without a new review;
- compliance's sign-off fingerprint matches the current `policy.md` + `rules.yaml` + `cases.yaml` (nobody can quietly weaken a test).

**The record stays honest.** Every release stores the fingerprint of what went live. Each review checks that live still matches the last approved release, and the card shows 🚨 if live was changed outside ThirdUmpire. Every release and rollback is appended to `bot/releases.jsonl` (who, when, from which version to which), and the version taken down is kept in history. Project deny rules in `.claude/settings.json` stop agents editing `bot/live/`, its fingerprint, the release log and `policy.md` (a person owns the policy). `rules.yaml` and `cases.yaml` are drafted by @rules, so any edit to them by anyone voids compliance's sign-off until compliance confirms again.

## Run it

Needs: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker + [Switch Console](https://github.com/sandbox-quantum/switch) 0.40+, a Slack workspace connected in Switch Console, Claude Code.

```sh
git clone https://github.com/sudarshan032/thirdumpire && cd thirdumpire && uv sync
THIRDUMPIRE_OFFLINE=1 uv run python -m thirdumpire review --k 3     # AI off, no keys: replays the recorded runs
uv run python -m unittest discover -s tests -t .                     # fixed test cases
python switch/setup.py --engineer <slack handle> --compliance <slack handle> --owner <slack handle>
```

The AI-off replay works from a fresh clone: the recorded runs are committed as [`sample/kitebase/replay.json.gz`](sample/kitebase/replay.json.gz).

For live reviews copy `.env.example` to `.env` and add two free keys (bot on Ollama Cloud, judge on Groq: different vendors, so the bot is never graded by itself). Then in the Slack channel: `@kitebase-builder <your change>`.

Other commands: `release --by <name> [--waiver "R5: reason"]` · `rollback --by <name>` · `rules-confirm --by <name>`. Add `--recreate` to `setup.py` to rebuild the room.

## For judges: check every claim in 2 minutes

No keys, no Slack needed:

| Claim | Command | You should see |
|---|---|---|
| AI-off replay of a real review | `THIRDUMPIRE_OFFLINE=1 uv run python -m thirdumpire review --k 3` | 🟡 NOT TESTED card: 40 cases × 3 runs, the Diwali gift lines flagged |
| The gate refuses without a sign-off | `uv run python -m thirdumpire release --by you` | `Refused: compliance has not confirmed the current rules and tests.` |
| The verdict logic is tested | `uv run python -m unittest discover -s tests -t .` | 15 tests OK (OUT, second look, NOT TESTED, partial/stale/reused reviews, tamper, rollback log) |
| The template is valid Switch | With Switch Console running: `POST /gateway/templates/validate` with [`switch/thirdumpire.template.yaml`](switch/thirdumpire.template.yaml) | `ok: true`, no errors, no warnings |

**Real vs sample:** the Slack room, the four agents, Switch's refusals and approval cards, and the model calls are real (recorded in the video and in `replay.json.gz`). The company, its policy and its customers are sample data.

## How it works

1. **Rules from the policy.** [`sample/kitebase/policy.md`](sample/kitebase/policy.md) → [`rules.yaml`](sample/kitebase/rules.yaml) (each rule quotes its policy sentence) → [`cases.yaml`](sample/kitebase/cases.yaml): per rule a *comply*, *break* (customer pushes), *boundary* and *no-trigger* case, plus multi-turn and prompt-injection cases.
2. **Old vs new, k runs each.** Both prompts play every case with mock tools (refund, discount, escalate, privacy request).
3. **Grade with code first.** Tool called / not called, argument caps (`offer_discount.percent ≤ 20`), regex on the reply. A cross-vendor LLM judge answers only the one yes/no question code can't.
4. **Diff.** Per-rule pass^k old vs new, flipped cases, an exact paired (McNemar) test, a second look for one-off wobbles.
5. **Coverage.** The judge maps every added prompt line to the rules that cover it. A line with no rule, or a rule with no cases, is **NOT TESTED**.
6. **Evidence pack.** `runs/<time>/evidence.json`: diff, hashes, models, every transcript, approvals, releases and rollbacks; SHA-256 posted on the card.

## Sample data

Kitebase is a **fictional** SaaS company (notes app), made up for this demo: a 48-line support policy, 12 rules, 40 cases, 6 mock accounts. No client data is in this repo.

## Known limits

- **Only prompt changes are reviewed.** A model swap or a tool change is not checked yet.
- **The provider can change a model under you.** A nightly live-vs-live re-check is next.
- **The coverage check and the judge are AI too**, and can be wrong. Code grades first; the judge answers only one yes/no per case.
- **One change at a time** (one candidate file).
- **No backup approver.** If the owner is away, nothing ships.
- **Agents run on one machine.**
- **Tests catch only what they test** (like DRS judging only what the cameras saw). NOT TESTED narrows the gap: nothing new reaches customers untested unless a named person signs a waiver in writing. ~40 cases can't prove small regressions, and simulated customers are not real customers.
- Text bots only today. My real bots are voice bots (EN/HI/TE); voice + Indian-language cases are next.
- In the demo, compliance and owner are my own extra Slack accounts.

## How it maps to the judging criteria

| Criterion | Where to look |
|---|---|
| Use of Switch (30%) | Room template with 4 agents, roles, reference and doc; addressing policies set by `setup.py`; permission cards that pause the real command; agents hand off by name; wrong-person approvals refused by Switch |
| Technical execution (25%) | Clone → `uv sync` → replay with no keys; 15 tests; evidence pack with SHA-256; a release gate that refuses partial, stale, reused or unsigned reviews |
| Creativity and impact (25%) | A real problem from my job (87 prompt saves in 3 weeks, 58 recordings checked by hand) → about 3 minutes per change |
| Presentation (20%) | Video above; this README; [RESULTS.md](RESULTS.md) |

## How it was built

- Built solo, 4–7 Nov 2026, for Flip the Switch. All code written in the hackathon window.
- **AI assistance:** I directed Claude Code (by voice, Wispr Flow) and reviewed every change. In the product: bot `gpt-oss:120b` (Ollama Cloud free), judge `qwen3.8-27b` (Groq free).
- Stack: Python stdlib + PyYAML, Switch Console 0.40.1, Slack, Claude Code agents.

## Licence

MIT
