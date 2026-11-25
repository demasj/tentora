# Open exam format (JSON)

This is the authoritative, easy-to-author format the app is guaranteed to
support. VCE (`.vce`) import is best-effort; this JSON format is the stable
path. Validate imports against a schema (zod in the app) and fail with a clear
per-question error rather than silently dropping questions.

## Top-level shape

```jsonc
{
  "title": "Databricks Certified Data Engineer Associate",
  "source": "json",                 // "vce" | "json"
  "generatedAt": "2026-07-10T11:06:45+02:00", // optional ISO 8601; when the bank was made
  "timeLimitMinutes": 90,            // optional
  "passingScore": 70,               // optional, percentage
  "sections": [                      // optional; enables topic-weighted scoring
    { "name": "Data Ingestion and Loading", "weight": 21 }
  ],
  "questions": [ /* see below */ ]
}
```

Banks written by `make generate` also carry `"exam"` (the profile slug) and
`"guideVersion"` (the exam-guide version they were written for). A bank whose
`guideVersion` differs from its profile's is stale and is skipped; banks
without one are always used.

`sections` is an extension used by the scoring tool: each entry gives a section
name and its exam weight (a percentage). Weights across all sections should sum
to 100. Every question's `section` should match one of these names. If
`sections` is omitted, scoring falls back to plain percent-correct.

`generatedAt` is shown as the bank's date in the browser app. `make generate`
writes it automatically. Without it the app falls back to the file's
modification time, which git does not preserve: after a clone that would be the
checkout time. So keep `generatedAt` on banks that are checked in.

## Question shape

```jsonc
{
  "id": "q1",                        // unique within the exam
  "type": "single",                  // single | multiple | trueFalse | fillBlank | ordering | dragDrop | hotspot
  "section": "Data Ingestion and Loading",  // optional, used for weighted scoring
  "prompt": "Which ...?",            // may contain markdown / inline data-URI images
  "choices": [
    { "id": "a", "text": "Alpha", "correct": true },
    { "id": "b", "text": "Beta",  "correct": false }
  ],
  "explanation": "Alpha is correct because ...",  // shown in review / study mode
  "images": [],                      // optional data URIs, kept self-contained
  "objective": "2.3",                // optional: the exam-guide objective id (profile.json)
  "sources": ["https://docs.databricks.com/..."],  // optional: official pages backing the answer
  "verification": {                  // optional: result of the independent fact check
    "status": "confirmed",           // confirmed | unverified
    "note": "The page says ...",
    "checkedAt": "2026-11-22"
  }
}
```

`objective`, `sources` and `verification` are required for generated questions
(`make generate` rejects anything without them). Sources must be `https` URLs on
the profile's `sourceDomains`. In a built exam, extra questions carry
`"unscored": true`. They are graded for the review but never count towards the
score.

### Question types used by the Databricks banks

The Databricks DE Associate exam is entirely multiple-choice, so the practice
banks use three types:

| `type`      | Author it as | Correctly answered when |
|-------------|--------------|--------------------------|
| `single`    | 4 choices (`a`–`d`), exactly one `correct: true` | the chosen choice id equals the correct one |
| `multiple`  | 4 choices, two or more `correct: true`; prompt says "Select TWO" | the chosen set equals the correct set (all-or-nothing) |
| `trueFalse` | exactly two choices with ids `"true"` and `"false"` | the chosen id matches the correct one |

Databricks exams are multiple choice only, so `fillBlank`, `ordering`,
`dragDrop` and `hotspot` are reserved names and are not supported by the
grader.

## Answers file (for the scoring tool)

`scripts/score.py grade <exam> <answers>` reads a flat map of question id →
your answer:

```jsonc
{
  "q1": "b",           // single / trueFalse: the chosen choice id
  "q6": ["a", "b"],    // multiple: list of chosen choice ids
  "q12": "true"        // trueFalse: "true" | "false"
}
```

Keys that are not question ids (e.g. a leading `"_comment"`) are ignored.
