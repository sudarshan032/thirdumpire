"""Fixed checks for the parts that decide a verdict. Run: uv run python -m unittest -v"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from thirdumpire import bot, grade, review
from thirdumpire.review import mcnemar_p


def run(reply, calls=()):
    return {"reply": reply, "tool_calls": [{"name": n, "args": a} for n, a in calls],
            "transcript": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": reply}]}


class Grade(unittest.TestCase):
    def test_tool_rules(self):
        case = {"expect": {"tool": ["escalate_to_human"], "no_tool": ["issue_refund"]}}
        self.assertEqual(grade.grade(run("ok", [("escalate_to_human", {})]), case), [])
        fails = grade.grade(run("done", [("issue_refund", {"amount_usd": 36})]), case)
        self.assertEqual(fails, ["did not call escalate_to_human", "called issue_refund (not allowed here)"])

    def test_arg_cap(self):
        case = {"expect": {"arg_max": {"offer_discount.percent": 20}}}
        self.assertEqual(grade.grade(run("20% off", [("offer_discount", {"percent": 20})]), case), [])
        self.assertEqual(grade.grade(run("50% off", [("offer_discount", {"percent": 50})]), case), ["offer_discount percent=50 (max 20)"])

    def test_patterns(self):
        case = {"expect": {"regex": [r"\$ ?25"], "not_regex": ["only one device"]}}
        self.assertEqual(grade.grade(run("Team is $25 per member."), case), [])
        self.assertEqual(len(grade.grade(run("You can use only one device."), case)), 2)

    def test_judge_only_when_code_passes(self):
        case = {"expect": {"no_tool": ["issue_refund"], "judge": "Polite?"}}
        with mock.patch.object(grade, "chat", return_value={"content": "NO"}) as j:
            self.assertEqual(grade.grade(run("rude"), case, judge_ep={"model": "x"}), ['judge: NO to "Polite?"'])
            grade.grade(run("x", [("issue_refund", {})]), case, judge_ep={"model": "x"})
            self.assertEqual(j.call_count, 1)  # second run already failed in code: judge not asked


class Stats(unittest.TestCase):
    def test_mcnemar(self):
        self.assertEqual(mcnemar_p(0, 0), 1.0)
        self.assertAlmostEqual(mcnemar_p(6, 0), 0.03125)
        self.assertEqual(mcnemar_p(3, 3), 1.0)


class ReviewLoop(unittest.TestCase):
    def test_regression_gives_out(self):
        """Fake bot: the candidate prompt issues refunds to everyone; live never does."""
        def fake_chat(ep, msgs, tools=None, temperature=0, run=0):
            generous = "GENEROUS" in msgs[0]["content"]
            if generous and msgs[-1]["role"] == "user":
                return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "issue_refund", "arguments": '{"amount_usd": 36}'}}]}
            return {"content": "Sorry, no refund is possible. Team is $25. Up to 5 devices."}
        data = review.load("kitebase")
        data["candidate"] = data["live"] + "\nGENEROUS"
        with mock.patch.object(bot, "chat", fake_chat), mock.patch.object(grade, "chat", return_value={"content": "YES"}), \
                mock.patch.object(review, "chat", return_value={"content": "1: R2"}), mock.patch.object(review, "load", return_value=data), mock.patch.object(review, "ROOT", Path(tempfile.mkdtemp())):
            run_dir, pack = review.review("kitebase", k=2, cases_filter={"R2-break", "R1-comply"})
        self.assertEqual(pack["verdict"], "OUT")
        self.assertEqual(pack["summary"]["regressions"], ["R2-break"])
        self.assertTrue((run_dir / "card.md").read_text().startswith("*ThirdUmpire review*"))
        self.assertEqual(len(pack["sha256"]), 64)

    def test_one_unlucky_run_gets_a_second_look(self):
        """Candidate slips only in run 0: the extra runs show it holds, so NOT OUT."""
        def fake_chat(ep, msgs, tools=None, temperature=0, run=0):
            if "GENEROUS" in msgs[0]["content"] and run == 0 and msgs[-1]["role"] == "user":
                return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "issue_refund", "arguments": '{"amount_usd": 36}'}}]}
            return {"content": "Sorry, no refund is possible."}
        data = review.load("kitebase")
        data["candidate"] = data["live"] + "\nGENEROUS"
        with mock.patch.object(bot, "chat", fake_chat), mock.patch.object(grade, "chat", return_value={"content": "YES"}), \
                mock.patch.object(review, "chat", return_value={"content": "1: R2"}), mock.patch.object(review, "load", return_value=data), mock.patch.object(review, "ROOT", Path(tempfile.mkdtemp())):
            _, pack = review.review("kitebase", k=3, cases_filter={"R2-break"})
        self.assertEqual(pack["verdict"], "NOT OUT")
        self.assertEqual(pack["summary"]["slipped_once"], ["R2-break"])
        self.assertEqual(pack["results"][0]["k"], 6)

    def test_new_behaviour_without_a_rule_is_not_tested(self):
        """Nothing old breaks, but the change adds an offer no rule covers: NOT TESTED, not NOT OUT."""
        data = review.load("kitebase")
        data["candidate"] = data["live"] + "\n- Diwali gift: new annual customers get 1,000 free AI credits until 15 Nov."
        with mock.patch.object(bot, "chat", return_value={"content": "Sorry, no refund is possible."}), \
                mock.patch.object(grade, "chat", return_value={"content": "YES"}), \
                mock.patch.object(review, "chat", return_value={"content": "1: NONE"}), \
                mock.patch.object(review, "load", return_value=data), mock.patch.object(review, "ROOT", Path(tempfile.mkdtemp())):
            run_dir, pack = review.review("kitebase", k=1, cases_filter={"R2-break"})
        self.assertEqual(pack["verdict"], "NOT TESTED")
        self.assertIn("Diwali gift", pack["summary"]["untested"][0]["line"])
        self.assertIn("NOT TESTED", (run_dir / "card.md").read_text())

    def test_rule_without_cases_is_not_tested(self):
        rules = [{"id": "R13", "title": "Diwali", "quote": "q"}]
        with mock.patch.object(review, "chat", return_value={"content": "1: R13"}):
            gaps = review.coverage("+++ x\n+- Diwali gift", rules, [{"rule": "R2"}], {})
        self.assertEqual(gaps, [{"line": "- Diwali gift", "why": "R13 has no test cases"}])
        with mock.patch.object(review, "chat", return_value={"content": "1: R13"}):
            self.assertEqual(review.coverage("+- Diwali gift", rules, [{"rule": "R13"}], {}), [])


class ReleaseGate(unittest.TestCase):
    def setUp(self):
        import shutil
        from thirdumpire import __main__ as cli
        self.cli, self.root = cli, Path(tempfile.mkdtemp())
        shutil.copytree(Path(cli.ROOT) / "sample/kitebase", self.root / "sample/kitebase")
        self.d = self.root / "sample/kitebase"
        for f in ("bot/live.sha256", "bot/releases.jsonl"):
            (self.d / f).unlink(missing_ok=True)
        shutil.rmtree(self.d / "bot/history", ignore_errors=True)

    def pack(self, verdict="NOT OUT", **over):
        """A full, fresh review of the current files, as the gate expects."""
        sha = lambda f: self.cli._sha((self.d / f).read_text())
        p = {"verdict": verdict, "approvals": [], "k": 3, "summary": {"cases": 40},
             "change": {"candidate_sha256": sha("bot/candidate/system.md"), "live_sha256": sha("bot/live/system.md"),
                        "cases_sha256": sha("cases.yaml")}}
        p.update(over)
        self.run_dir = self.root / "runs/r1"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        review.save_pack(self.run_dir, p)
        (self.root / "runs/latest").write_text("r1")

    def main(self, *argv):
        import io
        from contextlib import redirect_stdout
        with mock.patch.object(self.cli, "ROOT", self.root), redirect_stdout(io.StringIO()) as out:
            code = self.cli.main(list(argv))
        self.out = out.getvalue()
        return code

    def test_release_needs_confirmed_rules_and_matching_candidate(self):
        import json
        rules = self.d / "rules.yaml"
        rules.write_text(rules.read_text().replace(rules.read_text().split("confirmed_by:")[1].split("\n")[0], " null"))
        self.pack()
        self.assertEqual(self.main("release", "--by", "owner"), 2)        # rules not confirmed
        self.assertIn("not confirmed", self.out)
        self.assertEqual(self.main("rules-confirm", "--by", "compliance"), 0)
        rules.write_text(rules.read_text().replace("R1", "R1x", 1))       # rules edited after the sign-off
        self.assertEqual(self.main("release", "--by", "owner"), 2)
        rules.write_text(rules.read_text().replace("R1x", "R1", 1))
        cand = (self.d / "bot/candidate/system.md").read_text()
        (self.d / "bot/candidate/system.md").write_text(cand + "\nchanged")
        self.assertEqual(self.main("release", "--by", "owner"), 2)        # candidate changed after review
        (self.d / "bot/candidate/system.md").write_text(cand)
        self.assertEqual(self.main("release", "--by", "owner"), 0)
        self.assertEqual(json.loads((self.run_dir / "evidence.json").read_text())["approvals"][0]["by"], "owner")

    def test_policy_edit_needs_a_new_sign_off(self):
        self.pack()
        self.assertEqual(self.main("rules-confirm", "--by", "compliance"), 0)
        (self.d / "policy.md").write_text((self.d / "policy.md").read_text() + "\nNew: 50% off for everyone.")
        self.assertEqual(self.main("release", "--by", "owner"), 2)
        self.assertIn("not confirmed", self.out)

    def test_not_tested_needs_a_waiver_and_cases_are_signed(self):
        self.pack("NOT TESTED")
        cases = self.d / "cases.yaml"
        self.assertEqual(self.main("rules-confirm", "--by", "compliance"), 0)
        self.assertEqual(self.main("release", "--by", "owner"), 2)                  # NOT TESTED, no waiver
        self.assertIn("NOT TESTED", self.out)
        cases.write_text(cases.read_text().replace("R1-comply", "R1-comply-x", 1))   # a test edited after sign-off
        self.assertEqual(self.main("release", "--by", "owner", "--waiver", "NEW: tested by hand"), 2)
        cases.write_text(cases.read_text().replace("R1-comply-x", "R1-comply", 1))
        self.assertEqual(self.main("release", "--by", "owner", "--waiver", "NEW: tested by hand"), 0)

    def test_partial_or_stale_reviews_are_refused(self):
        self.main("rules-confirm", "--by", "compliance")
        self.pack(summary={"cases": 2})                                   # --cases R2-break: a slice, not the suite
        self.assertEqual(self.main("release", "--by", "owner"), 2)
        self.assertIn("partial", self.out)
        self.pack(k=1)
        self.assertEqual(self.main("release", "--by", "owner"), 2)
        self.pack()
        (self.d / "bot/live/system.md").write_text("someone else's release")  # live moved since the review
        self.assertEqual(self.main("release", "--by", "owner"), 2)
        self.assertIn("live bot changed", self.out)

    def test_rollback_is_logged_and_the_same_review_cannot_ship_twice(self):
        import json
        self.main("rules-confirm", "--by", "compliance")
        old = (self.d / "bot/live/system.md").read_text()
        (self.d / "bot/candidate/system.md").write_text(old + "\n- New line.")
        self.pack()
        self.assertEqual(self.main("release", "--by", "owner"), 0)
        self.assertEqual(self.main("rollback", "--by", "owner"), 0)
        self.assertEqual((self.d / "bot/live/system.md").read_text(), old)
        log = [json.loads(l) for l in (self.d / "bot/releases.jsonl").read_text().splitlines()]
        self.assertEqual([(e["action"], e["by"]) for e in log], [("release", "owner"), ("rollback", "owner")])
        self.assertTrue(list((self.d / "bot/history").glob("rolled-back-*.md")))      # what was taken down is kept
        self.assertEqual(self.main("release", "--by", "owner"), 2)                    # same review, already used
        self.assertIn("already released", self.out)

    def test_live_changed_outside_the_gate_shows_on_the_card(self):
        data = review.load("kitebase")
        data["dir"] = self.d
        (self.d / "bot/live.sha256").write_text("0" * 64)
        with mock.patch.object(bot, "chat", return_value={"content": "Sorry, no refund is possible."}), \
                mock.patch.object(grade, "chat", return_value={"content": "YES"}), \
                mock.patch.object(review, "chat", return_value={"content": ""}), \
                mock.patch.object(review, "load", return_value=data), mock.patch.object(review, "ROOT", self.root):
            run_dir, pack = review.review("kitebase", k=1, cases_filter={"R2-break"})
        self.assertTrue(pack["summary"]["live_tampered"])
        self.assertIn("Live was changed outside ThirdUmpire", (run_dir / "card.md").read_text())


if __name__ == "__main__":
    unittest.main()
