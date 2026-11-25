# Changelog

All notable changes to Tentora are listed here. Versions follow
[Semantic Versioning](https://semver.org/).

## 0.1.0 — 2026-11-25

First public release.

### Exams
- Browser exam (stdlib server, vanilla JS) that feels like the test centre:
  server-side clock, mark for review, strike-out, overview screen, unscored items.
- Terminal exam (`make take`, `make retry`) and grading of answer files (`make grade`).
- Topic-weighted pass/fail using the official section weights.

### Questions
- Exam profiles from official guide PDFs (`make profile`).
- Docs-grounded, independently verified question generation (`make generate`),
  with `FOCUS=weak` to target the sections you miss most.
- `make reverify` re-checks banks against today's docs and retires questions
  that are now wrong.
- Databricks Certified Data Engineer Associate profile with 147 generated questions in 4 banks.

### Project
- README with demo, and documentation in `docs/`: getting started, question
  generation, scoring and the bank format.
- Contributing guide, code of conduct, security policy, issue and pull request
  templates.
- CI on Python 3.12 and 3.13, with Dependabot keeping GitHub Actions up to date.
- `AGENTS.md` with guidance for AI coding agents.
