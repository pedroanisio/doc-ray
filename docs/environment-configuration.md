---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-23"
---

# Environment Configuration — the Canonical Registry

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

Every environment variable doc-ray reads has **one** definition, in one file:
[`config/env.registry.yaml`](../config/env.registry.yaml). The sample files, the
descriptions, the defaults, and the per-host validation all derive from it, and
CI fails if any of them drift apart. This guide is how you work with that
system.

## TL;DR

```bash
# Add or change a variable  ->  edit the registry, regenerate, test
$EDITOR config/env.registry.yaml
python scripts/gen_env_samples.py            # rewrites .env.example + .env.prod.example
pytest tests/test_env_registry.py            # the drift gate

# Validate a real host file (never prints secret values)
python scripts/check_env.py .env             # dev file
python scripts/check_env.py .env.prod --prod # prod file
```

The registry is the source of truth. **Never hand-edit `.env.example` or
`.env.prod.example`** — they are generated and the gate will reject a manual
change.

## Why this exists

Before the registry, the same variable's description and default were
maintained by hand in two sample files, and neither was checked against the
code. The result was predictable drift: `.env.example` fell ~20 variables behind
what the code actually read, defaults in the samples disagreed with the code,
and a development `.env` quietly accumulated production values with nothing to
flag it. A wrong or missing knob only surfaced at runtime — a timed-out SMTP
relay, a webapp that refused to boot on a role mismatch, a `source .env` that
reached the live cluster.

The registry replaces that with a single definition and four mechanical checks,
so a divergence fails a test instead of a deployment.

## The pieces

| File | Role |
| --- | --- |
| [`config/env.registry.yaml`](../config/env.registry.yaml) | **Canonical source** — every variable: key, group, description, default, flags |
| [`scripts/gen_env_samples.py`](../scripts/gen_env_samples.py) | Generates `.env.example` + `.env.prod.example` from the registry |
| [`tests/test_env_registry.py`](../tests/test_env_registry.py) | **Drift gate** — 9 checks binding registry ↔ code ↔ samples |
| [`scripts/check_env.py`](../scripts/check_env.py) | Validates a real (gitignored) `.env` / `.env.prod` on a host |
| `.env.example`, `.env.prod.example` | Generated, tracked samples — templates to copy from |
| `.env`, `.env.prod` | Real, **gitignored** values on a host — never in the repo |

Flow:

```
                edit
config/env.registry.yaml ──► scripts/gen_env_samples.py ──► .env.example
      (source of truth)                │                     .env.prod.example   (tracked templates)
              ▲                        │                            │  copy + fill
              │  tests/test_env_registry.py (CI gate)               ▼
       code call sites ◄── binds ──────┘                     .env / .env.prod    (gitignored, per host)
                                                                    │  validate
                                                             scripts/check_env.py
```

## The file model — dev vs prod, samples vs real

- **Two tracked samples**, both generated from the registry:
  - `.env.example` — **development** defaults (local compose stack: Postgres +
    MinIO). Keys with no code default are emitted commented-out so an empty dev
    env still runs.
  - `.env.prod.example` — **production** template. Uses the registry's `prod:`
    value where set (managed cluster, in-network hosts, SMTP2GO port 2525, …);
    secrets render as `KEY=   # SECRET — set in .env.prod`, never a real value.
- **Two real files**, gitignored, one per environment, holding actual values:
  - `.env` — the development file.
  - `.env.prod` — the production file. Every `make prod-*` target invokes
    compose with `--env-file .env.prod`, which *replaces* `.env` for
    interpolation.

The samples are documentation and templates; the real files are secrets. The
registry keeps the two samples honest; `check_env.py` keeps a host's real file
honest.

### Per-environment `.env` (optional symlink pattern)

Because tooling that isn't a prod make target (bare `docker compose`, scripts
that `source .env`) reads `.env`, a host can point `.env` at the file for *its*
environment:

```bash
# On a production host (this box): .env IS the prod config.
ln -sf .env.prod .env

# On a development box: .env is (or points at) the dev config.
#   cp .env.example .env    # then fill in local values
```

This makes "which config does this host use?" unambiguous and removes the class
of bug where a dev `.env` drifts into holding production values. See
[ENVIRONMENT.md](../ENVIRONMENT.md) for the production host's specifics.

## Registry schema

Each entry under `variables:` in `config/env.registry.yaml`:

