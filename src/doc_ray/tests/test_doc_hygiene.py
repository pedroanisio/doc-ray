"""Mechanical guards for documentation that tends to drift silently.

README example commands must point at documents that exist; the README's
hand-written GraphQL surface list must match the live schema; ``.env.example``
must mention every environment variable the code reads; the governing documents
must carry no project-template placeholder text; every README must reference
``DISCLAIMER.md``. None of these break a build when they rot, so each is a test.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # src/doc_ray/tests -> repo root
_README = _ROOT / "README.md"
_ENV_EXAMPLE = _ROOT / ".env.example"
_GOVERNING_DOCS = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")


# ---------------------------------------------------------------------------
# README: example commands must reference documents that actually exist.
# ---------------------------------------------------------------------------


def _bash_blocks(markdown: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", markdown, re.DOTALL)


_FIXTURE_FILE_RE = re.compile(r"\b[\w][\w.\-]*\.(?:pdf|epub|docx)\b")


def test_readme_example_documents_exist():
    readme = _README.read_text(encoding="utf-8")
    referenced = {
        m.group(0) for block in _bash_blocks(readme) for m in _FIXTURE_FILE_RE.finditer(block)
    }
    fixtures_dir = _ROOT / "static" / "fixtures"
    missing = sorted(
        name
        for name in referenced
        if not (fixtures_dir / name).exists() and not (_ROOT / name).exists()
    )
    assert not missing, (
        f"README references example document(s) that don't exist in the repo: {missing}. "
        f"Available fixtures: {sorted(p.name for p in fixtures_dir.glob('*'))}"
    )


# ---------------------------------------------------------------------------
# README: the "Other query/mutation surface" prose must name every live
# GraphQL field, not a hand-maintained snapshot of it.
# ---------------------------------------------------------------------------


def _sdl_type_fields(sdl: str, type_name: str) -> set[str]:
    match = re.search(rf"type {type_name} \{{(.*?)\n\}}", sdl, re.DOTALL)
    assert match, f"no `type {type_name} {{ ... }}` block in the schema SDL"
    return set(re.findall(r"^\s*(\w+)(?:\(|:)", match.group(1), re.MULTILINE))


def test_readme_documents_full_graphql_surface():
    from doc_ray.entrypoints.api.schema import build_schema

    sdl = build_schema().as_str()
    # `version` is a trivial health-check field, deliberately not called out.
    fields = (_sdl_type_fields(sdl, "Query") | _sdl_type_fields(sdl, "Mutation")) - {"version"}
    readme = _README.read_text(encoding="utf-8")
    missing = sorted(name for name in fields if name not in readme)
    assert not missing, (
        f"README's GraphQL API section doesn't mention: {missing}. "
        "Update the 'Other query/mutation surface' paragraph (or wherever these "
        "belong) to name every field, not a stale snapshot of the schema."
    )


# ---------------------------------------------------------------------------
# README: the Layout table's `src/doc_ray/entrypoints/` row must name every
# live entrypoint module — and only live ones (drift-risk-map C01: the row
# had silently fallen three modules behind the package).
# ---------------------------------------------------------------------------

_ENTRYPOINTS_DIR = _ROOT / "src" / "doc_ray" / "entrypoints"


def _live_entrypoint_names() -> set[str]:
    modules = {p.stem for p in _ENTRYPOINTS_DIR.glob("*.py")} - {"__init__"}
    packages = {
        d.name for d in _ENTRYPOINTS_DIR.iterdir() if d.is_dir() and (d / "__init__.py").exists()
    }
    return modules | packages


def _readme_entrypoints_row(readme: str) -> str:
    for line in readme.splitlines():
        if line.startswith("| `src/doc_ray/entrypoints/`"):
            return line
    raise AssertionError("README Layout table has no `src/doc_ray/entrypoints/` row")


def test_readme_layout_lists_every_live_entrypoint():
    row = _readme_entrypoints_row(_README.read_text(encoding="utf-8"))
    documented = set(re.findall(r"`(\w+)`", row))
    live = _live_entrypoint_names()
    missing = sorted(live - documented)
    phantom = sorted(documented - live)
    assert not missing, (
        f"README's Layout row for src/doc_ray/entrypoints/ omits live module(s): {missing}. "
        "New entrypoints must be added to the README inventory."
    )
    assert not phantom, (
        f"README's Layout row for src/doc_ray/entrypoints/ names module(s) that don't "
        f"exist: {phantom}. Remove or rename them to match the package."
    )


# ---------------------------------------------------------------------------
# .env.example: every environment variable the running code reads must be
# documented somewhere in the file (commented-out opt-in flags count).
# ---------------------------------------------------------------------------

#: Matches os.environ.get("X"), os.environ["X"], getenv("X"), the config.py
#: _env_int/_env_float/_env_bool/_env_str("X", default) helpers, and
#: extraction.py's Mapping-based source.get("X") — but not multi-line calls
#: where the name isn't on the same line as the accessor (those are added to
#: _KNOWN_MULTILINE_VARS below, since a regex can't safely see across lines).
_ENV_READ_RE = re.compile(
    r"(?:os\.environ(?:\.get)?|getenv|_env_(?:int|float|bool|str)|source\.get)"
    r"\([^)]*?['\"]([A-Z][A-Z0-9_]{3,})['\"]"
)

#: Read via a multi-line call (the variable name isn't on the same source
#: line as the `os.environ`/`_env_*` call), so the regex above can't see it.
_KNOWN_MULTILINE_VARS = {"DOCRAY_PDF_HEADING_BOLD_MAX_WORDS"}

#: Read only through docker-compose `${VAR:-default}` substitution, never by
#: application code directly — documented by the Compose files' own comments,
#: not by this test.
_COMPOSE_ONLY_VARS = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "MINIO_PORT",
    "MINIO_CONSOLE_PORT",
}

#: Standard CI control-plane sentinels read by test safety helpers, not runtime
#: configuration knobs that belong in `.env.example`.
_CI_SENTINEL_VARS = {"CI", "GITHUB_ACTIONS"}

#: OS-provided identity vars read as fallbacks (e.g. scripts/provision_host.py
#: picks the operator user from SUDO_USER/USER). The shell/sudo sets these — they
#: are not doc-ray configuration and have no place in `.env.example`.
_SYSTEM_ENV_VARS = {"USER", "SUDO_USER"}


def _env_vars_read_in_code() -> set[str]:
    found = set(_KNOWN_MULTILINE_VARS)
    for base in (_ROOT / "src" / "doc_ray", _ROOT / "webapp", _ROOT / "scripts"):
        for path in base.rglob("*.py"):
            found.update(_ENV_READ_RE.findall(path.read_text(encoding="utf-8")))
    return found - _COMPOSE_ONLY_VARS - _CI_SENTINEL_VARS - _SYSTEM_ENV_VARS


def _env_vars_documented_in_example() -> set[str]:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    # Deliberately lenient about a leading `# `: a documented-but-commented-out
    # opt-in default (e.g. DOCRAY_PRINCIPAL_ID) still counts as documented.
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{3,})=", text, re.MULTILINE))


def test_env_example_documents_all_config_vars():
    missing = sorted(_env_vars_read_in_code() - _env_vars_documented_in_example())
    assert not missing, (
        f".env.example doesn't mention these environment variable(s) the code reads: "
        f"{missing}. New runtime knobs must ship a documented default in .env.example."
    )


# ---------------------------------------------------------------------------
# Governing documents: no leftover project-template placeholder text.
# ---------------------------------------------------------------------------

_TEMPLATE_MARKERS = ("[PROJECT]", "SEED VERSION", "Replace this block", "**Project Name**")


def test_governing_docs_have_no_template_placeholders():
    for name in _GOVERNING_DOCS:
        text = (_ROOT / name).read_text(encoding="utf-8")
        present = [marker for marker in _TEMPLATE_MARKERS if marker in text]
        assert not present, f"{name} still contains project-template placeholder text: {present}"


def test_contributing_purpose_reference_is_resolvable():
    """CONTRIBUTING.md makes PURPOSE.md alignment mandatory; the file must exist.

    If PURPOSE.md were ever removed while CONTRIBUTING.md still references it, the
    constraint would silently dangle, so the contract must then acknowledge the
    absence explicitly rather than assert a dependency on a missing document.
    """
    text = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    if "PURPOSE.md" in text and not (_ROOT / "PURPOSE.md").exists():
        assert "does not exist yet" in text or "doesn't exist yet" in text, (
            "CONTRIBUTING.md references PURPOSE.md as a mandatory governing document "
            "but the file doesn't exist and CONTRIBUTING.md doesn't acknowledge that."
        )


# ---------------------------------------------------------------------------
# Every tracked README must carry the DISCLAIMER.md reference block
# (CONTRIBUTING.md §5): a convention decays as READMEs multiply.
# ---------------------------------------------------------------------------


#: Directories a filesystem walk must never mistake for project sources.
_UNTRACKED_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", "_work"})


def _tracked_readmes() -> list[str]:
    """Return every tracked README, falling back to a walk outside a git checkout.

    The containerised test image carries no ``.git`` (``.dockerignore`` excludes
    it), so ``git ls-files`` cannot answer there and this guard would fail on the
    harness rather than on a real violation. Git stays authoritative wherever the
    checkout exists; the walk keeps the rule enforced inside the image.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "*README*.md"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            str(path.relative_to(_ROOT))
            for path in _ROOT.rglob("*README*.md")
            if not _UNTRACKED_DIRS.intersection(path.parts)
        )
    return [line for line in out.stdout.splitlines() if line]


