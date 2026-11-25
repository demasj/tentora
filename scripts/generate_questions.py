#!/usr/bin/env python3
"""Generate fresh practice questions for an exam profile, grounded in the docs.

Uses the local Claude Code CLI to author exam-realistic multiple-choice
questions for one exam profile (exams/<slug>/profile.json), distributed across
its sections by weight and tagged with the guide objective each one tests.

Grounding: the docs are fetched locally (docs_cache.py, from the source
domains' llms.txt, cached under results/<slug>/docs/) and pasted into the
prompts, so every CLI call is a single tool-free turn:

0. **Map** (once, cached) — each guide objective is mapped to 1-3 doc pages:
   first to overview pages from llms.txt, then to the detail pages they link to.
1. **Author** — gets the pages of the least-covered objectives and may only
   cite those pages' URLs (enforced by the JSON schema).
2. **Verify** — an independent call gets the text of every cited page and checks
   the correct answer, the distractors and the product names against it. Only
   questions it confirms are kept; the rest are re-requested — together with the
   reason each one was rejected — until every section has its full count.

Every kept question is also checked locally (bank_tools.check_question: valid
structure, an objective of its section, sources on official domains, no
outdated product names) and against all existing banks with the exam's
near-duplicate test (score.is_duplicate).

Usage:
    python scripts/generate_questions.py                      # 45 for the default exam
    python scripts/generate_questions.py --exam data-engineer-associate --count 20
    python scripts/generate_questions.py --focus weak         # target your weak sections
    python scripts/generate_questions.py --no-verify          # skip step 2 (faster, unverified)

Within each section the model is shown how many questions every objective
already has and asked to fill the least-covered objectives first.

Auth: this shells out to the `claude` CLI (Claude Code), reusing whatever login
that CLI already has. No ANTHROPIC_API_KEY or extra SDK is required.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

import bank_tools
import docs_cache
import exam_config as cfg
import score

# The `claude` CLI resolves this alias to the newest Opus model, so we never pin a version.
DEFAULT_MODEL = "opus"

# Attempts (history.jsonl lines) that --focus weak looks at.
FOCUS_WINDOW = 5


def question_schema(objectives: list[str], urls: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["questions"],
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "objective", "prompt", "choices", "explanation", "sources"],
                    "properties": {
                        "type": {"type": "string", "enum": ["single", "multiple", "trueFalse"]},
                        "objective": {"type": "string", "enum": objectives},
                        "prompt": {"type": "string"},
                        "explanation": {"type": "string"},
                        "sources": {"type": "array", "minItems": 1,
                                    "items": {"type": "string", "enum": urls}},
                        "choices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id", "text", "correct"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "text": {"type": "string"},
                                    "correct": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "status", "note"],
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": ["confirmed", "unconfirmed", "incorrect"]},
                    "note": {"type": "string"},
                },
            },
        }
    },
}


def _terminology(profile: cfg.Profile) -> str:
    return "\n".join(f"  - say \"{t['new']}\", never \"{t['old']}\"" for t in profile.terminology)


def author_system_prompt(profile: cfg.Profile) -> str:
    domains = ", ".join(profile.source_domains)
    return (
        f"You are an expert author of practice questions for the {profile.title} exam "
        f"(exam guide version {profile.guide_version}). You write accurate, exam-realistic "
        "scenario-based multiple-choice questions in the style of the official sample questions.\n"
        f"Grounding (mandatory): the official documentation ({domains}) is given in <doc url=...> "
        "blocks. Base every question ONLY on what those pages say — not on memory, blogs, forums or "
        "third-party tutorials. Each question's 'sources' lists the 1-3 doc urls that support the "
        "correct answer. If the pages do not confirm a fact, do not write a question about it.\n"
        "Scope: test only the exam objective given in 'objective'; never invent objectives or test "
        "features outside the guide. Use only current product names and behaviours:\n"
        f"{_terminology(profile)}\n"
        "Rules for each question:\n"
        "- 'single': exactly ONE choice has correct=true, provide 4 plausible choices.\n"
        "- 'multiple': TWO or more choices have correct=true; state 'Select TWO' (or the count) "
        "in the prompt; provide 4-5 choices.\n"
        "- 'trueFalse': exactly two choices with ids 'true' and 'false', one correct (use sparingly; "
        "the real exam is single/multiple choice).\n"
        "- Use choice ids 'a','b','c','d','e' for single/multiple.\n"
        "- 'explanation' says why the answer is correct and why the distractors are wrong.\n"
        "- Distractors must be plausible but clearly wrong to someone who knows the material.\n"
        "- Do NOT duplicate or trivially reword any of the provided existing questions."
    )


def verify_system_prompt(profile: cfg.Profile) -> str:
    return (
        f"You are an independent fact checker for {profile.title} practice questions. The text of "
        "every cited page is given in <doc url=...> blocks (official documentation, fetched today). "
        "Judge each question only against the docs of its own 'sources':\n"
        "- 'confirmed': the pages support the marked correct answer(s), every distractor is wrong, "
        "and product names are current.\n"
        "- 'incorrect': the pages contradict the marked answer, or a distractor is also correct.\n"
        "- 'unconfirmed': the pages do not settle it (missing, unrelated, or ambiguous).\n"
        "Be strict: when in doubt, answer 'unconfirmed'. 'note' is one sentence citing what the "
        "page says. Return one result per question, by its index."
    )


def find_claude_cli() -> str:
    """Locate the `claude` CLI binary, or exit with a clear message."""
    exe = shutil.which("claude")
    if exe:
        return exe
    # Fall back to the directory Claude Code advertises via its env var.
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH")
    if execpath:
        candidate = os.path.join(os.path.dirname(execpath), "claude")
        if os.path.exists(candidate):
            return candidate
    sys.exit(
        "The 'claude' CLI was not found on your PATH. Install Claude Code "
        "(https://claude.com/claude-code) and make sure `claude` runs, then retry."
    )


# Running total of `total_cost_usd` over all CLI calls (API-equivalent cost).
SPENT_USD = 0.0


def call_claude(cli: str, model: str, system: str, user: str, schema: dict,
                tools: list[str] | None = None, allowed: list[str] | None = None,
                timeout: int = 1800, add_dir: str | None = None) -> dict:
    """Run the `claude` CLI in print mode and return its structured JSON output.

    `tools` are the only built-in tools the model gets (none by default);
    `allowed` are the permission rules that let them run unattended.
    Reuses the CLI's existing login (no API key needed). Raises RuntimeError on
    any failure so the caller can decide how loudly to complain.
    """
    cmd = [
        cli, "--print",                  # prompt goes in on stdin: the avoid list
        "--model", model,                # can outgrow a command-line argument
        "--system-prompt", system,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--tools", ",".join(tools or []),
        # No hooks, plugins, skills, CLAUDE.md or MCP tool schemas: they'd be
        # resent on every turn of every call and are pure token overhead here.
        "--safe-mode", "--strict-mcp-config", "--no-session-persistence",
    ]
    if allowed:
        cmd += ["--allowedTools", ",".join(allowed)]
    if add_dir:
        cmd += ["--add-dir", add_dir]
    try:
        proc = subprocess.run(
            cmd,
            input=user,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {timeout}s")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {detail}")

    envelope = _parse_envelope(proc.stdout)
    global SPENT_USD
    SPENT_USD += envelope.get("total_cost_usd") or 0
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI reported an error: {envelope.get('result')}")

    # `--json-schema` populates `structured_output` directly; fall back to
    # parsing the `result` text if an older CLI omits it.
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result_text = envelope.get("result")
    if isinstance(result_text, str):
        return json.loads(result_text)
    raise RuntimeError("claude CLI returned no parseable result")


def _parse_envelope(stdout: str) -> dict:
    """Parse the `--output-format json` envelope, tolerating stray leading lines."""
    text = stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Take the last line that parses as a JSON object.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    raise RuntimeError(f"could not parse claude CLI output as JSON:\n{stdout[:500]}")


def collect_existing(paths: list[str]) -> list[dict]:
    """Every question in the banks at `paths` (non-banks are skipped)."""
    return [q for bank in score.load_banks(paths) for q in bank["exam"]["questions"] if q.get("prompt")]


# There is no fixed number of rounds: a section keeps asking for the missing
# questions until it has them all. The only stop is a section that makes no
# progress at all for this many rounds in a row (e.g. the CLI is logged out or
# keeps failing), so a broken setup can't burn tokens forever.
MAX_STALLED_ROUNDS = 5
# Rejection reasons shown to the model in the next round (most recent round only).
MAX_FEEDBACK = 30


# Doc text per page and per author call (~4 chars per token).
PAGE_CHARS = 12_000
DOC_BUDGET = 100_000


def docs_block(pages: dict[str, str]) -> str:
    return "\n\n".join(f'<doc url="{url}">\n{text}\n</doc>' for url, text in pages.items())


def pick_docs(order: list[str], docs: dict[str, list[tuple[str, str]]],
              budget: int = DOC_BUDGET) -> tuple[list[str], dict[str, str]]:
    """The objectives (in `order`) whose pages fit in `budget` chars, and those pages (url → text).

    The first objective with docs is always taken, so a round never goes empty.
    """
    chosen: list[str] = []
    pages: dict[str, str] = {}
    for obj in order:
        new = {u: t for u, t in docs.get(obj, []) if u not in pages}
        if not docs.get(obj) or (chosen and sum(map(len, pages.values())) + sum(map(len, new.values())) > budget):
            continue
        chosen.append(obj)
        pages.update(new)
    return chosen, pages


def verify(cli: str, model: str, profile: cfg.Profile, questions: list[dict],
           pages: dict[str, str]) -> list[dict]:
    """Return one {"status", "note"} per question (independent fact check of its sources)."""
    if not questions:
        return []
    payload = [{"index": i, "objective": q.get("objective"), "prompt": q["prompt"],
                "choices": q["choices"], "explanation": q.get("explanation", ""),
                "sources": q.get("sources", [])} for i, q in enumerate(questions)]
    cited = {u: pages[u] for q in questions for u in q.get("sources", []) if u in pages}
    try:
        data = call_claude(cli, model, verify_system_prompt(profile),
                           docs_block(cited) + "\n\nQuestions:\n"
                           + json.dumps(payload, ensure_ascii=False, indent=1), VERIFY_SCHEMA)
    except RuntimeError as exc:
        print(f"    ! verification failed: {exc}", file=sys.stderr)
        return [{"status": "unconfirmed", "note": "verification call failed"}] * len(questions)
    by_index = {r.get("index"): r for r in data.get("results", [])}
    return [{"status": by_index.get(i, {}).get("status", "unconfirmed"),
             "note": by_index.get(i, {}).get("note", "no verdict returned")}
            for i in range(len(questions))]


def objective_counts(known: list[dict], section: str) -> Counter:
    return Counter(q.get("objective") for q in known if q.get("section") == section)


def _feedback_block(rejected: list[tuple[str, str]]) -> str:
    if not rejected:
        return ""
    lines = "\n".join(f"- \"{prompt[:160]}{'…' if len(prompt) > 160 else ''}\" → {reason}"
                      for prompt, reason in rejected[:MAX_FEEDBACK])
    return ("\n\nREJECTED in the previous round — do not repeat these mistakes (fix the problem "
            "or pick another concept):\n" + lines)


def generate_section(cli: str, model: str, profile: cfg.Profile, section: str, count: int,
                     known: list[dict], docs: dict[str, list[tuple[str, str]]], check: bool = True,
                     verify_model: str | None = None) -> list[dict]:
    """Generate exactly `count` new, grounded questions for `section`.

    `docs` maps objective id → [(url, page text)]. Each round the model gets the
    pages of the least-covered objectives (up to DOC_BUDGET) and may only write
    about — and cite — those. It also sees every known question of this section
    and how many questions each objective already has. Its output is checked locally
    (bank_tools.check_question), against known questions with the exam's
    near-duplicate test, and — if `check` — by an independent verification
    call. Rejected questions are requested again, with the reasons for every
    rejection of the previous round, until `count` is reached. Only after
    MAX_STALLED_ROUNDS rounds in a row without a single accepted question does
    it give up. `known` is extended with the accepted questions.
    """
    sigs = [score.signature(q) for q in known]
    kept: list[dict] = []
    system = author_system_prompt(profile)
    rejected: list[tuple[str, str]] = []       # (prompt, reason) from the last round
    stalled = rounds = 0
    while len(kept) < count:
        if stalled >= MAX_STALLED_ROUNDS:
            print(f"    ! giving up on '{section}': {stalled} rounds in a row without a usable "
                  "question", file=sys.stderr)
            break
        rounds += 1
        need = count - len(kept)
        if rounds > 1:
            print(f"    round {rounds}: {need} more ({len(rejected)} rejected last round)", file=sys.stderr)
        counts = objective_counts(known, section)
        objectives = "\n".join(f"- {o['id']} ({counts[o['id']]} existing): {o['text']}"
                                for o in profile.objectives.get(section, []))
        order = sorted(profile.objective_ids(section), key=lambda o: counts[o])
        targets, pages = pick_docs(order, docs)
        if not targets:
            print(f"    ! no documentation for any objective of '{section}'", file=sys.stderr)
            break
        avoid = [q["prompt"] for q in known if q.get("section") == section]
        avoid_block = "\n".join(f"- {p}" for p in avoid)
        user = (
            f"Official documentation:\n{docs_block(pages)}\n\n"
            f"Write {need} exam questions for the section \"{section}\".\n"
            f"Objectives of this section (from the official exam guide):\n{objectives}\n"
            f"Write only for the objectives {', '.join(targets)} (their documentation is above), "
            "spreading the questions over them, the ones with the fewest existing questions first.\n\n"
            "Every question must test something not covered by the existing questions "
            "below; pick a different concept, feature or scenario rather than rewording one.\n"
            f"Existing questions:\n{avoid_block or '(none)'}"
            + _feedback_block(rejected)
        )
        rejected = []
        before = len(kept)
        try:
            data = call_claude(cli, model, system, user, question_schema(targets, list(pages)))
        except RuntimeError as exc:
            print(f"    ! generation failed for '{section}': {exc}", file=sys.stderr)
            stalled += 1
            continue
        candidates: list[dict] = []
        round_sigs: list[score.Signature] = []
        for q in data.get("questions", []):
            q["section"] = section
            errors = bank_tools.check_question(q, profile, require_sources=True)
            if errors:
                print(f"    ! dropping a '{section}' question: {'; '.join(errors)}", file=sys.stderr)
                rejected.append((q.get("prompt", ""), "; ".join(errors)))
                continue
            sig = score.signature(q)
            if any(score.is_duplicate(sig, other) for other in sigs + round_sigs):
                rejected.append((q["prompt"], "duplicate: too similar to an existing question"))
                continue
            round_sigs.append(sig)
            candidates.append(q)
        dupes = sum(1 for _, r in rejected if r.startswith("duplicate"))
        if dupes:
            print(f"    - {dupes} duplicate(s) rejected", file=sys.stderr)

        today = datetime.now(timezone.utc).date().isoformat()
        verdicts = (verify(cli, verify_model or model, profile, candidates, pages) if check
                    else [{"status": "unverified", "note": "generated with --no-verify"}] * len(candidates))
        for q, verdict in zip(candidates, verdicts):
            if verdict["status"] not in ("confirmed", "unverified"):
                print(f"    ! {verdict['status']}: {q['prompt'][:70]}… — {verdict['note']}", file=sys.stderr)
                rejected.append((q["prompt"], f"fact check {verdict['status']}: {verdict['note']}"))
                continue
            if len(kept) >= count:
                break
            q["verification"] = {**verdict, "checkedAt": today}
            sigs.append(score.signature(q))  # only accepted questions block rewordings
            known.append(q)
            kept.append(q)
        stalled = 0 if len(kept) > before else stalled + 1
    if len(kept) < count:
        print(f"    ! only {len(kept)}/{count} new questions for '{section}'", file=sys.stderr)
    return kept


MAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objectives"],
    "properties": {"objectives": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "urls"],
        "properties": {"id": {"type": "string"}, "urls": {"type": "array", "items": {"type": "string"}}},
    }}},
}
MAP_SYSTEM = ("You map exam-guide objectives to the official documentation pages that teach them. "
              "Choose only URLs from the given list, copied exactly; most specific and relevant first.")
PAGES_PER_OBJECTIVE = 3
# Re-mapping costs tokens (pages themselves are refreshed daily for free).
MAP_MAX_AGE_DAYS = 30


def _ask_map(cli: str, model: str, profile: cfg.Profile,
             candidates: dict[str, str]) -> dict[str, list[str]]:
    """Ask once which of `candidates` (url → label) teach each objective; unknown URLs are dropped."""
    objectives = "\n".join(f"- {o['id']}: {o['text']}"
                           for name, _ in profile.sections for o in profile.objectives.get(name, []))
    listing = "\n".join(f"- {url} — {label}" for url, label in candidates.items())
    user = (f"For each objective of the {profile.title} exam, choose up to {PAGES_PER_OBJECTIVE} "
            f"pages that explain it.\n\nObjectives:\n{objectives}\n\nPages:\n{listing}")
    data = call_claude(cli, model, MAP_SYSTEM, user, MAP_SCHEMA)
    return {o["id"]: [u for u in o.get("urls", []) if u in candidates][:PAGES_PER_OBJECTIVE]
            for o in data.get("objectives", [])}


def objective_docs(cli: str, model: str, profile: cfg.Profile) -> dict[str, list[str]]:
    """Objective id → doc URLs, cached in results/<slug>/docs/map.json for MAP_MAX_AGE_DAYS.

    Two tool-free calls: objectives → overview pages from llms.txt, then →
    the detail pages those overview pages link to (or the overview itself).
    """
    path = os.path.join(docs_cache.cache_dir(profile), "map.json")
    if docs_cache.fresh(path, MAP_MAX_AGE_DAYS):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("guideVersion") == profile.guide_version:
            return cached["map"]
    print("  mapping objectives to the docs (once) ...")
    hubs = _ask_map(cli, model, profile, {e["url"]: f"{e['title']}: {e['desc']}"
                                          for e in docs_cache.index(profile)})
    candidates: dict[str, str] = {}
    for url in dict.fromkeys(u for urls in hubs.values() for u in urls):
        try:
            hub = docs_cache.page(profile, url)
        except (OSError, ValueError) as exc:
            print(f"    ! could not fetch {url}: {exc}", file=sys.stderr)
            continue
        candidates.setdefault(url, "overview page")
        for link, anchor in hub["links"]:
            candidates.setdefault(link, anchor)
    mapping = _ask_map(cli, model, profile, candidates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"guideVersion": profile.guide_version, "map": mapping}, f, indent=1)
    return mapping


def load_docs(profile: cfg.Profile, mapping: dict[str, list[str]],
              sections: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Objective id → [(url, page text)] for the objectives of `sections` (pages via the cache)."""
    docs: dict[str, list[tuple[str, str]]] = {}
    for section in sections:
        for obj in profile.objective_ids(section):
            for url in mapping.get(obj, []):
                try:
                    text = docs_cache.page(profile, url)["text"][:PAGE_CHARS]
                except (OSError, ValueError) as exc:
                    print(f"    ! could not fetch {url}: {exc}", file=sys.stderr)
                    continue
                if text:
                    docs.setdefault(obj, []).append((url, text))
            if obj not in docs:
                print(f"    ! no documentation found for objective {obj}", file=sys.stderr)
    return docs


