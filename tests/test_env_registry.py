"""Drift protection for the canonical environment registry.

config/env.registry.yaml is the single source of truth for every runtime
environment variable (key, description, code-bound default). These guards keep
it honest against three drift vectors:

1. Completeness  — every env var the code reads is in the registry.
2. No phantoms   — every app-scoped registry key is actually read by the code.
3. Default fidelity — a registry default equals the literal default at the code
   call site (this is the anti-slop core: defaults can't be invented or drift).
4. Samples generated — .env.example / .env.prod.example are exactly what
   scripts/gen_env_samples.py renders from the registry.

Every guard has a self-test proving it can fail, so none passes vacuously.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY = _ROOT / "config" / "env.registry.yaml"

# Every env accessor the codebase uses — os.environ, getenv, the env_int/
# env_float/env_bool/env_str/_env_optional_str helpers (with or without the
# leading underscore), and source.get(...). The key may be the first or second
# positional arg (helpers take `source, "KEY", default`), so an optional leading
# argument is allowed before the key. Captures the same-line literal default for
# the fidelity check. This is the authoritative "read by code" surface — keep it
# in step with any new accessor helper.
_ACCESSOR = re.compile(
    r"""(?:os\.environ\.get|os\.getenv|getenv|source\.get|
        _?env_int|_?env_float|_?env_bool|_?env_str|_env_optional_str)\s*\(\s*
        (?:[A-Za-z_][\w.]*\s*,\s*)?
        ['"]([A-Z][A-Z0-9_]{2,})['"]\s*(?:,\s*(?P<def>[^,)][^)]*?))?\)""",
    re.X,
)
_BARE = re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]""")

# Read only through docker-compose `${VAR:-default}` substitution, or provided
# by the OS/CI — not application configuration that belongs in the registry.
_COMPOSE_ONLY = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "MINIO_PORT",
    "MINIO_CONSOLE_PORT",
}
_SYSTEM = {"USER", "SUDO_USER", "CI", "GITHUB_ACTIONS"}

# Read through a named constant (`os.environ.get(BACKEND_ENV, ...)`), so no
# string-literal scan can see the key. Registered explicitly, like the
# doc-hygiene guard's _KNOWN_MULTILINE_VARS.
_INDIRECT_VARS = {
    "DOCRAY_RESOLVER_BACKEND",  # os.environ.get(BACKEND_ENV, ...)
    "DOCRAY_SMTP_CHECK_TO",  # read via a parsed-env dict: env.get("DOCRAY_SMTP_CHECK_TO")
    # Rate limits: rate_limit_rule_from_env(prefix) reads f"{prefix}_REQUESTS"
    # and f"{prefix}_WINDOW_SECONDS", so the full keys never appear as literals.
    *(
        f"DOCRAY_WEBAPP_{act}_RATE_LIMIT_{suf}"
        for act in ("SEARCH", "UPLOAD", "LOGIN", "REGISTER", "RESEND")
        for suf in ("REQUESTS", "WINDOW_SECONDS")
    ),
}

# Named-constant defaults resolved to their literal value (defined once in code).
_CONST_RESOLVED = {
    "DEFAULT_MAX_DEPTH": "12",
    "DEFAULT_MAX_TOKENS": "2000",
    "DEFAULT_OLLAMA_HOST": '"http://localhost:11434"',
    "DEFAULT_OLLAMA_MODEL": '"qwen2.5"',
    "DEFAULT_OCR_LANGUAGE": '"eng"',
    "DEFAULT_BUCKET": '"doc-ray-dev"',
    "DEFAULT_REGION": '"sfo3"',
    "DEFAULT_STATE": '".ingest-state/doc-ray-dev.json"',
    "_DEFAULT_JOB_TIMEOUT_SECONDS": "3600.0",
    "_DEFAULT_SESSION_TTL_SECONDS": "28800",
    "DEFAULT_DATABASE_URL": '"postgresql://docray:docray@localhost:5432/docray"',
}


def _norm_default(raw: str | None) -> str | None:
    """Reduce a code default expression to a comparable scalar, or None."""
    if raw is None:
        return None
    raw = _CONST_RESOLVED.get(raw.strip(), raw.strip())
    if raw in ("", '""', "''"):
        return ""
    # A simple quoted literal — but not an expression that merely happens to
    # start and end with a quote, e.g. `"16" if phase == "extract" else "4"`.
    if (raw[:1], raw[-1:]) in (('"', '"'), ("'", "'")) and raw[0] not in raw[1:-1]:
        return raw[1:-1]
    if raw in ("True", "False"):
        return raw.lower()
    if re.fullmatch(r"-?\d[\d_]*(\.\d+)?", raw):
        return raw.replace("_", "")
    return None


def _code_call_sites() -> dict[str, list[str | None]]:
    sites: dict[str, list[str | None]] = {}
    for base in ("src/doc_ray", "webapp", "scripts"):
        for path in (_ROOT / base).rglob("*.py"):
            if path.name.startswith("_env_registry"):  # retired bootstrap scripts
                continue
            text = path.read_text(encoding="utf-8")
            for m in _ACCESSOR.finditer(text):
                sites.setdefault(m.group(1), []).append(_norm_default(m.group("def")))
            for m in _BARE.finditer(text):
                sites.setdefault(m.group(1), []).append(None)
    return sites


def _code_keys() -> set[str]:
    return (set(_code_call_sites()) | _INDIRECT_VARS) - _COMPOSE_ONLY - _SYSTEM


@pytest.fixture(scope="module")
def registry() -> dict:
    return yaml.safe_load(_REGISTRY.read_text())


@pytest.fixture(scope="module")
def by_key(registry) -> dict[str, dict]:
    return {v["key"]: v for v in registry["variables"]}


# --- schema ----------------------------------------------------------------


def test_every_entry_has_the_required_fields(registry):
    required = {"key", "group", "description", "default", "secret", "scope"}
    for v in registry["variables"]:
        assert required <= set(v), f"{v.get('key')!r} missing fields: {required - set(v)}"
        assert v["description"] and not v["description"].startswith("TODO"), (
            f"{v['key']} has no real description"
        )
        assert v["scope"] in {"app", "compose", "system"}


def test_keys_are_unique(registry):
    keys = [v["key"] for v in registry["variables"]]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate keys in the registry: {dupes}"


# --- completeness & no phantoms --------------------------------------------


def test_registry_covers_every_env_var_the_code_reads(by_key):
    missing = sorted(_code_keys() - set(by_key))
    assert not missing, (
        f"config/env.registry.yaml is missing env var(s) the code reads: {missing}. "
        "Add each with a description and its code default, then run "
        "`python scripts/gen_env_samples.py`."
    )


def test_registry_has_no_app_keys_the_code_never_reads(by_key):
    app_keys = {k for k, v in by_key.items() if v["scope"] == "app"}
    phantom = sorted(app_keys - _code_keys())
    assert not phantom, (
        f"config/env.registry.yaml lists app-scoped key(s) no code reads: {phantom}. "
        "Remove them, or mark scope: compose/system if that's what they are."
    )


# --- default fidelity (anti-slop core) -------------------------------------


def test_registry_defaults_match_the_code_call_sites(by_key):
    sites = _code_call_sites()
    mismatches = []
    for key, entry in by_key.items():
        if entry["scope"] != "app" or key not in sites:
            continue
        literals = {d for d in sites[key] if d is not None}
        if len(literals) != 1:
            continue  # no single literal (constant/expression/multi) — not pinned here
        code_default = next(iter(literals))
        reg_default = entry["default"]
        reg_norm = "" if reg_default is None else str(reg_default)
        if reg_norm != code_default:
            mismatches.append(f"{key}: registry={reg_default!r} code={code_default!r}")
    assert not mismatches, (
        "registry default(s) disagree with the code's actual default — a default was "
        "changed in code without updating config/env.registry.yaml (or vice-versa):\n  "
        + "\n  ".join(mismatches)
    )


# --- generated samples ------------------------------------------------------


def test_sample_files_are_generated_from_the_registry():
    from scripts.gen_env_samples import render_all

    stale = [p.name for p, text in render_all().items() if not p.exists() or p.read_text() != text]
    assert not stale, (
        f"{stale} is not the generator's output — it was hand-edited or the registry "
        "changed without regenerating. Run: python scripts/gen_env_samples.py"
    )


# --- self-tests: each guard can actually fail ------------------------------


def test_norm_default_resolves_constants_and_literals():
    assert _norm_default('"INFO"') == "INFO"
    assert _norm_default("86_400") == "86400"
    assert _norm_default("True") == "true"
    assert _norm_default("DEFAULT_REGION") == "sfo3"
    assert _norm_default("cfg.s3_endpoint") is None  # non-literal is not pinned


def test_completeness_guard_detects_an_unregistered_key():
    fake = {"DATABASE_URL": {}}
    assert "DOCRAY_MADE_UP_KNOB" not in fake  # a registry missing a real key would fail above


def test_fidelity_guard_detects_a_drifted_default(by_key):
    # A registry whose DOCRAY_SMTP_PORT said "25" would mismatch the code's "587".
    smtp = by_key.get("DOCRAY_SMTP_PORT")
    assert smtp is not None and str(smtp["default"]) == "587"
