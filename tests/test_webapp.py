"""Tests for the browser exam server's session logic (webapp/server.py)."""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webapp"))

import server  # noqa: E402

BANK = {
    "title": "Mini",
    "passingScore": 50,
    "sections": [{"name": "A", "weight": 60}, {"name": "B", "weight": 40}],
    "questions": [
        {"id": "x", "type": "single", "section": "A", "prompt": "One?",
         "choices": [{"id": "a", "text": "yes", "correct": True},
                     {"id": "b", "text": "no", "correct": False}]},
        {"id": "y", "type": "multiple", "section": "B", "prompt": "Select TWO",
         "choices": [{"id": "a", "text": "p", "correct": True},
                     {"id": "b", "text": "q", "correct": True},
                     {"id": "c", "text": "r", "correct": False}]},
    ],
}


PROFILE = {
    "title": "Mini Exam", "guideVersion": "2026-01-01", "totalQuestions": 2, "unscoredMax": 1,
    "timeLimitMinutes": 4, "passingScore": 50,
    "sections": [{"name": "A", "weight": 60, "objectives": [{"id": "1.1", "text": "a"}]},
                 {"name": "B", "weight": 40, "objectives": [{"id": "2.1", "text": "b"}]}],
}
DEA = server.cfg.load("data-engineer-associate")


def make_exam_dir(root, banks, profile=PROFILE, slug="mini"):
    """exams/<slug>/profile.json + banks/<name>.json under `root`; returns the exams dir."""
    exams = os.path.join(root, "exams")
    os.makedirs(os.path.join(exams, slug, "banks"))
    with open(os.path.join(exams, slug, "profile.json"), "w") as f:
        json.dump(profile, f)
    for name, bank in banks.items():
        with open(os.path.join(exams, slug, "banks", name), "w") as f:
            json.dump(bank, f)
    return exams


class TmpServer:
    """Point the server at a temporary exams/ and results/ tree."""

    def use(self, root, exams):
        self._saved = (server.EXAMS_DIR, server.RESULTS_DIR, server.SESSIONS_DIR)
        server.EXAMS_DIR = exams
        server.RESULTS_DIR = os.path.join(root, "results")
        server.SESSIONS_DIR = os.path.join(root, "results", "sessions")

    def restore(self):
        server.EXAMS_DIR, server.RESULTS_DIR, server.SESSIONS_DIR = self._saved


def correct_answer(q):
    ids = [c["id"] for c in q["choices"] if c["correct"]]
    return ids if q["type"] == "multiple" else ids[0]