```yaml
- key: DOCRAY_SMTP_PORT            # the environment variable name (UPPER_SNAKE)
  group: email                    # section for grouping in the samples
  description: >-                  # one line; states the knob's effect
    SMTP relay port. Note: DigitalOcean blocks 25/465/587; production uses 2525
    on SMTP2GO.
  default: "587"                  # the code's default, as a string (or null)
  secret: false                   # credential material? -> placeholder in samples
  scope: app                      # app | compose | system
  prod: "2525"                    # OPTIONAL: value used in .env.prod.example
  required_in_prod: false         # OPTIONAL: check_env.py errors if unset in --prod
```

| Field | Required | Meaning |
| --- | --- | --- |
| `key` | ✓ | The variable name. Must be unique. |
| `group` | ✓ | One of the 14 groups (database, object-store, email, auth, mcp, webapp, worker, backup, extraction, resolver, vision, billing, observability, ops); orders the samples. |
| `description` | ✓ | One line, present tense, states what the knob does. No `TODO`. |
| `default` | ✓ | The code-side default **as a string**, or `null` when the code has no default (an opt-in/feature flag). Must match the code (see fidelity check). |
| `secret` | ✓ | `true` for credentials/tokens/keys. Secrets render as placeholders in `.env.prod.example` and are redacted by `check_env.py`. |
| `scope` | ✓ | `app` = read by application code (the default). `compose` = only a `${VAR:-default}` substitution in compose files. `system` = OS/CI-provided. |
| `prod` | — | Production-appropriate value for `.env.prod.example` (e.g. the managed endpoint). Falls back to `default` when absent. |
| `required_in_prod` | — | `true` marks a file-level essential whose dev default is wrong/insecure for prod and that no overlay supplies. `check_env.py --prod` errors if it is unset. |

### `default: null` — opt-in, not "missing"

A `null` default means the code has no built-in default: the feature is **off
until configured** (OIDC, SAML, SCIM, Stripe, VLM, the AWS-SDK fallback, …). It
is *not* an error for these to be unset — `check_env.py` reports them as
`INFO … optional feature(s) unset`, never as an error, unless they are also
`required_in_prod`.

## How-to

### Add a new environment variable

1. Read it in code with a recognised accessor so the gate can see it:

   ```python
   os.environ.get("DOCRAY_MY_KNOB", "default-value")
   # or the helpers: env_int/env_float/env_bool/env_str/_env_optional_str(source, "DOCRAY_MY_KNOB", ...)
   ```

2. Add a registry entry with the **same default** as the code:

   ```yaml
   - key: DOCRAY_MY_KNOB
     group: webapp
     description: One line on what it does.
     default: "default-value"
     secret: false
     scope: app
   ```

3. Regenerate and test:

   ```bash
   python scripts/gen_env_samples.py
   pytest tests/test_env_registry.py
   ```

