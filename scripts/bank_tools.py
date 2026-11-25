#!/usr/bin/env python3
"""Question-bank checks and coverage for one exam profile.

  plan      [--exam slug]  Blueprint plus coverage: per section and per guide
                           objective, how many unique (verified) questions the
                           banks hold, and how many were reported as wrong.
  validate  [--exam slug]  Check every bank of the exam: structure, objective ids,
                           source URLs (official domains only) and outdated
                           product names. Exits non-zero on any error.

`check_question` is also used by generate_questions.py to reject bad output.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import exam_config as cfg
import score


def is_official(url: str, profile: cfg.Profile) -> bool:
    """True for an https URL on one of the profile's `sourceDomains` (or a subdomain)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and any(host == d or host.endswith("." + d)
                                            for d in profile.source_domains)


def outdated_terms(text: str, profile: cfg.Profile) -> list[str]:
    """Outdated product names in `text`, as "old → new". "(formerly X)" mentions are fine."""
    text = re.sub(r"\(formerly [^)]*\)", "", text, flags=re.IGNORECASE)
    hits = []
    for term in profile.terminology:
        # Acronyms (all caps) match case-sensitively, names case-insensitively.
        flags = 0 if term["old"].isupper() else re.IGNORECASE
        if re.search(rf"(?<![\w-]){re.escape(term['old'])}s?(?![\w-])", text, flags):
            hits.append(f"{term['old']} → {term['new']}")
    return hits


def check_question(q: dict[str, Any], profile: cfg.Profile, require_sources: bool = False) -> list[str]:
    """Every problem with question `q` under `profile` (empty list = valid)."""
    errors: list[str] = []
    qtype = q.get("type")
    choices = q.get("choices") or []
    n_correct = sum(1 for c in choices if c.get("correct"))
    if not q.get("prompt"):
        errors.append("missing prompt")
    if qtype not in ("single", "multiple", "trueFalse"):
        errors.append(f"unsupported type {qtype!r}")
    if len(choices) < 2:
        errors.append("fewer than 2 choices")
    if len({c.get("id") for c in choices}) != len(choices):
        errors.append("duplicate choice ids")
    if qtype == "single" and n_correct != 1:
        errors.append(f"single question has {n_correct} correct choices (expected 1)")
    if qtype == "multiple" and n_correct < 2:
        errors.append(f"multiple question has {n_correct} correct choices (expected >= 2)")
    if qtype == "trueFalse" and ({c.get("id") for c in choices} != {"true", "false"} or n_correct != 1):
        errors.append("trueFalse question must have exactly 'true'/'false' choices, one correct")

    section = q.get("section")
    if section not in profile.weights:
        errors.append(f"unknown section {section!r}")
    objective = q.get("objective")
    if objective and objective not in profile.objective_ids(section if section in profile.weights else None):
        errors.append(f"objective {objective!r} is not an objective of section {section!r}")
    if require_sources and not objective:
        errors.append("missing objective")

    sources = q.get("sources") or []
    if require_sources and not sources:
        errors.append("no source URL")
    for url in sources:
        if not is_official(url, profile):
            errors.append(f"source not on an official domain: {url}")

    text = " ".join([q.get("prompt", ""), q.get("explanation", "")] + [c.get("text", "") for c in choices])
    errors += [f"outdated name: {hit}" for hit in outdated_terms(text, profile)]
    return errors


def coverage(profile: cfg.Profile, banks: list[dict[str, Any]], flags: dict[str, Any]) -> dict[str, Any]:
    """Unique questions per section and per objective (verified ones counted separately)."""
    per_obj: Counter = Counter()
    per_obj_verified: Counter = Counter()
    per_section: Counter = Counter()
    untagged = flagged = 0
    for group in score.unique_question_groups(banks):
        if score.group_keys(group) & set(flags):
            flagged += 1
            continue
        _, q = group[0]
        per_section[q.get("section")] += 1
        obj = next((m.get("objective") for _, m in group if m.get("objective")), None)
        verified = any((m.get("verification") or {}).get("status") == "confirmed" for _, m in group)
        if obj:
            per_obj[obj] += 1
            per_obj_verified[obj] += int(verified)
        else:
            untagged += 1
    return {"objective": per_obj, "verified": per_obj_verified, "section": per_section,
            "untagged": untagged, "flagged": flagged}


def cmd_plan(profile: cfg.Profile) -> int:
    rdir = score.results_dir(profile.slug)
    banks = score.load_banks(score.bank_paths(profile), profile.guide_version)
    cov = coverage(profile, banks, score.load_flags(rdir))
    dist = profile.distribute()
    print(f"{profile.title} — {profile.total_questions} questions"
          f"{f' (+ up to {profile.unscored_max} unscored)' if profile.unscored_max else ''}, "
          f"{profile.time_limit_minutes} min, pass at {profile.passing_score}%")
    print(f"  guide {profile.guide_version} · {len(banks)} bank(s) in {os.path.relpath(profile.banks_dir)}\n")
    print("  weight  exam  pool  section / objective  (pool = unique questions, ✓ = verified)")
    for name, weight in profile.sections:
        print(f"  {weight:>5}%  {dist[name]:>4}  {cov['section'][name]:>4}  {name}")
        for o in profile.objectives.get(name, []):
            n, v = cov["objective"][o["id"]], cov["verified"][o["id"]]
            warn = "  ← no questions" if n == 0 else ""
            print(f"                {n:>3} ✓{v:<3} {o['id']} {o['text'][:70]}{'…' if len(o['text']) > 70 else ''}{warn}")
    if cov["untagged"]:
        print(f"\n  {cov['untagged']} question(s) have no objective tag (older banks).")
    if cov["flagged"]:
        print(f"  {cov['flagged']} question(s) reported as wrong are excluded "
              f"(see {os.path.relpath(score.flags_path(rdir))}).")
    return 0


def cmd_validate(profile: cfg.Profile) -> int:
    paths = score.bank_paths(profile)
    if not paths:
        print(f"  no banks in {profile.banks_dir}")
        return 0
    failed = 0
    for path in paths:
        banks = score.load_banks([path])
        if not banks:
            print(f"  {os.path.relpath(path):<60} INVALID (not a bank or bad JSON)")
            failed += 1
            continue
        exam = banks[0]["exam"]
        stale = exam.get("guideVersion") and exam["guideVersion"] != profile.guide_version
        problems = [(q.get("id", f"#{i}"), err) for i, q in enumerate(exam["questions"], 1)
                    for err in check_question(q, profile)]
        status = "OK" if not problems else f"{len(problems)} problem(s)"
        print(f"  {os.path.relpath(path):<60} {status} - {len(exam['questions'])} questions"
              f"{'  [stale: guide ' + exam['guideVersion'] + ']' if stale else ''}")
        for qid, err in problems:
            print(f"      {qid}: {err}")
        failed += bool(problems)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=["plan", "validate"])
    parser.add_argument("--exam", default=None, help="Exam profile slug (default $EXAM).")
    args = parser.parse_args()
    try:
        profile = cfg.load(args.exam)
    except ValueError as exc:
        sys.exit(str(exc))
    return cmd_plan(profile) if args.cmd == "plan" else cmd_validate(profile)


if __name__ == "__main__":
    sys.exit(main())
