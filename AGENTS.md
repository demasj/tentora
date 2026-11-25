# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, Copilot and others) working in this repository.

## Project

**Tentora** — a browser-based exam-practice application that loads exam
question banks and simulates a real certification exam.

Users study for IT certification exams. They load a question bank, take a
timed exam that mimics the real testing experience, and review their results.

## Architecture (what actually exists)

Python 3 stdlib only — no pip dependencies. The browser app is a stdlib
`http.server` + vanilla JS (`webapp/`), not React. Everything is driven via the
`Makefile`; `EXAM=<slug>` selects the exam (default `data-engineer-associate`).

- `exams/<slug>/profile.json` — **single source of truth per exam**: title,
  scored question count, `unscoredMax`, time limit, pass mark, sections with
  weights and the guide's objectives (verbatim, ids like `2.3`), `terminology`
  (outdated → current product names), `sourceDomains`, `guideVersion`.
  Never hardcode weights, counts or names elsewhere; load via
  `scripts/exam_config.py` (`cfg.load(slug)` → `Profile`).
- `exams/<slug>/guide.pdf` — the official exam guide the profile came from (local only, git-ignored).
- `exams/<slug>/banks/*.json` — question banks (open JSON, `docs/format.md`).
  No master bank: the "full exam" draws `totalQuestions` unique questions from
  all banks (reworded duplicates count once), rotating via
  `results/<slug>/rotation.json`, never drawing reported questions
  (`results/<slug>/flags.json`), skipping banks of another `guideVersion`.
- `scripts/score.py` — pure grading + **topic-weighted** pass/fail, combined
  exams (incl. unscored items), rotation, flags, history (`history.jsonl`),
  terminal `take` / `grade`.
- `scripts/generate_questions.py` — grounded generation via the `claude` CLI:
  docs are fetched locally (`scripts/docs_cache.py`: llms.txt → objective→page map,
  cached in `results/<slug>/docs/`) and pasted into tool-free calls; author (may
  only cite the given pages + objective) → local checks (`bank_tools.check_question`)
  → dedupe → independent verify call on the cited page text; only confirmed
  questions are kept. `--focus weak`. `make reverify` (`--reverify`) re-checks
  banks against today's docs and flags what is now incorrect.
- `scripts/make_profile.py` — guide PDF → `profile.json` (review before commit).
- `scripts/bank_tools.py` — `plan` (coverage per objective) and `validate`.
- `webapp/server.py` — multi-exam sessions; the server owns answers and the
  clock; per-exam results under `results/<slug>/`, sessions in `results/sessions/`.
- `tests/` — `make test` (unittest). Run it before declaring a change done.

The exam UI must keep feeling like the real test centre: no study aids during
the exam, unscored items unidentified, answers only after submitting.

Scoring: per-section percent-correct combined by section weight (weights
renormalized over sections that have questions); unscored items never count.

## Exam content rules

These apply to every exam profile, bank and generated question.

- Ground all content in the exam provider's official sources only: the domains
  in the profile's `sourceDomains` (for Databricks: docs.databricks.com and
  Databricks Academy). No third-party blogs, tutorials or forum posts.
- Every question cites the official doc page URL(s) it is based on.
- Use only current product names. The profile's `terminology` lists renames
  (outdated → current). When the guide uses a name you are unsure about, check
  the official docs before using it, and add any rename to `terminology`.
- Never invent objectives: every question maps to an objective id from the guide.
- If a claim cannot be confirmed in the official docs, say so explicitly and
  point to where in the docs it should be verified; never guess.
- Generated questions are AI-written practice material, never real exam
  questions. Do not reproduce exam content covered by a candidate agreement.

`exams/<slug>/guide.pdf` is the official exam guide for the objectives and
structure; its distilled form is `exams/<slug>/profile.json`. Guides and their
verbatim sample questions are the provider's copyright: they stay local
(git-ignored) and are never committed.