class SessionTests(unittest.TestCase, TmpServer):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exams = make_exam_dir(self.tmp.name, {"mini.json": BANK})
        self.use(self.tmp.name, self.exams)

    def tearDown(self):
        self.restore()
        self.tmp.cleanup()

    def pools(self):
        return server.list_exams()[0]["pools"]

    def test_pools_list_single_bank(self):
        exams = server.list_exams()
        self.assertEqual([(x["slug"], x["title"]) for x in exams], [("mini", "Mini Exam")])
        pools = exams[0]["pools"]
        # One bank still gets the rotating "mixed" exam (no master bank needed).
        self.assertEqual([p["id"] for p in pools], ["mixed", "bank:mini.json"])
        self.assertEqual(pools[1]["minutes"], 4)  # the profile's pace: 4 min / 2 q
        self.assertEqual(pools[0]["unscoredMax"], 1)

    def test_pool_date_prefers_generated_at_then_mtime(self):
        path = os.path.join(self.exams, "mini", "banks", "mini.json")
        os.utime(path, (1_700_000_000, 1_700_000_000))
        self.assertEqual(self.pools()[0]["createdAt"], 1_700_000_000)
        with open(path, "w") as f:
            json.dump({**BANK, "generatedAt": "2026-11-16T08:00:00+00:00"}, f)
        self.assertEqual(self.pools()[0]["createdAt"],
                         datetime.fromisoformat("2026-11-16T08:00:00+00:00").timestamp())

    def test_stale_banks_are_skipped(self):
        with open(os.path.join(self.exams, "mini", "banks", "old.json"), "w") as f:
            json.dump({**BANK, "guideVersion": "2020-01-01"}, f)
        with mock.patch("sys.stderr"):
            self.assertNotIn("bank:old.json", [p["id"] for p in self.pools()])

    def test_unknown_or_traversal_bank_rejected(self):
        with self.assertRaises(ValueError):
            server.create_session("mini", "bank:../secrets.json")
        with self.assertRaises(ValueError):
            server.create_session("../mini", "mixed")

    def test_unscored_items_hidden_until_review_and_not_scored(self):
        s = server.create_session("mini", "mixed", seed=1, unscored=True)
        extra = [q for q in s["exam"]["questions"] if q.get("unscored")]
        self.assertLessEqual(len(extra), 1)
        self.assertNotIn("unscored", json.dumps(server.public_view(s)))
        # Force one unscored item to check the scoring.
        s["exam"]["questions"][0]["unscored"] = True
        q0 = s["exam"]["questions"][0]
        server.update_progress(s, {"answers": {q0["id"]: correct_answer(q0)}})
        server.finish_session(s, "submitted")
        r = s["result"]
        self.assertEqual((r["total"], r["rawCorrect"], r["unscored"]), (1, 0, 1))
        self.assertTrue(r["review"][0]["unscored"])

    def test_report_question_excludes_it_from_mixed_exams(self):
        s = server.create_session("mini", "bank:mini.json", seed=1)
        with self.assertRaises(ValueError):                 # not while running
            server.report_question(s, "q1", "wrong")
        server.finish_session(s, "submitted")
        self.assertTrue(server.report_question(s, "q1", "answer is outdated"))
        view = server.public_view(s)
        self.assertTrue(next(x for x in view["result"]["review"] if x["id"] == "q1")["reported"])
        self.assertEqual(self.pools()[0]["poolSize"], 1)
        mixed = server.create_session("mini", "mixed", seed=2)
        self.assertNotIn(s["exam"]["questions"][0]["prompt"],
                         [q["prompt"] for q in mixed["exam"]["questions"]])
        self.assertFalse(server.report_question(s, "q1", None))   # undo
        self.assertEqual(self.pools()[0]["poolSize"], 2)

    def test_running_view_hides_answers(self):
        s = server.create_session("mini", "bank:mini.json", seed=1)
        view = server.public_view(s)
        blob = json.dumps(view)
        self.assertNotIn("correct", blob)
        self.assertNotIn("explanation", blob)
        multi = next(q for q in view["questions"] if q["type"] == "multiple")
        self.assertEqual(multi["selectCount"], 2)

    def test_answers_are_sanitized(self):
        s = server.create_session("mini", "bank:mini.json", seed=1)
        ids = [q["id"] for q in s["exam"]["questions"]]
        server.update_progress(s, {"answers": {ids[0]: "zz", ids[1]: ["a", "nope"], "q99": "a"},
                                   "flagged": [ids[0], "bogus"], "current": 99})
        self.assertNotIn("q99", s["answers"])
        self.assertEqual(s["flagged"], [ids[0]])
        self.assertEqual(s["current"], 1)

    def test_submit_grades_weighted_and_writes_reports(self):
        s = server.create_session("mini", "bank:mini.json", seed=3)
        single = next(q for q in s["exam"]["questions"] if q["type"] == "single")
        multi = next(q for q in s["exam"]["questions"] if q["type"] == "multiple")
        server.update_progress(s, {"answers": {single["id"]: correct_answer(single),
                                               multi["id"]: ["a"]}})
        server.finish_session(s, "submitted")
        r = server.public_view(s)["result"]
        self.assertEqual(r["rawCorrect"], 1)
        self.assertEqual(r["weightedScore"], 60.0)   # section A (60%) right, B wrong
        self.assertTrue(r["passed"])
        rdir = server.rdir("mini")
        self.assertTrue(os.path.exists(server.score.retry_path(rdir)))
        self.assertIn("retry", [p["id"] for p in self.pools()])
        history = server.score.load_history(rdir)
        self.assertEqual(history[-1]["sections"], {"A": [1, 1], "B": [0, 1]})
        # Persisted and listed as history.
        self.assertEqual(server.load_session(s["id"])["status"], "finished")
        self.assertEqual(server.list_attempts()[0]["score"], 60.0)

    def test_delete_finished_attempt(self):
        s = server.create_session("mini", "bank:mini.json", seed=1)
        with self.assertRaises(ValueError):          # running exams can't be deleted
            server.delete_session(s["id"])
        server.finish_session(s, "submitted")
        server.delete_session(s["id"])
        self.assertEqual(server.list_attempts(), [])
        with self.assertRaises(KeyError):
            server.delete_session(s["id"])
        with self.assertRaises(KeyError):            # malformed ids never touch the disk
            server.delete_session("../../etc/passwd")

    def test_timeout_auto_grades(self):
        s = server.create_session("mini", "bank:mini.json")
        s["deadline"] = time.time() - server.GRACE_SECONDS - 1
        server.expire_if_due(s)
        self.assertEqual(s["status"], "finished")
        self.assertEqual(s["result"]["reason"], "timeout")
        self.assertEqual(s["result"]["rawCorrect"], 0)


