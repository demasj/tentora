# Getting started

## Requirements

- Python 3.12+ (stdlib only, so there's nothing to `pip install`)
- `make`, and Docker for `make web` (optional)
- For `make generate` / `make profile` only: the [`claude` CLI](https://claude.com/claude-code), logged in

## Quick start

```bash
make plan        # blueprint + how many questions each guide objective has
make generate    # 45 new questions, grounded in docs.databricks.com and verified
make web         # sit the exam in the browser → http://localhost:8080
make take        # ...or in the terminal
```

`EXAM=<slug>` picks the exam for every target (default `data-engineer-associate`);
`make exams` lists them. Running `make` with no target lists all targets.


## In the browser (`make web`)

The home screen lists every exam, each with its pools: the **full exam** (45
scored questions mixed from all banks, rotating so you get questions you haven't
seen before), single banks, and a retry of your wrong answers.

The exam itself works like the test centre. You read the instructions and agree
to them, then the clock starts. The timer runs on the server. It keeps going
when the tab closes, and when time runs out your answers are submitted
automatically. You can mark questions for review, use an overview screen, strike
out answers with a right-click and change the font size. Keyboard: `A`–`F`
choose an answer, `←`/`→` move, `M` marks, `O` opens the overview.

- **Unscored items.** Like the real exam, the full exam can include up to N
  extra questions that don't count (10 for the DE Associate). They are not
  identified until the review, and the time limit is the same as the real exam's.
  You switch this on or off on the instructions screen.
- **Score report**: PASS/FAIL, then your results per section. In the review,
  every question shows its guide objective and links to the documentation pages
  it is based on.
- **Report a problem.** If a question is wrong or outdated, click *Report a problem with
  this question* in the review. It is stored in `results/<slug>/flags.json`,
  excluded from future exams and counted in `make plan`. Click again to undo.

The interface is in English by default; the language menu switches to Dutch at
any time. The correct answers stay on the server until you finish. The container
mounts `exams/` read-only and `results/` read-write, so new banks show up
straight away and results are shared with `make take`. The port is bound to
`127.0.0.1` only; `WEB_PORT=9000 make web` picks another.

## In the terminal

```bash
make take                    # full exam from all banks of EXAM
make take UNSCORED=5         # with 5 unscored items mixed in
make take BANKS=exams/data-engineer-associate/banks/generated-1.json   # one bank as-is
make take SEED=42            # repeatable mix and order
make retry                   # only the questions you got wrong last time
make grade BANK=exams/<slug>/banks/generated-1.json ANSWERS=my-answers.json
```

Type `?` at a question to see the answer and explanation before you answer.
After the exam you get the score per section and a review of the wrong answers,
with sources. The review is also saved as `results/<slug>/<timestamp>.md`.


## Project layout

```
Makefile                        # entry point for everything above
exams/<slug>/profile.json       # exam facts, sections, weights, objectives, outdated names
exams/<slug>/guide.pdf          # the official exam guide (your local copy, git-ignored)
exams/<slug>/banks/*.json       # question banks (open JSON format, docs/format.md)
scripts/exam_config.py          # loads profiles (single source of truth)
scripts/score.py                # grading, combined exams, rotation, flags, history, take/grade
scripts/generate_questions.py   # grounded + verified question generation
scripts/make_profile.py         # exam guide PDF → profile.json
scripts/bank_tools.py           # plan (coverage) and validate
webapp/                         # browser exam (stdlib server + vanilla JS)
results/<slug>/                 # your reports, retry bank, rotation, flags, history (git-ignored)
```