def test_every_tracked_readme_references_disclaimer():
    missing = sorted(
        path
        for path in _tracked_readmes()
        if "DISCLAIMER.md" not in (_ROOT / path).read_text(encoding="utf-8")
    )
    assert not missing, (
        f"README(s) missing the mandatory DISCLAIMER.md reference block: {missing}. "
        "CONTRIBUTING.md requires every README to reference @DISCLAIMER.md (relative "
        "to its depth), placed after the title."
    )


# ---------------------------------------------------------------------------
# Structural documentation policy must not carry unresolved repository state.
# ---------------------------------------------------------------------------


def test_gitignore_has_no_conflict_markers_and_keeps_quarantine_ignored():
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>"))
    assert re.search(r"^\.doc-quarantine/$", text, re.MULTILINE)


def test_web_manual_has_one_canonical_location_under_docs():
    assert (_ROOT / "docs" / "webapp_user_manual.md").is_file()
    assert not (_ROOT / "webapp_user_manual.md").exists()


def test_superseded_docs_do_not_reappear_at_their_old_paths():
    """Superseded documents were relocated out of the tree; DATABASE.md is the one dictionary."""
    assert not (_ROOT / "docs" / "DATABASE_SPEC.md").exists()
    assert not (_ROOT / "docs" / "SHOWCASE.md").exists()
    assert not (_ROOT / "docs" / "archive").exists()


