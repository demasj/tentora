# Question generation

How Tentora turns an official exam guide into a profile, and the official docs into verified practice questions. You need the [`claude` CLI](https://claude.com/claude-code), logged in.

## Adding another exam

```bash
make profile GUIDE=~/Downloads/Data-Engineer-Professional-Exam-Guide.pdf EXAM=data-engineer-professional
# review exams/data-engineer-professional/profile.json, then:
make generate EXAM=data-engineer-professional
```

`make profile` copies the PDF to `exams/<slug>/guide.pdf` and has Claude read it.
Claude extracts the question count, time limit, unscored items, the sections
with their weights, the objectives (verbatim) and every "(formerly X)" rename.
Review the profile before you commit it. It drives generation, scoring and the
browser exam. The guide states no pass mark? Then the profile uses 70%; change
`passingScore` to practise against a stricter bar (the DE Associate profile uses 80%).

When Databricks publishes a new guide, run `make profile ... --force` again (or edit
`guideVersion` and the objectives). Banks written for an older guide version are skipped.

## How questions are made (`make generate`)

Each section gets its share of `COUNT` (by weight). Claude never browses the web
itself. `scripts/docs_cache.py` fetches the official docs over plain HTTP and caches
them in `results/<exam>/docs/` for 30 days (delete that folder to refresh). Each
Claude call then gets the page text in its prompt and answers in one turn, with no
tools. That is far cheaper than an agentic search session.

0. **Map** (once, cached in `docs/map.json`): every guide objective is linked to
   1-3 doc pages. The links come from the site's own index
   ([llms.txt](https://docs.databricks.com/llms.txt)) and the detail pages those
   index pages link to (2 Sonnet calls).
1. **Author**: Claude gets the pages of the objectives with the fewest questions
   and may only write about, and cite, those pages (the JSON schema only allows
   their URLs). Every question names the guide objective it tests.
2. **Local checks**: valid structure, an objective of the right section, sources
   on official domains only, no outdated product names (the profile's
   `terminology`, such as *Databricks Asset Bundles → Declarative Automation Bundles*),
   and not a near-duplicate of any existing question.
3. **Verify**: a separate Claude call (Sonnet by default, `VERIFY_MODEL=opus` to
   change it) gets the text of the cited pages and confirms the answer and the
   distractors. Only confirmed questions are kept.

Each section prints `spent so far: $…`, the API-equivalent cost of the calls so far.

### Keeping questions current (`make reverify`)

Doc pages are refetched daily (free, plain HTTP). The objective→page map is
rebuilt every 30 days or when `guideVersion` changes. `make reverify` re-checks
every sourced question in the banks against today's pages (Sonnet, one call per
batch) and updates each `verification`. Questions the docs now contradict are
reported in `results/<exam>/flags.json`, so exams stop drawing them. A later
`confirmed` lifts only these automatic reports, never your own. `unconfirmed`
questions are listed for you to check and are not reported. Questions already
checked today are skipped.

Rejected questions are requested again, and the next round is told why each one
was rejected (for example *outdated name: Databricks Asset Bundles → Declarative
Automation Bundles* or *fact check incorrect: …*). There is no fixed number of
rounds: a section keeps going until it has its full count. It only gives up
after 5 rounds in a row without a single usable question (for example when the
`claude` CLI is logged out). Then it reports the shortfall and exits non-zero.

| Variable | Default | Meaning |
|----------|---------|---------|
| `COUNT` | exam size (45) | How many new questions. |
| `FOCUS` | `blueprint` | `weak` shifts the section mix towards the sections you missed most in your last 5 attempts. |
| `VERIFY` | `1` | `VERIFY=0` skips step 3 (faster; questions are marked `unverified`). |
| `MODEL` | `opus` | Model for the `claude` CLI; a full id pins one. |
| `OUT` | `exams/<slug>/banks/generated-<n>.json` | Output file (never overwrites). |

