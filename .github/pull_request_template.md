## What changed and why

<!-- One paragraph: the behaviour a user or operator sees, and the reason. -->

## Landing checklist

- [ ] `make check` passes locally (see `CONTRIBUTING.md` §6)
- [ ] Tests were written first for the behaviour this change adds or fixes
- [ ] `CHANGELOG.md` has an entry under *Unreleased*; a user-visible change also has a release note

## Semantic contamination review

Answer for every touched file under `docs/`, `CHANGELOG.md`, `README.md`, a migration, or any comment longer than three lines:

1. Would this paragraph mislead a reader who has never seen the issue tracker, a working session, or the maintainers' infrastructure?
2. Does it state an invariant, or a diary entry? (A date, a corpus count, or "we found" is a diary entry.)
3. Does it name a phase, block, issue, model or person as the reason something exists?
4. Could a script regenerate it? Then the script's output should not be committed.
5. Is it about the product, or about producing the product?
6. Would it be acceptable in `docs/public/` tomorrow?

- [ ] Reviewed; nothing above applies, or the exceptions are listed in `.release-audit-allow.txt` with a reason
