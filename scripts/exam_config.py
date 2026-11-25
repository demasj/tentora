"""Exam profiles: the single source of truth for each certification's shape.

Every exam lives in its own folder under ``exams/``::

    exams/<slug>/profile.json    title, question count, time, pass mark, sections
                                 (weight + the guide's objectives), outdated names
    exams/<slug>/guide.pdf       the official exam guide the profile came from
    exams/<slug>/banks/*.json    question banks (open JSON format, docs/format.md)

The generator, the scorer and the web app all load a ``Profile`` from here, so
weights, pass mark and objectives never drift apart. Add an exam with
``make profile GUIDE=<pdf> EXAM=<slug>`` (scripts/make_profile.py).

Run ``python3 scripts/exam_config.py [slug]`` to print an exam's blueprint.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMS_DIR = os.environ.get("EXAM_EXAMS_DIR", os.path.join(ROOT, "exams"))
DEFAULT_EXAM = "data-engineer-associate"


@dataclass(frozen=True)
class Profile:
    slug: str
    dir: str
    title: str
    total_questions: int
    time_limit_minutes: int
    passing_score: float
    sections: list[tuple[str, int]]              # (name, weight %), weights sum to 100
    objectives: dict[str, list[dict[str, str]]]  # section -> [{"id", "text"}]
    guide_version: str = ""
    unscored_max: int = 0
    terminology: list[dict[str, str]] = field(default_factory=list)  # [{"old", "new"}]
    source_domains: list[str] = field(default_factory=lambda: ["docs.databricks.com"])
    passing_score_note: str = ""

    @property
    def banks_dir(self) -> str:
        return os.path.join(self.dir, "banks")

    @property
    def weights(self) -> dict[str, int]:
        return dict(self.sections)

    def sections_json(self) -> list[dict[str, Any]]:
        """The `sections` block written into banks and exams (drives weighted scoring)."""
        return [{"name": n, "weight": w} for n, w in self.sections]

    def objective_ids(self, section: str | None = None) -> list[str]:
        secs = [section] if section else [n for n, _ in self.sections]
        return [o["id"] for s in secs for o in self.objectives.get(s, [])]

    def distribute(self, total: int | None = None) -> dict[str, int]:
        return distribute_questions(self.weights, self.total_questions if total is None else total)


def distribute_questions(weights: dict[str, float], total: int) -> dict[str, int]:
    """Split ``total`` questions across sections proportionally to ``weights``.

    Uses the largest-remainder method so the counts always sum to ``total``
    exactly (plain rounding can drift by a question or two). Weights need not
    sum to 100.
    """
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum <= 0:
        return {name: 0 for name in weights}
    exact = {name: total * w / weight_sum for name, w in weights.items()}
    floors = {name: int(v) for name, v in exact.items()}
    remainder = total - sum(floors.values())
    ranked = sorted(exact, key=lambda n: exact[n] - floors[n], reverse=True)
    for name in ranked[:remainder]:
        floors[name] += 1
    return floors


def profile_from_dict(slug: str, folder: str, data: dict[str, Any]) -> Profile:
    """Build and sanity-check a Profile; raises ValueError with a clear message."""
    try:
        sections = [(s["name"], int(s["weight"])) for s in data["sections"]]
        profile = Profile(
            slug=slug, dir=folder, title=data["title"],
            total_questions=int(data["totalQuestions"]),
            time_limit_minutes=int(data["timeLimitMinutes"]),
            passing_score=data.get("passingScore", 70),
            sections=sections,
            objectives={s["name"]: list(s.get("objectives", [])) for s in data["sections"]},
            guide_version=str(data.get("guideVersion", "")),
            unscored_max=int(data.get("unscoredMax", 0)),
            terminology=list(data.get("terminology", [])),
            source_domains=list(data.get("sourceDomains", ["docs.databricks.com"])),
            passing_score_note=data.get("passingScoreNote", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"exams/{slug}/profile.json is invalid: {exc!r}") from exc
    total = sum(w for _, w in sections)
    if total != 100:
        raise ValueError(f"exams/{slug}/profile.json: section weights sum to {total}, not 100")
    ids = profile.objective_ids()
    if len(ids) != len(set(ids)):
        raise ValueError(f"exams/{slug}/profile.json: duplicate objective ids")
    return profile


def load(slug: str | None = None, exams_dir: str | None = None) -> Profile:
    """Load the profile for ``slug`` (default: $EXAM, else the default exam)."""
    exams_dir = exams_dir or EXAMS_DIR
    slug = slug or os.environ.get("EXAM") or DEFAULT_EXAM
    if not slug.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid exam name: {slug!r}")
    folder = os.path.join(exams_dir, slug)
    path = os.path.join(folder, "profile.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        known = ", ".join(p.slug for p in list_profiles(exams_dir)) or "none"
        raise ValueError(f"Unknown exam '{slug}' (no {path}). Known exams: {known}") from None
    return profile_from_dict(slug, folder, data)


def list_profiles(exams_dir: str | None = None) -> list[Profile]:
    """Every valid profile under exams/, sorted by title. Broken ones are reported, not fatal."""
    exams_dir = exams_dir or EXAMS_DIR
    out = []
    for slug in sorted(os.listdir(exams_dir)) if os.path.isdir(exams_dir) else []:
        if os.path.isfile(os.path.join(exams_dir, slug, "profile.json")):
            try:
                out.append(load(slug, exams_dir))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"  ! skipping exam {slug}: {exc}", file=sys.stderr)
    return sorted(out, key=lambda p: p.title)


if __name__ == "__main__":
    p = load(sys.argv[1] if len(sys.argv) > 1 else None)
    dist = p.distribute()
    print(f"{p.title} — {p.total_questions} questions"
          f"{f' (+ up to {p.unscored_max} unscored)' if p.unscored_max else ''}, "
          f"{p.time_limit_minutes} min, pass at {p.passing_score}%  [guide {p.guide_version}]\n")
    for name, weight in p.sections:
        print(f"  {weight:>3}%  {dist[name]:>2} q   {name}")
    print(f"\n  total: {sum(dist.values())} questions")
