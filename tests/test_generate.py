"""Tests for generate_questions, bank_tools and make_profile (no real CLI calls)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import bank_tools  # noqa: E402
import docs_cache  # noqa: E402
import exam_config as cfg  # noqa: E402
import generate_questions as gen  # noqa: E402
import make_profile  # noqa: E402

DEA = cfg.load("data-engineer-associate")
SECTION = "Governance and Security"
DOC = "https://docs.databricks.com/aws/en/data-governance/unity-catalog/"
DOCS = {"7.2": [(DOC, "Unity Catalog: use GRANT to give a privilege on a securable.")]}


def q(prompt: str, answer: str, section: str = SECTION, objective: str = "7.2") -> dict:
    return {"type": "single", "section": section, "objective": objective, "prompt": prompt,
            "choices": [{"id": "a", "text": answer, "correct": True},
                        {"id": "b", "text": "nothing at all", "correct": False}],
            "explanation": "because", "sources": [DOC]}


VACUUM = q("What does VACUUM remove from a Delta table?", "unreferenced old data files")
GRANT = q("Which keyword grants a privilege in Unity Catalog?", "GRANT")
AUTOLOADER = q("Which Auto Loader option sets the source file format?", "cloudFiles.format")
OPTIMIZE = q("Which command compacts small files in a Delta table?", "OPTIMIZE")


class GenerateSectionTests(unittest.TestCase):
    def run_section(self, replies, count, known, verdicts=None):
        calls: list[str] = []
        verify_calls: list[list] = []

        def fake(_cli, _model, system, user, schema, **kw):
            self.assertFalse(kw.get("tools"))                    # single tool-free turn
            self.assertIn(f'<doc url="{DOC}">', user)            # docs are in the prompt
            if "fact checker" in system:
                verify_calls.append(user)
                statuses = verdicts.pop(0) if verdicts else None
                n = user.count('"index"')
                return {"results": [{"index": i, "status": (statuses or ["confirmed"] * n)[i],
                                     "note": "ok"} for i in range(n)]}
            item = schema["properties"]["questions"]["items"]["properties"]
            self.assertEqual(item["sources"]["items"]["enum"], [DOC])  # only given pages citable
            self.assertEqual(item["objective"]["enum"], ["7.2"])
            calls.append(user)
            return {"questions": [dict(x) for x in replies[len(calls) - 1]]}

        with mock.patch.object(gen, "call_claude", side_effect=fake), mock.patch("sys.stderr"):
            kept = gen.generate_section("claude", "opus", DEA, SECTION, count, known, DOCS)
        return kept, calls, verify_calls

    def test_rejects_rewordings_and_tops_up(self):
        known = [dict(VACUUM)]
        kept, calls, _ = self.run_section([
            # reworded copy of a known question + a dup within the reply -> 1 kept
            [q("Which files does VACUUM remove from a Delta table?", "unreferenced old data files"),
             GRANT, q("Which privilege grants Unity Catalog keyword?", "GRANT")],
            [AUTOLOADER, OPTIMIZE],          # top-up round
        ], count=3, known=known)
        self.assertEqual([x["prompt"] for x in kept],
                         [GRANT["prompt"], AUTOLOADER["prompt"], OPTIMIZE["prompt"]])
        self.assertEqual(len(calls), 2)
        self.assertIn("Write 2 exam questions", calls[1])        # asks only for what's missing
        self.assertIn(GRANT["prompt"], calls[1])                 # and shows what it just made
        self.assertIn("7.2 (2 existing)", calls[1])              # objective coverage is shown
        self.assertEqual(len(known), 4)                          # known grows for later sections
        self.assertEqual(kept[0]["verification"]["status"], "confirmed")

    def test_unverified_questions_are_dropped_and_requested_again(self):
        kept, calls, verify_calls = self.run_section(
            [[GRANT, OPTIMIZE], [AUTOLOADER]], count=2, known=[],
            verdicts=[["incorrect", "confirmed"], ["confirmed"]])
        self.assertEqual([x["prompt"] for x in kept], [OPTIMIZE["prompt"], AUTOLOADER["prompt"]])
        self.assertEqual((len(calls), len(verify_calls)), (2, 2))

    def test_local_checks_reject_bad_questions(self):
        no_source = {**GRANT, "sources": []}
        blog = {**OPTIMIZE, "sources": ["https://medium.com/some-blog"]}
        old_name = q("How do you deploy Databricks Asset Bundles to prod?", "bundle deploy -t prod")
        wrong_obj = {**AUTOLOADER, "objective": "2.3"}
        kept, calls, _ = self.run_section([[no_source, blog, old_name, wrong_obj], [GRANT]],
                                          count=1, known=[])
        self.assertEqual([x["prompt"] for x in kept], [GRANT["prompt"]])
        # The next round is told why each question was rejected.
        self.assertIn("REJECTED in the previous round", calls[1])
        self.assertIn("Databricks Asset Bundles → Declarative Automation Bundles", calls[1])
        self.assertIn("no source URL", calls[1])
        self.assertIn("not on an official domain", calls[1])
        self.assertNotIn("REJECTED", calls[0])

    def test_keeps_going_past_four_rounds_while_progressing(self):
        # One usable question per round, interleaved with rejects: 6 rounds for 6 questions.
        good = [q(f"Distinct scenario {i} about privilege{i} securable{i}?", f"answer{i}")
                for i in range(6)]
        replies = [[{**GRANT, "sources": []}, g] for g in good]
        kept, calls, _ = self.run_section(replies, count=6, known=[])
        self.assertEqual(len(kept), 6)
        self.assertEqual(len(calls), 6)

    def test_fact_check_reason_is_fed_back_and_fixed_version_accepted(self):
        kept, calls, _ = self.run_section([[GRANT], [GRANT]], count=1, known=[],
                                          verdicts=[["incorrect"], ["confirmed"]])
        self.assertEqual([x["prompt"] for x in kept], [GRANT["prompt"]])  # not blocked as a dupe
        self.assertIn("fact check incorrect", calls[1])

    def test_gives_up_only_after_stalled_rounds(self):
        stall = gen.MAX_STALLED_ROUNDS
        kept, calls, _ = self.run_section([[dict(VACUUM)]] * stall, count=1, known=[dict(VACUUM)])
        self.assertEqual(kept, [])
        self.assertEqual(len(calls), stall)
        self.assertIn("duplicate", calls[1])

    def test_shows_every_known_question_of_the_section(self):
        known = [q(f"Distinct question number {i} about topic{i}?", f"answer{i}") for i in range(200)]
        known.append(q("Other section question?", "x", section="Implementing CI/CD", objective="5.1"))
        _kept, calls, _ = self.run_section([[GRANT]], count=1, known=known)
        self.assertIn("topic199", calls[0])                      # no cap on the list
        self.assertNotIn("Other section question?", calls[0])


class DocsTests(unittest.TestCase):
    def test_pick_docs_fills_least_covered_within_budget(self):
        docs = {"a": [("u1", "x" * 60)], "b": [("u1", "x" * 60), ("u2", "y" * 30)],
                "c": [("u3", "z" * 50)]}
        chosen, pages = gen.pick_docs(["c", "missing", "a", "b"], docs, budget=100)
        self.assertEqual(chosen, ["c"])                          # a (60) would exceed 100
        chosen, pages = gen.pick_docs(["a", "b", "c"], docs, budget=100)
        self.assertEqual((chosen, list(pages)), (["a", "b"], ["u1", "u2"]))  # shared page once
        chosen, _ = gen.pick_docs(["a"], docs, budget=1)         # first one always fits
        self.assertEqual(chosen, ["a"])

    def test_html_to_page_keeps_article_text_and_links(self):
        html = """<html><body><nav><a href="/x">Menu</a></nav><article>
            <h1>Auto Loader</h1><script>var a=1;</script><p>Use <code>cloudFiles</code> &amp; more.</p>
            <a href="options#schema">Auto Loader options</a></article><footer>(c)</footer></body></html>"""
        page = docs_cache.html_to_page(html, "https://docs.databricks.com/aws/en/auto-loader/")
        self.assertEqual(page["text"], "# Auto Loader\nUse cloudFiles & more.\nAuto Loader options")
        self.assertEqual(page["links"], [["https://docs.databricks.com/aws/en/auto-loader/options",
                                          "Auto Loader options"]])

    def test_parse_index(self):
        text = ("# Docs\n- [Auto Loader](https://docs.databricks.com/ingestion/auto-loader/) - Ingest files.\n"
                "- [API](https://docs.databricks.com/api/llms.txt)\nnot a link\n")
        self.assertEqual(docs_cache.parse_index(text), [{
            "url": "https://docs.databricks.com/ingestion/auto-loader/", "title": "Auto Loader",
            "desc": "Ingest files."}])

    def test_page_refuses_unofficial_urls(self):
        with self.assertRaises(ValueError):
            docs_cache.page(DEA, "https://medium.com/some-blog")


class ReverifyTests(unittest.TestCase):
    def test_rechecks_banks_and_flags_only_what_the_docs_now_contradict(self):
        import dataclasses
        import json
        import tempfile
        import score
        with tempfile.TemporaryDirectory() as tmp:
            prof = dataclasses.replace(DEA, dir=tmp)
            os.makedirs(prof.banks_dir)
            unsourced = {**OPTIMIZE, "sources": []}
            bank = os.path.join(prof.banks_dir, "b.json")
            with open(bank, "w") as f:
                json.dump({"questions": [GRANT, VACUUM, AUTOLOADER, unsourced]}, f)
            verdicts = {GRANT["prompt"]: "incorrect", VACUUM["prompt"]: "confirmed",
                        AUTOLOADER["prompt"]: "confirmed"}

            def fake(_cli, _model, _system, user, _schema, **_kw):
                self.assertIn(f'<doc url="{DOC}">', user)
                qs = json.loads(user.split("Questions:\n", 1)[1])
                self.assertNotIn(unsourced["prompt"], [x["prompt"] for x in qs])
                return {"results": [{"index": x["index"], "status": verdicts[x["prompt"]],
                                     "note": "page says so"} for x in qs]}

            with mock.patch.object(score, "RESULTS_ROOT", os.path.join(tmp, "results")), \
                    mock.patch.object(docs_cache, "page", return_value={"text": "doc text"}), \
                    mock.patch.object(gen, "call_claude", side_effect=fake), \
                    mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                rdir = score.results_dir(prof.slug)
                score.set_flag(rdir, VACUUM, gen.REVERIFY_FLAG + "was wrong last month")
                score.set_flag(rdir, AUTOLOADER, "I think choice b is also right")
                self.assertEqual(gen.reverify("claude", "sonnet", prof), 1)
                flags = {v["prompt"]: v["reason"] for v in score.load_flags(rdir).values()}
                # Second run the same day re-checks nothing.
                self.assertEqual(gen.reverify("claude", "sonnet", prof), 0)

            self.assertEqual(flags, {GRANT["prompt"]: gen.REVERIFY_FLAG + "page says so",
                                     AUTOLOADER["prompt"]: "I think choice b is also right"})
            with open(bank) as f:
                saved = json.load(f)["questions"]
            self.assertEqual([(x.get("verification") or {}).get("status") for x in saved],
                             ["incorrect", "confirmed", "confirmed", None])


class BankToolsTests(unittest.TestCase):
    def test_outdated_terms(self):
        self.assertEqual(bank_tools.outdated_terms("Use Delta Live Tables for this", DEA),
                         ["Delta Live Tables → Lakeflow Declarative Pipelines"])
        # "(formerly X)" is how the guide itself names renames: allowed.
        self.assertEqual(bank_tools.outdated_terms(
            "Declarative Automation Bundles (formerly Databricks Asset Bundles)", DEA), [])
        self.assertEqual(bank_tools.outdated_terms("import dlt  # the Python module", DEA), [])
        self.assertTrue(bank_tools.outdated_terms("a DLT pipeline", DEA))

    def test_shipped_banks_are_valid(self):
        import score
        banks = sorted(f for f in os.listdir(DEA.banks_dir) if f.endswith(".json"))
        self.assertTrue(banks)
        for name in banks:
            for question in score.load_json(os.path.join(DEA.banks_dir, name))["questions"]:
                self.assertEqual(bank_tools.check_question(question, DEA), [], f"{name}:{question['id']}")


class FocusTests(unittest.TestCase):
    def test_weak_sections_get_more_questions(self):
        history = [{"sections": {SECTION: [0, 7], "Implementing CI/CD": [5, 5]}}]
        w = gen.focus_weights(DEA, history)
        self.assertEqual(w["Implementing CI/CD"], 0)            # perfect: nothing extra
        self.assertEqual(w[SECTION], 15)                         # all wrong: full weight
        dist = cfg.distribute_questions(w, 20)
        self.assertEqual(sum(dist.values()), 20)
        self.assertEqual(dist["Implementing CI/CD"], 0)

    def test_all_perfect_falls_back_to_blueprint(self):
        history = [{"sections": {n: [3, 3] for n, _ in DEA.sections}}]
        self.assertEqual(gen.focus_weights(DEA, history), DEA.weights)


class ProfileTests(unittest.TestCase):
    def test_build_profile_numbers_objectives_and_defaults_pass_mark(self):
        extracted = {"title": "X", "guideVersion": "2026-01-01", "totalQuestions": 60,
                     "unscoredMax": 0, "timeLimitMinutes": 120, "passingScore": None,
                     "sections": [{"name": "One", "weight": 40, "objectives": ["a", "b"]},
                                  {"name": "Two", "weight": 60, "objectives": ["c"]}],
                     "terminology": []}
        prof = make_profile.build_profile(extracted)
        self.assertEqual(prof["passingScore"], make_profile.DEFAULT_PASSING)
        self.assertEqual([o["id"] for s in prof["sections"] for o in s["objectives"]], ["1.1", "1.2", "2.1"])
        loaded = cfg.profile_from_dict("x", "/tmp/x", prof)
        self.assertEqual(loaded.distribute(), {"One": 24, "Two": 36})

    def test_weights_must_sum_to_100(self):
        with self.assertRaises(ValueError):
            cfg.profile_from_dict("x", "/tmp/x", {"title": "X", "totalQuestions": 1, "timeLimitMinutes": 1,
                                                  "sections": [{"name": "A", "weight": 50}]})

    def test_repo_profile_matches_guide_blueprint(self):
        self.assertEqual(DEA.distribute(), {
            "Databricks Intelligence Platform": 3, "Data Ingestion and Loading": 9,
            "Data Transformation and Modeling": 10, "Working with Lakeflow Jobs": 7,
            "Implementing CI/CD": 5, "Troubleshooting, Monitoring, and Optimization": 4,
            "Governance and Security": 7})


if __name__ == "__main__":
    unittest.main()