# ---------------------------------------------------------------------------
# Self-test: each guard above must actually be able to fail, not pass vacuously.
# ---------------------------------------------------------------------------


def test_readme_disclaimer_guard_sees_the_tracked_readmes():
    readmes = _tracked_readmes()
    assert "README.md" in readmes  # the root README is always tracked
    assert all(path.endswith(".md") for path in readmes)


def test_readme_example_documents_exist_detects_a_missing_fixture(tmp_path):
    fake_readme = tmp_path / "README.md"
    fake_readme.write_text("```bash\ningester /data/does-not-exist-anywhere.pdf\n```\n")
    referenced = {
        m.group(0)
        for block in _bash_blocks(fake_readme.read_text())
        for m in _FIXTURE_FILE_RE.finditer(block)
    }
    assert referenced == {"does-not-exist-anywhere.pdf"}
    fixtures_dir = _ROOT / "static" / "fixtures"
    assert not (fixtures_dir / "does-not-exist-anywhere.pdf").exists()


def test_env_example_completeness_check_detects_an_undocumented_var(tmp_path, monkeypatch):
    documented = _env_vars_documented_in_example()
    assert "DOCRAY_TOTALLY_MADE_UP_VAR_FOR_THIS_TEST" not in documented


def test_governing_doc_placeholder_check_detects_a_marker():
    assert any(marker in "some [PROJECT] text" for marker in _TEMPLATE_MARKERS)


def test_readme_entrypoint_guard_detects_missing_and_phantom_modules():
    row = "| `src/doc_ray/entrypoints/` | Driving adapters: `cli`, `ghost_module` |"
    documented = set(re.findall(r"`(\w+)`", row))
    live = _live_entrypoint_names()
    assert "worker" in live - documented  # a real module the fake row omits
    assert "ghost_module" in documented - live  # a fake module the row invents