If the gate says the registry is missing a key you *did* add code for, your
accessor call isn't matched by the extractor — see
[Indirect reads](#indirect-reads).

### Change a default

Change it in **both** the code call site and the registry `default:` — the
fidelity check asserts they are equal. Then regenerate:

```bash
python scripts/gen_env_samples.py && pytest tests/test_env_registry.py
```

### Give a variable a production-specific value

When the dev default is wrong for prod (a managed endpoint, an in-network host,
a firewall workaround), add a `prod:` field. It only affects
`.env.prod.example`:

```yaml
- key: S3_ENDPOINT
  group: object-store
  description: Object-store endpoint URL (MinIO locally, DigitalOcean Spaces in production).
  default: http://localhost:9000
  prod: https://nyc3.digitaloceanspaces.com
  secret: false
  scope: app
```

### Mark a variable required in production

For a file-level essential (wrong/insecure dev default, not supplied by a
compose overlay), add `required_in_prod: true`. `check_env.py --prod` will error
if it is unset. The current set: `DATABASE_URL`, `S3_ACCESS_KEY`,
`S3_SECRET_KEY`, `S3_ENDPOINT`, `S3_BUCKET`, `DOCRAY_WEBAPP_SECRET_KEY`.

### Validate a host's real env file

```bash
python scripts/check_env.py .env.prod --prod
```

Reports, without ever printing a secret value:

- **ERROR** — a `required_in_prod` key is unset (in `--prod`), or a non-prod
  file targets production infrastructure (the `source .env` → live-cluster
  footgun). Exit `2`.
- **WARNING** — an unknown key (no code reads it: a typo or a stale variable).
  Exit `1`.
- **INFO** — optional features that are unset (off). Exit unaffected.
- **OK** — all required-in-prod present, no prod-drift. Exit `0`.

### Regenerate and verify samples

```bash
python scripts/gen_env_samples.py            # write the two sample files
python scripts/gen_env_samples.py --check     # exit 1 if they are stale (CI mode)
```

### Bootstrap a new environment

1. Copy the right template: `cp .env.prod.example .env.prod` (or
   `.env.example .env`).
2. Fill in the `# SECRET — set in .env.prod` placeholders and any blank
   required values.
3. Validate: `python scripts/check_env.py .env.prod --prod` — resolve every
   ERROR.
4. Optionally point `.env` at it for this host: `ln -sf .env.prod .env`.

## The drift gate — what each check enforces

`tests/test_env_registry.py` (also run inside the landing checklist as
`python scripts/gen_env_samples.py --check`):

| Check | Fails when |
| --- | --- |
| `test_registry_covers_every_env_var_the_code_reads` | Code reads a variable that isn't in the registry (**completeness**). |
| `test_registry_has_no_app_keys_the_code_never_reads` | The registry lists an `app`-scoped key no code reads (**no phantoms**). |
| `test_registry_defaults_match_the_code_call_sites` | A registry `default:` disagrees with the code's literal default (**fidelity** — the anti-slop core). |
| `test_sample_files_are_generated_from_the_registry` | `.env.example` / `.env.prod.example` isn't exactly the generator's output (a hand-edit or a forgotten regenerate). |
| `test_every_entry_has_the_required_fields` | An entry is missing a field or still says `TODO`. |
| `test_keys_are_unique` | A key appears twice. |
| three self-tests | The guards above could pass vacuously (they prove each can fail). |

### Indirect reads

The extractor recognises literal calls: `os.environ[.get]`, `getenv`,
`source.get`, and `env_int/env_float/env_bool/env_str/_env_optional_str` with
the key as the first or second argument. Three patterns it *cannot* see, kept in
explicit allowlists in the test:

- **A named constant** — `os.environ.get(BACKEND_ENV, ...)`
  (`DOCRAY_RESOLVER_BACKEND`).
- **A parsed-env dict** — `env.get("DOCRAY_SMTP_CHECK_TO")`.
- **A constructed name** — `rate_limit_rule_from_env(prefix)` reads
  `f"{prefix}_REQUESTS"` / `f"{prefix}_WINDOW_SECONDS"` (the
  `DOCRAY_WEBAPP_*_RATE_LIMIT_*` keys).

If you read a variable through such a pattern, add it to `_INDIRECT_VARS` in the
test with a one-line comment. Compose-only (`${VAR:-default}`) and OS/CI
variables go to `_COMPOSE_ONLY` / `_SYSTEM` (and `scope: compose`/`system` in
the registry) instead.

## Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| `missing env var(s) the code reads: [...]` | New knob not in the registry. Add it (same default as code), regenerate. If it's read indirectly, add to `_INDIRECT_VARS`. |
| `app-scoped key(s) no code reads: [...]` | Dead registry entry, or a compose/system var mis-scoped. Remove it, or set `scope: compose`/`system`. |
| `registry default(s) disagree with the code` | A default changed on one side only. Make the registry `default:` equal the code literal. |
| `[...] is not the generator's output` | A sample was hand-edited, or the registry changed without regenerating. Run `python scripts/gen_env_samples.py`. |
| `check_env.py`: `targets PRODUCTION but is not the prod file` | A dev `.env` holds managed-cluster / Spaces values. Repoint it at the local stack, or use `.env.prod` for prod work. |
| `check_env.py`: `required in production but unset` | A `required_in_prod` essential is missing from `.env.prod`. Set it. |

## Reference

- **Add/change a var:** edit `config/env.registry.yaml` → `python scripts/gen_env_samples.py` → `pytest tests/test_env_registry.py`
- **Validate a host:** `python scripts/check_env.py <file> [--prod]`
- **CI gate:** `python scripts/gen_env_samples.py --check` (in the landing checklist) + `pytest tests/test_env_registry.py`
- **Production specifics:** [ENVIRONMENT.md](../ENVIRONMENT.md)
- **How to work in this repo:** [CLAUDE.md](../CLAUDE.md)

---

[← root README](../README.md)
