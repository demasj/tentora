#!/usr/bin/env python3
"""Score a practice exam and review answers.

Pure, dependency-free grading (stdlib only) that mirrors what the eventual
`engine/` grading module will do: each question type has exactly one grading
function, and the pass/fail decision weights every section by its official
exam weight.

Subcommands
-----------
  take   [bank.json ...] [--exam slug] Sit the exam interactively in the terminal.
                                     Without bank files, every bank of the exam
                                     profile (exams/<slug>/banks/*.json) is used.
                                     Type '?' at a question to reveal the answer
                                     and explanation before you commit (study aid).
                                     Question order and answer choices are both
                                     shuffled each run (choices relabelled a,b,c,
                                     ... so the correct answer isn't always 'a');
                                     --no-shuffle keeps the authored order.
                                     Afterwards the review of wrong answers is saved
                                     to results/<slug>/<timestamp>.md, and the wrong questions
                                     to results/<slug>/wrong-latest.json (`make retry`).
                                     Pass several banks to draw one combined,
                                     section-weighted 45-question exam from all of
                                     them (see "Combining banks" below).
  grade  <exam.json> <answers.json>  Grade a saved answers file.
                                     Add --review to see a per-question breakdown.

Combining banks (take)
----------------------
Given more than one bank, `take` builds a single exam of --count questions
(default 45) whose per-section counts follow the official blueprint weights, and
draws each section's questions at random from the unique questions. Reworded
duplicates (common across generated banks) count as one question, so no
question appears twice. Successive exams rotate through the pool: questions
served before are skipped until every question has been seen, then a new round
starts (state in results/<slug>/rotation.json). Questions reported as wrong
(results/<slug>/flags.json, from the browser review) are never drawn, and banks
written for another exam-guide version than the profile's are skipped. Near the end of a round a section that
has run out gives its slots to unseen questions of other sections. Use --seed for a
reproducible mix.

Answers file format (grade): { "q1": "b", "q6": ["a", "b"], "q12": "true" }
  - single / trueFalse : the chosen choice id (string)
  - multiple           : a list of chosen choice ids

Weighted score
--------------
Each section contributes `weight% * (correct-in-section / answered-in-section)`.
Only sections that actually have questions are counted, and their weights are
renormalized to 100% — so partial banks still score sensibly. Pass if the
weighted score >= the exam's passingScore. Questions marked ``"unscored": true``
(the real exam's up-to-N pretest items) are graded for the review but never
count towards the score.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import exam_config as cfg

RESULTS_ROOT = os.path.join(cfg.ROOT, "results")
DEFAULT_PASSING_SCORE = 70


def results_dir(slug: str, root: str | None = None) -> str:
    """Where one exam's reports, retry bank, rotation, flags and history live."""
    return os.path.join(root or RESULTS_ROOT, slug)


def retry_path(rdir: str) -> str:
    return os.path.join(rdir, "wrong-latest.json")


# --------------------------------------------------------------------------- #
# Grading — one pure function per question type.
# --------------------------------------------------------------------------- #

def _correct_choice_ids(question: dict[str, Any]) -> list[str]:
    return [c["id"] for c in question.get("choices", []) if c.get("correct")]


def _normalize_single(answer: Any) -> str | None:
    """Accept a raw string, a 1-element list, or a bool (for trueFalse)."""
    if isinstance(answer, bool):
        return "true" if answer else "false"
    if isinstance(answer, list):
        return str(answer[0]) if len(answer) == 1 else None
    if answer is None:
        return None
    return str(answer).strip().lower() if isinstance(answer, str) else str(answer)


def grade_question(question: dict[str, Any], answer: Any) -> bool:
    """Return True if `answer` correctly answers `question`."""
    qtype = question.get("type", "single")
    correct = set(_correct_choice_ids(question))

    if qtype == "multiple":
        if not isinstance(answer, list):
            return False
        given = {str(a).strip().lower() for a in answer}
        return given == {c.lower() for c in correct}

    # single and trueFalse both resolve to picking the one correct choice id.
    given = _normalize_single(answer)
    if given is None:
        return False
    return given in {c.lower() for c in correct}