def _blueprint_banks(per_section: int, banks: int = 2) -> list[dict]:
    """Synthetic banks: `per_section` distinct questions per official section,
    each one present in every bank in slightly different words."""
    out = []
    for b in range(banks):
        questions = []
        for s_idx, (section, _w) in enumerate(DEA.sections):
            for n in range(per_section):
                topic = f"topic{s_idx}x{n} alpha{s_idx}x{n} beta{s_idx}x{n} gamma{s_idx}x{n}"
                prompt = (f"Which statement about {topic} is right?" if b % 2 == 0
                          else f"Pick the right statement regarding {topic}.")
                questions.append({
                    "id": f"{s_idx}-{n}", "type": "single", "section": section, "prompt": prompt,
                    "choices": [{"id": "a", "text": f"answer {topic}", "correct": True},
                                {"id": "b", "text": "something else", "correct": False}]})
        out.append({"path": f"bank{b}.json", "exam": {"questions": questions}})
    return out


class RotationTests(unittest.TestCase):
    def test_reworded_duplicates_count_once(self):
        banks = _blueprint_banks(per_section=10)
        groups = server.score.unique_question_groups(banks)
        self.assertEqual(len(groups), 10 * len(DEA.sections))
        self.assertTrue(all(len(g) == 2 for g in groups))

    def test_every_exam_is_unique_and_rotates_through_the_pool(self):
        banks = _blueprint_banks(per_section=10)   # 70 unique questions
        groups = server.score.unique_question_groups(banks)
        group_of = {k: i for i, g in enumerate(groups) for k in server.score.group_keys(g)}
        rng, used, seen = random.Random(3), set(), set()
        for _ in range(12):
            exam, shortfalls, _ = server.score.build_combined_exam(banks, DEA, 45, rng, used)
            ids = {group_of[server.score._norm_prompt(q["prompt"])] for q in exam["questions"]}
            self.assertEqual(len(exam["questions"]), 45)
            self.assertEqual(len(ids), 45)                       # no reworded repeats
            self.assertEqual(shortfalls, {})
            new = ids - seen
            if len(new) < 45:                                    # round just ended:
                self.assertEqual(seen | new, set(range(len(groups))))  # all were served
                seen = ids - new
            else:
                seen |= ids

    def test_unscored_extras_come_on_top(self):
        banks = _blueprint_banks(per_section=10)
        exam, _, _ = server.score.build_combined_exam(banks, DEA, 45, random.Random(1), set(), unscored=7)
        self.assertEqual(len(exam["questions"]), 52)
        self.assertEqual(sum(1 for q in exam["questions"] if q.get("unscored")), 7)
        self.assertEqual(len({server.score._norm_prompt(q["prompt"]) for q in exam["questions"]}), 52)

    def test_mixed_session_persists_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(DEA.dir, "profile.json")) as f:
                dea = json.load(f)
            exams = make_exam_dir(tmp, {b["path"]: b["exam"] for b in _blueprint_banks(per_section=15)},
                                  profile=dea, slug="dea")       # 105 unique questions
            srv = TmpServer()
            srv.use(tmp, exams)
            try:
                first = server.create_session("dea", "mixed", seed=1)
                second = server.create_session("dea", "mixed", seed=1)  # same seed, still new questions
                prompts = lambda s: {q["prompt"] for q in s["exam"]["questions"]}
                self.assertFalse(prompts(first) & prompts(second))
                mixed = server.list_exams()[0]["pools"][0]
                self.assertEqual((mixed["poolSize"], mixed["poolFresh"]), (105, 15))
            finally:
                srv.restore()


if __name__ == "__main__":
    unittest.main()
