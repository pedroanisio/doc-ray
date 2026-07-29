# Doc-Ray GraphQL Maximizer

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

↑ Back to the [root README](../../README.md).

Version: `0.3.0`

Repo-local skill for helping AI agents extract maximum value from Doc-Ray's
GraphQL API. It teaches agents to inspect the live schema, query the corpus with
evidence-preserving fields, use pagination and job polling correctly, distinguish
mentions from canonical identities, and validate API assumptions against the
current source tree. It also teaches agents to distinguish "no corpus" from
"no rows visible to this API principal" under Doc-Ray's RLS/auth model.

For direct corpus inventory, use the deterministic helper instead of composing
ad hoc curl/Python snippets:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/list_documents.py \
  --env-file .env \
  --auth first-configured-token
```

## Install or Update

From the Doc-Ray repository root:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/install_or_update.py
```

Default destination is `${CODEX_HOME:-~/.codex}/skills/doc-ray-graphql-maximizer`.
The installer compares SemVer from `VERSION`; it refuses to overwrite a newer
installed skill unless `--force` is used.

## Drift Check

Run this before trusting a long-lived installed copy:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/check_doc_ray_drift.py
```

The checker verifies the skill package metadata and its assumptions against
Doc-Ray's core GraphQL structure: `schema.py`, `query.py`, `mutation.py`, type
modules, FastAPI mount code, auth/RLS boundary files, README, CHANGELOG, and
exhaustive root-operation coverage against the live Strawberry schema.
