# Scoring

Pass/fail uses a **topic-weighted** score. Each section's percent correct is
multiplied by its weight. Only sections that have questions count, and their
weights are rescaled to 100%. Unscored items never count. Every attempt is
appended to `results/<slug>/history.jsonl`, per section and per objective; that
history is what `FOCUS=weak` uses.


## Example

An exam has three sections: A (weight 30%), B (50%) and C (20%). Your attempt
drew no questions from C, so A and B are rescaled to 37.5% and 62.5%.

| Section | Weight | Rescaled | Correct | Score |
|---------|-------:|---------:|--------:|------:|
| A | 30% | 37.5% | 6 / 8 | 75% |
| B | 50% | 62.5% | 9 / 10 | 90% |

Topic-weighted score: 0.375 × 75 + 0.625 × 90 = **84.4%**. With a pass mark of
80% that is a PASS, even though a plain count (15 / 18 = 83.3%) would give a
different number.

## Reported questions

A question you report in the review (*Report a problem with this question*) is
stored in `results/<slug>/flags.json` and is never drawn again. `make reverify`
adds automatic reports for questions the current docs contradict.
