#!/usr/bin/env python3
"""Tentora web server — sit the practice exams in the browser.

Stdlib only (http.server). All grading is delegated to ``scripts/score.py`` so
the browser exam scores exactly like ``make take``. The correct answers never
leave the server while an exam is running: the browser only receives prompts
and choice texts, and the review (with answers + explanations) is sent after
the exam is submitted.

Sessions are persisted as JSON under ``results/sessions/`` so a browser
refresh, a closed tab or a container restart doesn't lose an exam in progress.
The exam clock is server-side: when the time is up the attempt is graded
automatically, like at a real test centre.

Every exam profile under ``exams/`` is offered on the home screen; each exam
keeps its own reports, retry bank, rotation, reported questions and history in
``results/<slug>/``.

Run:  python3 webapp/server.py            (http://localhost:8080)
Env:  EXAM_PORT, EXAM_HOST, EXAM_EXAMS_DIR, EXAM_RESULTS_DIR
"""

from __future__ import annotations

import glob
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import exam_config as cfg  # noqa: E402
import score  # noqa: E402

STATIC_DIR = os.path.join(ROOT, "webapp", "static")
EXAMS_DIR = os.environ.get("EXAM_EXAMS_DIR", cfg.EXAMS_DIR)
RESULTS_DIR = os.environ.get("EXAM_RESULTS_DIR", os.path.join(ROOT, "results"))
SESSIONS_DIR = os.path.join(RESULTS_DIR, "sessions")

# Grace period for network latency when the client submits right at time-up.
GRACE_SECONDS = 5

_lock = threading.Lock()
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


def profiles() -> list[cfg.Profile]:
    return cfg.list_profiles(EXAMS_DIR)


def profile(slug: str) -> cfg.Profile:
    try:
        return cfg.load(slug, EXAMS_DIR)
    except ValueError:
        raise ValueError(f"Unknown exam: {slug}") from None


def rdir(slug: str) -> str:
    """Per-exam results folder: reports, retry bank, rotation, flags, history."""
    return score.results_dir(slug, RESULTS_DIR)


# --------------------------------------------------------------------------- #
# Question pools.
# --------------------------------------------------------------------------- #

def _bank_paths(p: cfg.Profile) -> list[str]:
    return score.bank_paths(p)


def list_exams() -> list[dict[str, Any]]:
    """Every exam profile with the pools the candidate can choose from."""
    return [{"slug": p.slug, "title": p.title, "guideVersion": p.guide_version,
             "pools": list_pools(p)} for p in profiles()]


def list_pools(p: cfg.Profile) -> list[dict[str, Any]]:
    banks = score.load_banks(_bank_paths(p), p.guide_version)
    results = rdir(p.slug)
    base = {"exam": p.slug, "passingScore": p.passing_score}
    pools: list[dict[str, Any]] = []
    if banks:
        flags = set(score.load_flags(results))
        groups = [g for g in score.unique_question_groups(banks) if not score.group_keys(g) & flags]
        used = score.load_rotation(results)
        pools.append({
            **base,
            "id": "mixed",
            "title": p.title,
            "kind": "mixed",
            # The mix is only as fresh as its newest bank.
            "createdAt": max((_created_at(b["path"], b["exam"]) or 0) for b in banks) or None,
            "bankCount": len(banks),
            "poolSize": len(groups),       # unique questions (reworded duplicates count once)
            "poolFresh": sum(1 for g in groups if not score.group_keys(g) & used),
            "questions": p.total_questions,
            "unscoredMax": p.unscored_max,
            "minutes": p.time_limit_minutes,
        })
    for bank in banks:
        exam = bank["exam"]
        pools.append({
            **base,
            "id": "bank:" + os.path.basename(bank["path"]),
            "title": exam.get("title", os.path.basename(bank["path"])),
            "kind": "bank",
            "createdAt": _created_at(bank["path"], exam),
            "file": os.path.basename(bank["path"]),
            "questions": len(exam["questions"]),
            "minutes": _time_limit(exam, p),
        })
    retry = score.retry_path(results)
    if os.path.exists(retry):
        try:
            exam = score.load_json(retry)
            n = len(exam.get("questions") or [])
        except (OSError, json.JSONDecodeError):
            n = 0
        if n:
            pools.append({
                **base,
                "id": "retry",
                "title": exam.get("title", "Retry"),
                "kind": "retry",
                "createdAt": _created_at(retry, exam),
                "questions": n,
                "minutes": _time_limit(exam, p),
            })
    return pools