REVERIFY_FLAG = "reverify: "


def reverify(cli: str, model: str, profile: cfg.Profile) -> int:
    """Fact-check every sourced question in the exam's banks against today's docs.

    Each question's `verification` is rewritten in its bank. Questions the docs
    now contradict ('incorrect') are reported in flags.json, so exams stop
    drawing them; a later 'confirmed' lifts only such automatic reports, never
    one you made yourself. Questions already checked today are skipped, so an
    interrupted run can simply be restarted. Returns the number found incorrect.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    rdir = score.results_dir(profile.slug)
    flags = score.load_flags(rdir)
    tally: Counter = Counter()
    for bank in score.load_banks(score.bank_paths(profile), profile.guide_version):
        todo = [q for q in bank["exam"]["questions"] if q.get("sources")
                and (q.get("verification") or {}).get("checkedAt") != today]
        if not todo:
            continue
        print(f"  {os.path.basename(bank['path'])}: {len(todo)} question(s)")
        pages: dict[str, str] = {}
        for url in dict.fromkeys(u for q in todo for u in q["sources"]):
            try:
                pages[url] = docs_cache.page(profile, url)["text"][:PAGE_CHARS]
            except (OSError, ValueError) as exc:
                print(f"    ! could not fetch {url}: {exc}", file=sys.stderr)
        # Batches whose cited pages fit in DOC_BUDGET (one question always fits).
        batches: list[list[dict]] = [[]]
        for q in todo:
            cited = {u for x in batches[-1] + [q] for u in x["sources"] if u in pages}
            if batches[-1] and sum(len(pages[u]) for u in cited) > DOC_BUDGET:
                batches.append([])
            batches[-1].append(q)
        for batch in batches:
            for q, verdict in zip(batch, verify(cli, model, profile, batch, pages)):
                if verdict["note"] == "verification call failed":
                    continue  # keep the old verdict; retried on the next run
                q["verification"] = {**verdict, "checkedAt": today}
                tally[verdict["status"]] += 1
                flag = flags.get(score._norm_prompt(q["prompt"]))
                if verdict["status"] != "confirmed":  # unconfirmed: shown, not reported
                    print(f"    ! {verdict['status']}: {q['prompt'][:70]}… — {verdict['note']}")
                if verdict["status"] == "incorrect":
                    if not flag:
                        score.set_flag(rdir, q, REVERIFY_FLAG + verdict["note"])
                elif (verdict["status"] == "confirmed" and flag
                      and flag.get("reason", "").startswith(REVERIFY_FLAG)):
                    score.set_flag(rdir, q, None)
            tmp = bank["path"] + ".tmp"  # save after every batch: progress survives a crash
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(bank["exam"], f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, bank["path"])
        print(f"    spent so far: ${SPENT_USD:.2f}")
    print(f"\nRe-verified {sum(tally.values())} question(s): " +
          ", ".join(f"{n} {s}" for s, n in tally.most_common()) if tally else "\nNothing to re-verify.")
    if tally["incorrect"]:
        print(f"  {tally['incorrect']} incorrect question(s) reported; they are no longer drawn "
              "(see results/<exam>/flags.json).")
    return tally["incorrect"]


def focus_weights(profile: cfg.Profile, history: list[dict]) -> dict[str, float]:
    """Section weights for --focus weak: blueprint weight x share of questions missed.

    Uses the per-section results of the given attempts. Sections never seen keep
    their blueprint weight; if every section is perfect, the blueprint is returned.
    """
    weights: dict[str, float] = {}
    for name, weight in profile.sections:
        correct = sum(h.get("sections", {}).get(name, [0, 0])[0] for h in history)
        total = sum(h.get("sections", {}).get(name, [0, 0])[1] for h in history)
        weights[name] = weight * (1 - correct / total) if total else weight
    return weights if sum(weights.values()) > 0 else dict(profile.weights)


def default_out_path(profile: cfg.Profile) -> str:
    return next_free_path(os.path.join(profile.banks_dir, "generated-1.json"))


def next_free_path(path: str) -> str:
    """Return `path` if free, else the first non-colliding `<stem>-<n><ext>`.

    Generated banks are always written as *new* files; an existing practice
    file is never overwritten.
    """
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    # Strip a trailing "-<n>" so generated-1.json bumps to generated-2.json.
    base = root
    if "-" in os.path.basename(root):
        head, tail = root.rsplit("-", 1)
        if tail.isdigit():
            base = head
    n = 2
    while os.path.exists(f"{base}-{n}{ext}"):
        n += 1
    return f"{base}-{n}{ext}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate grounded practice questions via the Claude CLI.")
    parser.add_argument("--exam", default=None,
                        help=f"Exam profile slug under exams/ (default $EXAM or {cfg.DEFAULT_EXAM}).")
    parser.add_argument("--count", type=int, default=None,
                        help="Total questions to generate (default: the profile's exam size).")
    parser.add_argument("--out", default=None,
                        help="Output JSON path (default exams/<slug>/banks/generated-<n>.json). "
                             "Never overwrites: an existing file bumps to a fresh -<n> name.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model for the claude CLI (default '{DEFAULT_MODEL}': the alias for "
                             "the latest Opus). A full id such as claude-opus-5-5 pins a model.")
    parser.add_argument("--verify-model", default="sonnet",
                        help="Model for the fact-check and the one-off objective→docs mapping "
                             "(default 'sonnet': reading given pages doesn't need Opus).")
    parser.add_argument("--focus", choices=["blueprint", "weak"], default="blueprint",
                        help="'weak' shifts the section mix towards the sections you missed most "
                             f"in your last {FOCUS_WINDOW} attempts.")
    parser.add_argument("--reverify", action="store_true",
                        help="Generate nothing: re-check every sourced question in the banks "
                             "against today's docs and report the ones that are now wrong.")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the independent verification call (faster; questions are "
                             "marked 'unverified').")
    args = parser.parse_args()

    try:
        profile = cfg.load(args.exam)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.reverify:
        reverify(find_claude_cli(), args.verify_model, profile)
        return 0
    count = args.count or profile.total_questions
    known = collect_existing(score.bank_paths(profile))
    # Reported questions stay "known" so they aren't recreated in other words.
    print(f"{profile.title}: deduplicating against {len(known)} existing question(s).")

    weights: dict[str, float] = dict(profile.weights)
    if args.focus == "weak":
        history = score.load_history(score.results_dir(profile.slug), FOCUS_WINDOW)
        if history:
            weights = focus_weights(profile, history)
            print(f"  focus: weak sections from your last {len(history)} attempt(s)")
        else:
            print("  focus: no attempts yet — using the blueprint", file=sys.stderr)
    distribution = cfg.distribute_questions(weights, count)

    cli = find_claude_cli()
    sections = [s for s, _ in profile.sections if distribution.get(s)]
    try:
        docs = load_docs(profile, objective_docs(cli, args.verify_model, profile), sections)
    except (OSError, RuntimeError) as exc:
        sys.exit(f"Could not prepare the documentation: {exc}")
    print(f"  docs: {sum(len(v) for v in docs.values())} page(s) for {len(docs)} objective(s)"
          f" — cached in {docs_cache.cache_dir(profile)}")
    all_questions: list[dict] = []
    for section, weight in profile.sections:
        n = distribution.get(section, 0)
        if n == 0:
            continue
        print(f"  generating {n:>2}  ({weight}%)  {section} ...")
        all_questions.extend(generate_section(cli, args.model, profile, section, n, known, docs,
                                              check=not args.no_verify,
                                              verify_model=args.verify_model))
        print(f"    spent so far: ${SPENT_USD:.2f}")

    for i, q in enumerate(all_questions, 1):
        q["id"] = f"q{i}"

    if not all_questions:
        print("\nNo new questions were generated; nothing written.", file=sys.stderr)
        return 1

    exam = {
        "title": f"{profile.title} (generated)",
        "exam": profile.slug,
        "source": "json",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guideVersion": profile.guide_version,
        "timeLimitMinutes": profile.time_limit_minutes,
        "passingScore": profile.passing_score,
        "sections": profile.sections_json(),
        "questions": all_questions,
    }

    os.makedirs(profile.banks_dir, exist_ok=True)
    out = next_free_path(args.out) if args.out else default_out_path(profile)
    if args.out and out != args.out:
        print(f"  {args.out} exists — writing a new file instead.")
    with open(out, "x", encoding="utf-8") as f:  # exclusive create; never clobbers
        json.dump(exam, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"\nWrote {len(all_questions)} new unique questions to {out}")
    if len(all_questions) < count:
        print(f"  ! {count - len(all_questions)} short of {count} — see the warnings above.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