def format_choices(question: dict[str, Any], ids: list[str]) -> str:
    """Render choice ids as 'a) text; c) text' (unknown ids shown as-is)."""
    by_id = {c["id"].lower(): c["text"] for c in question.get("choices", [])}
    return "; ".join(
        f"{cid}) {by_id[cid.lower()]}" if cid.lower() in by_id else str(cid) for cid in ids
    )


def format_correct_answer(question: dict[str, Any]) -> str:
    return format_choices(question, _correct_choice_ids(question))


def _given_ids(given: Any) -> list[str]:
    if given is None:
        return []
    items = given if isinstance(given, list) else [given]
    return [str(g).strip().lower() for g in items if str(g).strip()]


# --------------------------------------------------------------------------- #
# Weighted scoring.
# --------------------------------------------------------------------------- #

def score_attempt(exam: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    section_weights = {s["name"]: s["weight"] for s in exam.get("sections", [])}

    per_section_total: dict[str, int] = defaultdict(int)
    per_section_correct: dict[str, int] = defaultdict(int)
    results = []
    raw_correct = 0

    for q in exam["questions"]:
        section = q.get("section", "Uncategorized")
        given = answers.get(q["id"])
        is_correct = given is not None and grade_question(q, given)
        if q.get("unscored"):
            results.append({"question": q, "given": given, "correct": is_correct, "unscored": True})
            continue
        per_section_total[section] += 1
        if is_correct:
            per_section_correct[section] += 1
            raw_correct += 1
        results.append({"question": q, "given": given, "correct": is_correct, "unscored": False})

    # Weighted score: renormalize over sections that actually have questions.
    present_weight = sum(section_weights.get(s, 0) for s in per_section_total)
    weighted = 0.0
    section_rows = []
    for section, total in per_section_total.items():
        correct = per_section_correct[section]
        pct = 100.0 * correct / total if total else 0.0
        weight = section_weights.get(section, 0)
        if present_weight > 0 and weight > 0:
            weighted += (weight / present_weight) * pct
        section_rows.append({
            "section": section, "weight": weight,
            "correct": correct, "total": total, "pct": pct,
        })

    # If sections carry no weights at all, fall back to plain percent-correct.
    if present_weight == 0:
        total_q = sum(per_section_total.values())
        weighted = 100.0 * raw_correct / total_q if total_q else 0.0

    passing = exam.get("passingScore", DEFAULT_PASSING_SCORE)
    return {
        "results": results,
        "sections": sorted(section_rows, key=lambda r: -r["weight"]),
        "raw_correct": raw_correct,
        "total": sum(per_section_total.values()),
        "weighted_score": round(weighted, 1),
        "passing_score": passing,
        "passed": weighted >= passing,
    }


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #

class _Style:
    """ANSI colours, enabled only on a real terminal (and not when NO_COLOR is set)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def red(self, t: str) -> str: return self._wrap("31", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)


def _terminal_width() -> int:
    return max(40, min(shutil.get_terminal_size((100, 24)).columns, 100))


def _wrap(text: str, width: int, indent: str, hang: str | None = None) -> list[str]:
    """Wrap each paragraph of `text`; first line gets `indent`, the rest `hang`."""
    hang = indent if hang is None else hang
    lines: list[str] = []
    for para in (text or "").splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=width, initial_indent=indent,
                                   subsequent_indent=hang) or [indent])
        indent = hang
    return lines


def wrong_answers(scored: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """(question number in this attempt, result) for every incorrect answer."""
    return [(n, r) for n, r in enumerate(scored["results"], 1) if not r["correct"]]


def _choice_lines(question: dict[str, Any], ids: list[str]) -> list[str]:
    if not ids:
        return ["(no answer)"]
    return [part for part in format_choices(question, ids).split("; ")]


def print_report(exam: dict[str, Any], scored: dict[str, Any], review: bool) -> None:
    st = _Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    width = _terminal_width()

    print()
    print(st.bold(f"  {exam.get('title', 'Exam')}"))
    print("  " + "-" * 60)
    for row in scored["sections"]:
        bar_len = int(row["pct"] / 5)
        bar = "#" * bar_len + "." * (20 - bar_len)
        print(f"  {row['weight']:>3}%  [{bar}] {row['pct']:5.1f}%  "
              f"{row['correct']}/{row['total']}  {row['section']}")
    print("  " + "-" * 60)
    wrong = wrong_answers(scored)
    verdict = st.green("PASS ✓") if scored["passed"] else st.red("FAIL ✗")
    score = st.bold(f"{scored['weighted_score']}%")
    note = st.dim(f"(pass mark {scored['passing_score']}%, weighted by section)")
    print(f"  Score {score}  {verdict}   {scored['raw_correct']}/{scored['total']} correct   "
          f"{len(wrong)} wrong   {note}")
    print()

    if not review:
        return
    if not wrong:
        print(st.green("  All correct — nothing to review."))
        print()
        return

    label_w = 9  # len("✗ you:   ")
    for k, (n, item) in enumerate(wrong, 1):
        q = item["question"]
        tag = " · unscored" if item.get("unscored") else ""
        head = f"── Wrong {k}/{len(wrong)} ── Q{n} · {q.get('section', '')}{tag} "
        print(st.bold("  " + head + "─" * max(0, width - len(head) - 2)))
        for line in _wrap(q["prompt"], width, "  "):
            print(line)
        print()
        for mark, label, ids, colour in (
            ("✗", "you:", _given_ids(item["given"]), st.red),
            ("✓", "right:", _correct_choice_ids(q), st.green),
        ):
            first = f"    {mark} {label:<6} "
            pad = " " * (4 + label_w)
            for i, choice in enumerate(_choice_lines(q, ids)):
                for line in _wrap(choice, width, first if i == 0 else pad, pad):
                    print(colour(line))
        if q.get("explanation"):
            print()
            for line in _wrap("Why: " + q["explanation"], width, "    "):
                print(st.dim(line))
        for url in q.get("sources") or []:
            print(st.dim(f"    Source: {url}"))
        print()


def render_markdown(exam: dict[str, Any], scored: dict[str, Any]) -> str:
    """The same report as `print_report(review=True)`, as Markdown for saving."""
    wrong = wrong_answers(scored)
    verdict = "PASS ✓" if scored["passed"] else "FAIL ✗"
    out = [
        f"# {exam.get('title', 'Exam')}",
        "",
        f"_{datetime.now():%Y-%m-%d %H:%M}_",
        "",
        f"**Score {scored['weighted_score']}% — {verdict}** · "
        f"{scored['raw_correct']}/{scored['total']} correct · {len(wrong)} wrong · "
        f"pass mark {scored['passing_score']}%",
        "",
        "| Weight | Score | Correct | Section |",
        "|-------:|------:|--------:|---------|",
    ]
    for row in scored["sections"]:
        out.append(f"| {row['weight']}% | {row['pct']:.1f}% | "
                   f"{row['correct']}/{row['total']} | {row['section']} |")
    out += ["", "## Wrong answers" if wrong else "All correct.", ""]
    for k, (n, item) in enumerate(wrong, 1):
        q = item["question"]
        out += [f"### Wrong {k}/{len(wrong)} · Q{n} · {q.get('section', '')}", "", q["prompt"], ""]
        out += [f"- ✗ **you:** {c}" for c in _choice_lines(q, _given_ids(item["given"]))]
        out += [f"- ✓ **right:** {c}" for c in _choice_lines(q, _correct_choice_ids(q))]
        if q.get("explanation"):
            out += ["", f"> **Why:** {q['explanation']}"]
        out += [f"> Source: <{url}>" for url in q.get("sources") or []]
        out.append("")
    return "\n".join(out)


def save_results(exam: dict[str, Any], scored: dict[str, Any], rdir: str) -> None:
    """Write the Markdown report, the history line and a retry bank of the wrong questions."""
    os.makedirs(rdir, exist_ok=True)
    append_history(scored, rdir)
    stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
    report = os.path.join(rdir, f"{stamp}.md")
    n = 2
    while os.path.exists(report):          # two attempts in the same second
        report = os.path.join(rdir, f"{stamp}-{n}.md")
        n += 1
    with open(report, "w", encoding="utf-8") as f:
        f.write(render_markdown(exam, scored))
    print(f"  Report saved: {report}")

    wrong = [{k: v for k, v in item["question"].items() if k != "unscored"}
             for _, item in wrong_answers(scored)]
    retry_bank = retry_path(rdir)
    if not wrong:
        if os.path.exists(retry_bank):
            os.remove(retry_bank)
        print("  No wrong answers — nothing to retry.")
        return
    retry = {
        "title": f"Retry: {len(wrong)} wrong questions",
        "source": "json",
        "passingScore": exam.get("passingScore", DEFAULT_PASSING_SCORE),
        "sections": exam.get("sections", []),
        "questions": wrong,
    }
    with open(retry_bank, "w", encoding="utf-8") as f:
        json.dump(retry, f, indent=2, ensure_ascii=False)
    print(f"  {len(wrong)} wrong questions saved — practise them again with: make retry")
    print()


def history_path(rdir: str) -> str:
    return os.path.join(rdir, "history.jsonl")


def append_history(scored: dict[str, Any], rdir: str) -> None:
    """One JSON line per attempt: per-section and per-objective results (for --focus weak)."""
    objectives: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in scored["results"]:
        obj = r["question"].get("objective")
        if obj and not r.get("unscored"):
            objectives[obj][1] += 1
            objectives[obj][0] += int(r["correct"])
    line = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "score": scored["weighted_score"], "passed": scored["passed"],
        "sections": {row["section"]: [row["correct"], row["total"]] for row in scored["sections"]},
        "objectives": dict(objectives),
    }
    with open(history_path(rdir), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_history(rdir: str, last: int | None = None) -> list[dict[str, Any]]:
    try:
        with open(history_path(rdir), encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return lines[-last:] if last else lines


# --------------------------------------------------------------------------- #
# Reported questions ("this question is wrong" from the browser review).
# --------------------------------------------------------------------------- #

def flags_path(rdir: str) -> str:
    return os.path.join(rdir, "flags.json")


def load_flags(rdir: str) -> dict[str, dict[str, Any]]:
    """Normalized prompt -> {"prompt", "reason", "at"} for every reported question."""
    try:
        with open(flags_path(rdir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def set_flag(rdir: str, question: dict[str, Any], reason: str | None) -> bool:
    """Report (reason given) or un-report (reason None) a question. Returns the new state."""
    flags = load_flags(rdir)
    key = _norm_prompt(question.get("prompt", ""))
    if reason is None:
        flags.pop(key, None)
    else:
        flags[key] = {"prompt": question.get("prompt", ""), "reason": reason[:1000],
                      "objective": question.get("objective", ""),
                      "at": datetime.now().isoformat(timespec="seconds")}
    os.makedirs(rdir, exist_ok=True)
    tmp = flags_path(rdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(flags, f, ensure_ascii=False, indent=2)
    os.replace(tmp, flags_path(rdir))
    return reason is not None


# --------------------------------------------------------------------------- #
# Interactive mode.
# --------------------------------------------------------------------------- #

_CHOICE_LABELS = "abcdefghijklmnopqrstuvwxyz"


def shuffle_choices(question: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Return a copy of `question` with its choices reordered and relabelled.

    Randomizes the order of the answer choices and reassigns sequential ids
    (a, b, c, ...) by their new position, so the correct answer lands under a
    different letter each run instead of always being 'a'. The `correct` flags
    ride along with each choice, so grading stays consistent with what's shown.

    trueFalse questions (and anything without choices) are returned unchanged —
    their ids are semantic ('true'/'false'), not positional.
    """
    choices = question.get("choices")
    if not choices or question.get("type") == "trueFalse":
        return question

    reordered = list(choices)
    rng.shuffle(reordered)
    new_choices = []
    for i, c in enumerate(reordered):
        relabelled = dict(c)
        relabelled["id"] = _CHOICE_LABELS[i] if i < len(_CHOICE_LABELS) else str(i + 1)
        new_choices.append(relabelled)

    shuffled = dict(question)
    shuffled["choices"] = new_choices
    return shuffled


def take_exam(exam: dict[str, Any], rdir: str, rng: random.Random | None = None,
              shuffle: bool = True) -> None:
    rng = rng or random.Random()
    questions = list(exam["questions"])
    if shuffle:
        questions = [shuffle_choices(q, rng) for q in questions]
        rng.shuffle(questions)                # randomize question order too
    exam = {**exam, "questions": questions}   # grade/report the shuffled view

    print(f"\n  {exam.get('title', 'Exam')}")
    print(f"  {len(exam['questions'])} questions | pass mark {exam.get('passingScore', DEFAULT_PASSING_SCORE)}%")
    print("  Enter choice id(s). For multi-select, separate with commas (e.g. a,c).")
    print("  Type '?' to reveal the answer + explanation before you commit.\n")

    answers: dict[str, Any] = {}
    for i, q in enumerate(exam["questions"], 1):
        while True:
            print(f"  Q{i}/{len(exam['questions'])} [{q.get('section', '')}]")
            print(f"  {q['prompt']}")
            for c in q.get("choices", []):
                print(f"      {c['id']}) {c['text']}")
            multi = q.get("type") == "multiple"
            prompt = "  Your answer (comma-separated): " if multi else "  Your answer: "
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Aborted.")
                return

            if raw == "?":
                print(f"\n  >> Correct: {format_correct_answer(q)}")
                if q.get("explanation"):
                    print(f"  >> Why: {q['explanation']}\n")
                continue  # re-ask so they still record an answer

            if multi:
                answers[q["id"]] = [a.strip().lower() for a in raw.split(",") if a.strip()]
            else:
                answers[q["id"]] = raw.lower()
            print()
            break

    scored = score_attempt(exam, answers)
    print_report(exam, scored, review=True)
    save_results(exam, scored, rdir)


# --------------------------------------------------------------------------- #
# Combining multiple banks into one section-weighted exam.
# --------------------------------------------------------------------------- #

def _norm_prompt(prompt: str) -> str:
    """Whitespace/case-normalized prompt, used to drop exact-duplicate questions."""
    return " ".join((prompt or "").split()).lower()


def load_banks(paths: list[str], guide_version: str = "") -> list[dict[str, Any]]:
    """Load each path, keeping only real exam banks (a non-empty `questions` list).

    Files that aren't exam banks (e.g. an answers file) are skipped with a note,
    so a plain `banks/*.json` glob can be passed in wholesale. With
    `guide_version`, banks stamped with a *different* `guideVersion` are stale
    (written for an older exam guide) and skipped; unstamped banks are kept.
    """
    banks: list[dict[str, Any]] = []
    for path in paths:
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! skipping {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data.get("questions"), list) or not data["questions"]:
            print(f"  ! skipping {path}: not an exam bank", file=sys.stderr)
            continue
        stamped = data.get("guideVersion")
        if guide_version and stamped and stamped != guide_version:
            print(f"  ! skipping {path}: written for exam guide {stamped}, "
                  f"current is {guide_version}", file=sys.stderr)
            continue
        banks.append({"path": path, "exam": data})
    return banks


# Generated banks often contain the same question in different words. Two
# questions count as duplicates when their content words (prompt + correct
# answer, minus stopwords) overlap by at least this Jaccard similarity; 0.45 was
# tuned on the Databricks banks (catches rewordings, keeps distinct topics apart).
DUPLICATE_SIMILARITY = 0.45
_STOPWORDS = frozenset(
    "a an the of to in on for and or is are be which what that this with by from "
    "as it its at when you your does do data engineer team".split())


def _fingerprint(question: dict[str, Any]) -> frozenset[str]:
    text = question.get("prompt", "") + " " + " ".join(
        c.get("text", "") for c in question.get("choices", []) if c.get("correct"))
    return frozenset(t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOPWORDS)


Signature = tuple[str, frozenset[str]]


def signature(question: dict[str, Any]) -> Signature:
    """What duplicate detection compares: the normalized prompt and the content words."""
    return _norm_prompt(question.get("prompt", "")), _fingerprint(question)


def is_duplicate(a: Signature, b: Signature) -> bool:
    """True if two questions (by `signature`) are the same question, even reworded."""
    union = len(a[1] | b[1])
    return a[0] == b[0] or bool(union and len(a[1] & b[1]) / union >= DUPLICATE_SIMILARITY)


def group_duplicates(questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster `questions` into groups of (near-)duplicates; each group is one unique question."""
    sigs = [signature(q) for q in questions]
    parent = list(range(len(questions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            if is_duplicate(sigs[i], sigs[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, q in enumerate(questions):
        groups[find(i)].append(q)
    return list(groups.values())


def unique_question_groups(banks: list[dict[str, Any]]) -> list[list[tuple[int, dict[str, Any]]]]:
    """All questions of `banks` (as (bank index, question)), grouped by near-duplicate."""
    tagged = [(i, q) for i, bank in enumerate(banks) for q in bank["exam"]["questions"]]
    index = {id(q): i for i, q in tagged}
    return [[(index[id(q)], q) for q in group]
            for group in group_duplicates([q for _, q in tagged])]


def group_keys(group: list[tuple[int, dict[str, Any]]]) -> set[str]:
    return {_norm_prompt(q.get("prompt", "")) for _, q in group}


def bank_paths(profile: cfg.Profile) -> list[str]:
    return sorted(glob.glob(os.path.join(profile.banks_dir, "*.json")))


def rotation_path(rdir: str) -> str:
    return os.path.join(rdir, "rotation.json")


def load_rotation(rdir: str) -> set[str]:
    """Prompts (normalized) already served in the current rotation cycle."""
    try:
        with open(rotation_path(rdir), encoding="utf-8") as f:
            return set(json.load(f).get("used", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


def save_rotation(used: set[str], rdir: str) -> None:
    os.makedirs(rdir, exist_ok=True)
    tmp = rotation_path(rdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"used": sorted(used)}, f, ensure_ascii=False, indent=0)
    os.replace(tmp, rotation_path(rdir))


def build_combined_exam(banks: list[dict[str, Any]], profile: cfg.Profile, total: int,
                        rng: random.Random, used: set[str] | None = None,
                        unscored: int = 0, exclude: set[str] | None = None,
                        ) -> tuple[dict[str, Any], dict[str, int], Counter]:
    """Draw `total` unique questions across `banks`, section-weighted.

    Per-section counts follow the profile's blueprint (`profile.distribute`).
    `unscored` extra questions (any section, unseen first) are added on top and
    marked ``"unscored": True``, like the real exam's pretest items. Groups whose
    prompt is in `exclude` (reported questions) are never drawn.
    Near-duplicate questions (see `group_duplicates`) count as one question, so
    an exam never contains the same question twice, even reworded.

    Rotation: `used` holds the normalized prompts already served in the current
    cycle, and only unseen questions are drawn. When a section has run out, its
    slots go to unseen questions of other sections; only once the whole pool has
    been served does a new cycle start. `used` is updated in place with this
    exam's questions (and their duplicates).

    Returns (exam, shortfalls, per_file_counts) where `shortfalls` maps a section
    to how many questions it fell short of its target (pool exhausted).
    """
    targets = profile.distribute(total)
    exclude = exclude or set()
    groups = [g for g in unique_question_groups(banks) if not group_keys(g) & exclude]
    rotation = used if used is not None else set()
    by_section = {section: [g for g, group in enumerate(groups)
                            if any(q.get("section") == section for _, q in group)]
                  for section, _weight in profile.sections}
    need = {section: targets.get(section, 0) for section, _weight in profile.sections}
    taken: set[int] = set()                         # group indexes in this exam
    picked: list[tuple[int, dict[str, Any]]] = []   # (bank index, question)

    def fresh(section: str) -> list[int]:
        out = [g for g in by_section[section]
               if g not in taken and not group_keys(groups[g]) & rotation]
        rng.shuffle(out)
        return out

    def take(g: int, section: str) -> None:
        taken.add(g)
        rotation.update(group_keys(groups[g]))
        need[section] -= 1
        picked.append(rng.choice([m for m in groups[g] if m[1].get("section") == section]))

    def draw() -> None:
        # Blueprint first: each section's target from its unseen questions...
        for section in need:
            room = min(need[section], total - len(picked))
            for g in fresh(section)[:max(0, room)]:
                take(g, section)
        # ...then slots a section couldn't fill go to unseen questions elsewhere.
        spare = [(g, section) for section in need for g in fresh(section)]
        rng.shuffle(spare)
        for g, section in spare:
            if len(picked) >= total:
                break
            if g not in taken:
                take(g, section)

    draw()
    if len(picked) < total:
        # Every question has been served: start a new cycle and top up with
        # questions that aren't already in this exam.
        rotation.clear()
        draw()
    shortfalls = {section: n for section, n in need.items() if n > 0 and len(picked) < total}

    # Unscored items: extra questions on top, unseen ones first.
    extra: list[tuple[int, dict[str, Any]]] = []
    if unscored > 0:
        rest = [g for g in range(len(groups)) if g not in taken]
        rng.shuffle(rest)
        rest.sort(key=lambda g: bool(group_keys(groups[g]) & rotation))  # stable: unseen first
        for g in rest[:unscored]:
            rotation.update(group_keys(groups[g]))
            bank_idx, q = rng.choice(groups[g])
            extra.append((bank_idx, {**q, "unscored": True}))
    picked += extra

    rng.shuffle(picked)             # interleave sections in the final order
    per_file = Counter(bank_idx for bank_idx, _ in picked)

    questions = []
    for i, (_bank_idx, q) in enumerate(picked, 1):
        merged = dict(q)
        merged["id"] = f"q{i}"      # unique ids; source ids collide across banks
        questions.append(merged)

    exam = {
        "title": profile.title,
        "exam": profile.slug,
        "source": "json",
        "timeLimitMinutes": profile.time_limit_minutes,
        "passingScore": profile.passing_score,
        "sections": profile.sections_json(),
        "questions": questions,
    }
    return exam, shortfalls, per_file


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #

def load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Score and review a practice exam.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_take = sub.add_parser("take", help="Sit the exam interactively.")
    p_take.add_argument("banks", nargs="*",
                        help="Bank JSON files (default: all banks of --exam). Several banks "
                             "are combined into one section-weighted exam.")
    p_take.add_argument("--exam", default=None,
                        help=f"Exam profile slug under exams/ (default $EXAM or {cfg.DEFAULT_EXAM}).")
    p_take.add_argument("--count", type=int, default=None,
                        help="Questions to draw when combining banks (default: the profile's).")
    p_take.add_argument("--unscored", type=int, default=0,
                        help="Extra unscored questions to mix in, like the real exam (default 0).")
    p_take.add_argument("--seed", type=int, default=None,
                        help="Seed the random selection AND choice shuffling for a reproducible run.")
    p_take.add_argument("--no-shuffle", action="store_true",
                        help="Keep the authored choice order (don't shuffle/relabel a,b,c,...).")

    p_grade = sub.add_parser("grade", help="Grade a saved answers file.")
    p_grade.add_argument("exam", help="Path to the bank JSON file.")
    p_grade.add_argument("answers", help="Path to the answers JSON file.")
    p_grade.add_argument("--review", action="store_true",
                         help="Show per-question review with explanations.")

    args = parser.parse_args()

    if args.cmd == "take":
        try:
            profile = cfg.load(args.exam)
        except ValueError as exc:
            sys.exit(str(exc))
        rdir = results_dir(profile.slug)
        banks = load_banks(args.banks or bank_paths(profile), profile.guide_version)
        if not banks:
            sys.exit(f"No valid exam banks to take (looked in {profile.banks_dir}).")
        rng = random.Random(args.seed)
        if len(banks) == 1 and args.banks:
            exam = banks[0]["exam"]
        else:
            used = load_rotation(rdir)
            exam, shortfalls, per_file = build_combined_exam(
                banks, profile, args.count or profile.total_questions, rng, used,
                unscored=args.unscored, exclude=set(load_flags(rdir)))
            save_rotation(used, rdir)
            print(f"\n  Combining {len(banks)} banks into {len(exam['questions'])} "
                  f"section-weighted questions:")
            for i, bank in enumerate(banks):
                print(f"    {per_file.get(i, 0):>3} from {bank['path']}")
            for section, missing in shortfalls.items():
                print(f"    ! {section}: {missing} short of target (pool exhausted)",
                      file=sys.stderr)
        take_exam(exam, rdir, rng, shuffle=not args.no_shuffle)
    elif args.cmd == "grade":
        exam = load_json(args.exam)
        answers = load_json(args.answers)
        scored = score_attempt(exam, answers)
        print_report(exam, scored, review=args.review)
        return 0 if scored["passed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