def _created_at(path: str, exam: dict[str, Any]) -> float | None:
    """When the bank was made: its `generatedAt` field, else the file's mtime."""
    stamp = exam.get("generatedAt")
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _time_limit(exam: dict[str, Any], p: cfg.Profile) -> int:
    """A bank's own limit, else the real exam's pace (e.g. 90 min / 45 q = 2 min per question)."""
    if exam.get("timeLimitMinutes") and len(exam["questions"]) >= p.total_questions:
        return int(exam["timeLimitMinutes"])
    return max(1, round(len(exam["questions"]) * p.time_limit_minutes / p.total_questions))


def build_exam(slug: str, pool_id: str, rng: random.Random, unscored: bool = False) -> dict[str, Any]:
    """Assemble the exam for `pool_id`, shuffled exactly like `score.py take`."""
    p = profile(slug)
    results = rdir(slug)
    if pool_id == "mixed":
        banks = score.load_banks(_bank_paths(p), p.guide_version)
        if not banks:
            raise ValueError("There are no question banks.")
        # Rotate through the pool: skip questions served in earlier exams until
        # all have been seen, then start over. Like the real exam, up to
        # `unscoredMax` unscored items may be mixed in (how many is not revealed).
        used = score.load_rotation(results)
        extra = rng.randint(0, p.unscored_max) if unscored and p.unscored_max else 0
        exam, _shortfalls, _per_file = score.build_combined_exam(
            banks, p, p.total_questions, rng, used, unscored=extra,
            exclude=set(score.load_flags(results)))
        score.save_rotation(used, results)
    elif pool_id == "retry":
        retry = score.retry_path(results)
        if not os.path.exists(retry):
            raise ValueError("There are no wrong answers to retry.")
        exam = score.load_json(retry)
    elif pool_id.startswith("bank:"):
        name = pool_id[len("bank:"):]
        allowed = {os.path.basename(b): b for b in _bank_paths(p)}
        if name not in allowed:
            raise ValueError(f"Unknown question bank: {name}")
        exam = score.load_json(allowed[name])
    else:
        raise ValueError(f"Unknown exam: {pool_id}")

    if not exam.get("questions"):
        raise ValueError("This exam has no questions.")
    questions = [score.shuffle_choices(q, rng) for q in exam["questions"]]
    rng.shuffle(questions)
    # Give every question a unique, positional id (source ids may collide).
    questions = [{**q, "id": f"q{i}"} for i, q in enumerate(questions, 1)]
    return {
        **exam,
        "exam": slug,
        "questions": questions,
        # A full mixed exam gets the real time limit (which covers its unscored
        # items); a smaller exam gets the real exam's pace per question.
        "timeLimitMinutes": _time_limit({"questions": [q for q in questions if not q.get("unscored")],
                                         "timeLimitMinutes": p.time_limit_minutes}, p)
                            if pool_id == "mixed" else _time_limit(exam, p),
        "passingScore": p.passing_score,
        "sections": exam.get("sections") or p.sections_json(),
    }


# --------------------------------------------------------------------------- #
# Sessions (persisted as JSON files).
# --------------------------------------------------------------------------- #

