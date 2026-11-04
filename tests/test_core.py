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
                mock.patch.object(review, "load", return_value=data), mock.patch.object(review, "ROOT", Path(tempfile.mkdtemp())):
            run_dir, pack = review.review("kitebase", k=2, cases_filter={"R2-break", "R1-comply"})
        self.assertEqual(pack["verdict"], "OUT")
        self.assertEqual(pack["summary"]["regressions"], ["R2-break"])
        self.assertTrue((run_dir / "card.md").read_text().startswith("*ThirdUmpire review*"))
        self.assertEqual(len(pack["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
