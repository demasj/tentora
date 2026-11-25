#!/usr/bin/env python3
"""Create an exam profile from an official exam guide PDF.

    python scripts/make_profile.py --guide ~/Downloads/guide.pdf --exam data-engineer-professional

Copies the PDF to exams/<slug>/guide.pdf, has the `claude` CLI read it (Read
tool only, confined to that folder) and extract the exam facts, sections,
weights and objectives verbatim, plus every "(formerly X)" rename the guide
mentions. The result is written to exams/<slug>/profile.json — review it before
committing: it drives generation, scoring and the browser exam.

Never overwrites an existing profile unless --force is given.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import Any

import exam_config as cfg
from generate_questions import DEFAULT_MODEL, call_claude, find_claude_cli

PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "guideVersion", "totalQuestions", "unscoredMax", "timeLimitMinutes",
                 "passingScore", "sections", "terminology"],
    "properties": {
        "title": {"type": "string"},
        "guideVersion": {"type": "string", "description": "Guide update date, YYYY-MM-DD"},
        "totalQuestions": {"type": "integer", "description": "Number of SCORED questions"},
        "unscoredMax": {"type": "integer", "description": "Max unscored items, 0 if none"},
        "timeLimitMinutes": {"type": "integer"},
        "passingScore": {"type": ["number", "null"], "description": "Only if the guide states one"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "weight", "objectives"],
                "properties": {
                    "name": {"type": "string"},
                    "weight": {"type": "integer"},
                    "objectives": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "terminology": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["old", "new"],
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
            },
        },
    },
}

SYSTEM = (
    "You extract the structure of an official certification exam guide into JSON. Read the PDF "
    "with the Read tool. Copy section names and objectives VERBATIM from the exam outline "
    "(one string per bullet; drop the section number prefix and the weight from the name). "
    "Weights are the section percentages and must sum to 100. totalQuestions is the number of "
    "scored questions. passingScore is null unless the guide states a pass mark. terminology "
    "lists every product rename the guide mentions, e.g. 'Git folders (formerly Databricks Repos)' "
    "gives {old: 'Databricks Repos', new: 'Databricks Git folders'}. Do not invent anything that "
    "is not in the guide."
)

DEFAULT_PASSING = 70


def build_profile(extracted: dict[str, Any]) -> dict[str, Any]:
    """Turn the model's extraction into profile.json content (adds objective ids, defaults)."""
    passing = extracted.get("passingScore")
    note = "" if passing else (f"The guide states no pass mark; {DEFAULT_PASSING}% is used. "
                               "Raise it here to practice against a stricter bar.")
    return {
        "title": extracted["title"],
        "guide": "guide.pdf",
        "guideVersion": extracted["guideVersion"],
        "totalQuestions": extracted["totalQuestions"],
        "unscoredMax": extracted.get("unscoredMax", 0),
        "timeLimitMinutes": extracted["timeLimitMinutes"],
        "passingScore": passing or DEFAULT_PASSING,
        "passingScoreNote": note,
        "sourceDomains": ["docs.databricks.com"],
        "terminology": extracted.get("terminology", []),
        "sections": [
            {"name": s["name"], "weight": s["weight"],
             "objectives": [{"id": f"{i}.{j}", "text": text}
                            for j, text in enumerate(s["objectives"], 1)]}
            for i, s in enumerate(extracted["sections"], 1)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guide", required=True, help="Path to the official exam guide PDF.")
    parser.add_argument("--exam", required=True, help="Slug for the new exam, e.g. data-engineer-professional.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing profile.json.")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.exam):
        sys.exit("--exam must be lowercase letters, digits and dashes.")
    folder = os.path.join(cfg.EXAMS_DIR, args.exam)
    out = os.path.join(folder, "profile.json")
    if os.path.exists(out) and not args.force:
        sys.exit(f"{out} exists — pass --force to overwrite it.")
    os.makedirs(os.path.join(folder, "banks"), exist_ok=True)
    guide = os.path.join(folder, "guide.pdf")
    if os.path.abspath(args.guide) != os.path.abspath(guide):
        shutil.copyfile(args.guide, guide)

    print(f"Reading {guide} with Claude ...")
    extracted = call_claude(find_claude_cli(), args.model, SYSTEM,
                            f"Extract the exam guide at {os.path.abspath(guide)}.", PROFILE_SCHEMA,
                            tools=["Read"], allowed=["Read"], add_dir=folder)
    profile = build_profile(extracted)
    try:
        cfg.profile_from_dict(args.exam, folder, profile)   # same checks as at load time
    except ValueError as exc:
        print(f"  ! {exc} — fix it by hand in {out}", file=sys.stderr)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")
    n_obj = sum(len(s["objectives"]) for s in profile["sections"])
    print(f"Wrote {out}: {len(profile['sections'])} sections, {n_obj} objectives, "
          f"{len(profile['terminology'])} renames. Review it, then: make generate EXAM={args.exam}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