def _session_path(sid: str) -> str:
    if not _SESSION_ID.match(sid):
        raise KeyError(sid)
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def load_session(sid: str) -> dict[str, Any]:
    path = _session_path(sid)
    if not os.path.exists(path):
        raise KeyError(sid)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_session(session: dict[str, Any]) -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = _session_path(session["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False)
    os.replace(tmp, path)


def delete_session(sid: str) -> None:
    """Remove a finished attempt from the history. Running exams can't be deleted."""
    session = load_session(sid)
    expire_if_due(session)
    if session["status"] != "finished":
        raise ValueError("An exam in progress can't be deleted.")
    os.remove(_session_path(sid))


def session_exam(session: dict[str, Any]) -> str:
    # Sessions from before multi-exam support belong to the original exam.
    return session.get("examSlug") or session["exam"].get("exam") or cfg.DEFAULT_EXAM


def create_session(slug: str, pool_id: str, seed: int | None = None,
                   unscored: bool = False) -> dict[str, Any]:
    rng = random.Random(seed)
    exam = build_exam(slug, pool_id, rng, unscored)
    now = time.time()
    session = {
        "id": uuid.uuid4().hex,
        "examSlug": slug,
        "pool": pool_id,
        "exam": exam,
        "status": "running",
        "startedAt": now,
        "deadline": now + exam["timeLimitMinutes"] * 60,
        "answers": {},
        "flagged": [],
        "current": 0,
        "finishedAt": None,
        "result": None,
    }
    save_session(session)
    return session


def _sanitize_answers(exam: dict[str, Any], answers: Any) -> dict[str, Any]:
    """Keep only answers to real questions, using real choice ids."""
    if not isinstance(answers, dict):
        return {}
    clean: dict[str, Any] = {}
    for q in exam["questions"]:
        given = answers.get(q["id"])
        ids = {c["id"] for c in q.get("choices", [])}
        if q.get("type") == "multiple":
            if isinstance(given, list):
                picked = [str(g) for g in given if str(g) in ids]
                if picked:
                    clean[q["id"]] = sorted(set(picked))
        elif isinstance(given, str) and given in ids:
            clean[q["id"]] = given
    return clean


def update_progress(session: dict[str, Any], body: dict[str, Any]) -> None:
    exam = session["exam"]
    if "answers" in body:
        session["answers"] = _sanitize_answers(exam, body["answers"])
    if "flagged" in body and isinstance(body["flagged"], list):
        valid = {q["id"] for q in exam["questions"]}
        session["flagged"] = [f for f in body["flagged"] if f in valid]
    if isinstance(body.get("current"), int):
        session["current"] = max(0, min(body["current"], len(exam["questions"]) - 1))


def finish_session(session: dict[str, Any], reason: str) -> None:
    """Grade the attempt (via score.py), store the result, write the reports."""
    exam = session["exam"]
    scored = score.score_attempt(exam, session["answers"])
    finished = min(time.time(), session["deadline"])
    flagged = set(session["flagged"])
    review = []
    for n, item in enumerate(scored["results"], 1):
        q = item["question"]
        review.append({
            "number": n,
            "id": q["id"],
            "section": q.get("section", ""),
            "objective": q.get("objective", ""),
            "type": q.get("type", "single"),
            "prompt": q["prompt"],
            "choices": q.get("choices", []),
            "given": score._given_ids(item["given"]),
            "correct": item["correct"],
            "answered": item["given"] is not None,
            "flagged": q["id"] in flagged,
            "unscored": item.get("unscored", False),
            "explanation": q.get("explanation", ""),
            "sources": q.get("sources", []),
        })
    session["result"] = {
        "weightedScore": scored["weighted_score"],
        "passingScore": scored["passing_score"],
        "passed": scored["passed"],
        "rawCorrect": scored["raw_correct"],
        "total": scored["total"],
        "answered": sum(1 for x in review if x["answered"] and not x["unscored"]),
        "unscored": sum(1 for x in review if x["unscored"]),
        "sections": scored["sections"],
        "durationSeconds": round(finished - session["startedAt"]),
        "reason": reason,
        "review": review,
    }
    session["status"] = "finished"
    session["finishedAt"] = finished
    save_session(session)
    # Same Markdown report + retry bank + history as the terminal `make take`.
    try:
        score.save_results(exam, scored, rdir(session_exam(session)))
    except OSError as exc:  # reporting must never lose the graded attempt
        print(f"  ! could not write report: {exc}", file=sys.stderr)


def report_question(session: dict[str, Any], qid: Any, reason: Any) -> bool:
    """Report ("this question is wrong") or un-report a question of a finished attempt."""
    if session["status"] != "finished":
        raise ValueError("Questions can be reported after the exam only.")
    q = next((x for x in session["exam"]["questions"] if x["id"] == qid), None)
    if q is None:
        raise ValueError("Unknown question.")
    return score.set_flag(rdir(session_exam(session)), q,
                          None if reason is None else str(reason).strip() or "reported in review")


def expire_if_due(session: dict[str, Any]) -> None:
    if session["status"] == "running" and time.time() > session["deadline"] + GRACE_SECONDS:
        finish_session(session, "timeout")


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """What the browser may see. No correct answers (or unscored marks) while the exam runs."""
    exam = session["exam"]
    view: dict[str, Any] = {
        "id": session["id"],
        "exam": session_exam(session),
        "title": exam.get("title", "Exam"),
        "pool": session["pool"],
        "status": session["status"],
        "timeLimitMinutes": exam["timeLimitMinutes"],
        "passingScore": exam["passingScore"],
        "startedAt": session["startedAt"],
        "remainingSeconds": max(0, round(session["deadline"] - time.time())),
        "answers": session["answers"],
        "flagged": session["flagged"],
        "current": session["current"],
        "questions": [
            {
                "id": q["id"],
                "type": q.get("type", "single"),
                "prompt": q["prompt"],
                "choices": [{"id": c["id"], "text": c["text"]} for c in q.get("choices", [])],
                # "Select TWO" is in the prompt already; this lets the UI enforce it.
                "selectCount": (sum(1 for c in q.get("choices", []) if c.get("correct"))
                                if q.get("type") == "multiple" else 1),
            }
            for q in exam["questions"]
        ],
    }
    if session["status"] == "finished":
        flags = score.load_flags(rdir(session_exam(session)))
        view["result"] = {**session["result"], "review": [
            {**x, "reported": score._norm_prompt(x["prompt"]) in flags}
            for x in session["result"]["review"]]}
    return view


def list_attempts() -> list[dict[str, Any]]:
    attempts = []
    for path in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        expire_if_due(s)
        item = {
            "id": s["id"],
            "exam": session_exam(s),
            "title": s["exam"].get("title", "Exam"),
            "pool": s["pool"],
            "status": s["status"],
            "startedAt": s["startedAt"],
            "questions": len(s["exam"]["questions"]),
        }
        if s["status"] == "finished":
            r = s["result"]
            item.update(score=r["weightedScore"], passed=r["passed"], scored=r["total"],
                        rawCorrect=r["rawCorrect"], durationSeconds=r["durationSeconds"])
        else:
            item.update(remainingSeconds=max(0, round(s["deadline"] - time.time())),
                        answered=len(s["answers"]))
        attempts.append(item)
    return sorted(attempts, key=lambda a: -a["startedAt"])


# --------------------------------------------------------------------------- #
# HTTP.
# --------------------------------------------------------------------------- #

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Tentora"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        sys.stderr.write(f"  {datetime.now():%H:%M:%S} {fmt % args}\n")

    # -- helpers ---------------------------------------------------------- #
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("Request too large.")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            # Single-page app: unknown paths fall back to the shell.
            full = os.path.join(STATIC_DIR, "index.html")
        with open(full, "rb") as f:
            body = f.read()
        self._send(HTTPStatus.OK, body,
                   _CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    # -- routes ----------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/catalog":
                with _lock:
                    self._json(HTTPStatus.OK, {"exams": list_exams(), "attempts": list_attempts()})
            elif m := re.fullmatch(r"/api/sessions/([0-9a-f]{32})", path):
                with _lock:
                    s = load_session(m.group(1))
                    expire_if_due(s)
                    self._json(HTTPStatus.OK, public_view(s))
            elif path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
            else:
                self._static(path)
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Session not found.")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            if path == "/api/sessions":
                seed = body.get("seed")
                with _lock:
                    s = create_session(str(body.get("exam") or cfg.DEFAULT_EXAM),
                                       str(body.get("pool", "")),
                                       seed if isinstance(seed, int) else None,
                                       unscored=body.get("unscored") is True)
                    self._json(HTTPStatus.CREATED, public_view(s))
            elif m := re.fullmatch(r"/api/sessions/([0-9a-f]{32})/report", path):
                with _lock:
                    s = load_session(m.group(1))
                    reported = report_question(s, body.get("question"),
                                               body.get("reason") if body.get("reported", True) else None)
                    self._json(HTTPStatus.OK, {"reported": reported})
            elif m := re.fullmatch(r"/api/sessions/([0-9a-f]{32})/(progress|submit)", path):
                with _lock:
                    s = load_session(m.group(1))
                    expire_if_due(s)
                    if s["status"] == "running":
                        update_progress(s, body)
                        if m.group(2) == "submit":
                            finish_session(s, "submitted")
                        else:
                            save_session(s)
                    self._json(HTTPStatus.OK, public_view(s))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Session not found.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if m := re.fullmatch(r"/api/sessions/([0-9a-f]{32})", path):
                with _lock:
                    delete_session(m.group(1))
                self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
            else:
                self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Session not found.")
        except ValueError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))


def main() -> None:
    host = os.environ.get("EXAM_HOST", "127.0.0.1")
    port = int(os.environ.get("EXAM_PORT", "8080"))
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"  Tentora running at http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
