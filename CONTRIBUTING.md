# Contributing to Tentora

Thanks for helping. Issues and pull requests are welcome.

## Ground rules

- Python 3 standard library only, so no new dependencies.
- Run `make test` before you open a pull request. CI runs the same command.
- Keep the exam UI faithful to a real test centre: no study aids during the exam,
  unscored items stay unidentified, and answers appear only after submitting.

## Question content

- Ground every question in the exam provider's official docs, and cite the page
  URL(s) in `sources`. No third-party blogs or forums.
- Use current product names. Add renames to the profile's `terminology`.
- Every question maps to an objective id from the official guide.
- **Never contribute real exam questions** or anything covered by a candidate
  agreement or NDA. **Never commit official exam guides** or their verbatim
  sample questions: they are the provider's copyright.
- Run `make validate EXAM=<slug>` on any bank you change. The bank format is in
  [docs/format.md](docs/format.md).

## Fixing a wrong question

Open an issue with the question id, the bank file, and the official doc page
that shows the correct answer. Or fix it in a pull request.

## Adding an exam

See "Adding another exam" in the [README](README.md). Commit `profile.json` and
the banks, not `guide.pdf`.
