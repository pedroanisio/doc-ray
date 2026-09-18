"""The release-audit gates (CONTRIBUTING.md §6): each must fail on the shape it exists
to catch, pass on the legitimate look-alike, and pass on the live tree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import check_build_context
import check_commit_message
import check_doc_types
import check_front_door
import check_private_references
import check_secrets
import check_tracked_ignored
import pytest
from _release_audit import Finding, is_allowed, load_allow_list

ROOT = Path(__file__).resolve().parents[1]


def _rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


# --- C-1 tracked-ignored -----------------------------------------------------------


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    return tmp_path


def test_c1_tracked_file_matching_an_ignore_rule_fails(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "out").mkdir()
    (repo / "out" / "result.json").write_text("{}")
    (repo / ".gitignore").write_text("out/\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-f", "out/result.json", ".gitignore"], check=True
    )
    rules = _rules(check_tracked_ignored.tracked_ignored(repo))
    assert "TRACKED-IGNORED" in rules
    assert "LOCAL-DIR" in _rules(check_tracked_ignored.local_dir_paths(["out/result.json"]))


def test_c1_tracked_symlink_to_untracked_target_fails(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "target.md").write_text("local\n")
    os.symlink("target.md", repo / "link.md")
    subprocess.run(["git", "-C", str(repo), "add", "link.md"], check=True)
    tracked = check_tracked_ignored.tracked_files(repo)
    assert "DANGLING-SYMLINK" in _rules(check_tracked_ignored.dangling_symlinks(repo, tracked))


def test_c1_fixture_tracked_without_a_rule_passes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "static" / "fixtures").mkdir(parents=True)
    (repo / "static" / "fixtures" / "sample.pdf").write_bytes(b"%PDF")
    subprocess.run(["git", "-C", str(repo), "add", "static/fixtures/sample.pdf"], check=True)
    assert check_tracked_ignored.tracked_ignored(repo) == []
    assert check_tracked_ignored.local_dir_paths(["static/fixtures/sample.pdf"]) == []


def test_c1_live_tree_passes() -> None:
    assert check_tracked_ignored.tracked_ignored(ROOT) == []
    tracked = check_tracked_ignored.tracked_files(ROOT)
    assert check_tracked_ignored.dangling_symlinks(ROOT, tracked) == []
    assert check_tracked_ignored.local_dir_paths(tracked) == []


# --- C-2 private references --------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "text", "expected"),
    [
        ("src/doc_ray/x.py", "# see /home/someone/notes.md\n", "PRIV-PATH"),
        (
            "docs/x.md",
            "the cluster db-pgsql-abc-123-do-user-9-0.h.db.ondigitalocean.com\n",
            "PRIV-HOST",
        ),
        ("docs/x.md", "target 10.13.0.6:9400\n", "PRIV-HOST"),
        ("docs/x.md", "Block D (#8) applies it\n", "PLAN-LABEL"),
        ("db/migrations/900_x.sql", "-- Live incident (2026-07-02): the cascade wedged\n", "DIARY"),
        ("src/doc_ray/x.py", "def f():\n    # per CLAUDE.md rule 5\n    return 1\n", "RULES-REF"),
        ("docs/x.md", "this session's gap-analysis found it\n", "SESSION"),
        ("scripts/x.py", "OUT = '_ephemeral/shots'\n", "PRIV-STORE"),
    ],
)
def test_c2_fails_on_each_contamination_shape(rel: str, text: str, expected: str) -> None:
    assert expected in _rules(check_private_references.scan_file(rel, text))


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("tests/test_x.py", 'ip = "203.0.113.10"\n'),
        (
            "pgadmin/servers.example.json",
            '"Host": "REPLACE_WITH_CLUSTER_HOST.db.ondigitalocean.com"\n',
        ),
        ("db/migrations/900_x.sql", "    lane text NOT NULL CHECK (lane IN ('gpu', 'cpu')),\n"),
        ("tests/test_x.py", 'created_at = "2026-07-15T10:00:00Z"\n'),
        ("docs/TUTORIAL.md", "installed at /home/you/.claude/skills/x\n"),
        ("webapp/csrf.py", '"""Return this session\'s CSRF token."""\n'),
        ("docs/x.md", "the block size is 4 KiB\n"),
    ],
)
def test_c2_passes_on_the_legitimate_look_alikes(rel: str, text: str) -> None:
    assert check_private_references.scan_file(rel, text) == []


def test_c2_allow_list_entries_need_a_reason(tmp_path: Path) -> None:
    bad = tmp_path / "allow.txt"
    bad.write_text("DIARY\tsrc/x.py:3\n")
    with pytest.raises(SystemExit):
        load_allow_list(bad)
    good = tmp_path / "ok.txt"
    good.write_text("DIARY\tsrc/x.py:3\tthe date is fixture data\n")
    allowed = load_allow_list(good)
    finding = Finding("DIARY", "src/x.py", 3, "", "")
    assert is_allowed(finding, allowed)
    assert not is_allowed(Finding("DIARY", "src/x.py", 4, "", ""), allowed)


def test_c2_live_tree_passes() -> None:
    assert check_private_references.run(ROOT) == []


# --- C-3 doc types -------------------------------------------------------------------


def test_c3_plan_and_missing_types_fail_and_shippable_passes() -> None:
    plan = "---\ndoc_type: plan\n---\n# x\n"
    assert "DOC-TYPE-WORKING" in _rules(check_doc_types.check_document("docs/x.md", plan))
    none = "# x\nno front matter\n"
    assert "DOC-TYPE-MISSING" in _rules(check_doc_types.check_document("docs/x.md", none))
    stale = "---\ndoc_type: design\nstatus: proposed\n---\n"
    assert "DOC-STATUS-WORKING" in _rules(check_doc_types.check_document("docs/x.md", stale))
    retire = "---\ndoc_type: runbook\nretire-when: done\n---\n"
    assert "DOC-STATUS-WORKING" in _rules(check_doc_types.check_document("docs/x.md", retire))
    assert check_doc_types.check_document("docs/x.md", "---\ndoc_type: how-to\n---\n") == []


def test_c3_live_tree_passes() -> None:
    assert check_doc_types.run(ROOT) == []


# --- C-4 build context ---------------------------------------------------------------


def test_c4_allow_list_semantics() -> None:
    rules = ["*", "!src/", "!static/fixtures/", "**/__pycache__/"]
    assert check_build_context.is_sent(rules, "src/doc_ray/x.py")
    assert check_build_context.is_sent(rules, "static/fixtures/a.pdf")
    assert not check_build_context.is_sent(rules, ".env.prod")
    assert not check_build_context.is_sent(rules, "src/doc_ray/__pycache__/x.pyc")
    assert not check_build_context.is_sent(rules, "_ephemeral/x.pdf")


def test_c4_non_allowlist_file_fails(tmp_path: Path) -> None:
    (tmp_path / ".dockerignore").write_text(".venv\n.env\n")
    assert "CONTEXT-NOT-ALLOWLIST" in _rules(check_build_context.run(tmp_path))


def test_c4_live_tree_passes() -> None:
    assert check_build_context.run(ROOT) == []


# --- C-6 commit messages -------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Agent host session 9b0c1d2e-1111-2222-3333-444455556666 - baseline checkpoint",
        "sync",
        "Implement code changes to enhance functionality and improve performance",
        "fix(api): short\n",
    ],
)
def test_c6_rejects_narrative_and_non_conventional_subjects(message: str) -> None:
    assert check_commit_message.check_message(message, "msg")


@pytest.mark.parametrize(
    "message",
    [
        "fix(api): reject malformed ids as not found",
        "feat(mcp): keep the MCP session id valid across restarts\n\nThe session manager …",
        "Merge branch 'main' of https://github.com/example/doc-ray",
        "docs: describe the DOCRAY_WORK_DIR fallback",
    ],
)
def test_c6_accepts_conventional_messages(message: str) -> None:
    assert check_commit_message.check_message(message, "msg") == []


# --- C-7 secrets ---------------------------------------------------------------------


def test_c7_key_blocks_tokens_and_env_values_fail() -> None:
    assert "SECRET-KEY-BLOCK" in _rules(
        check_secrets.scan_text("x.pem", "-----BEGIN OPENSSH PRIVATE KEY-----\n")
    )
    assert "SECRET-TOKEN" in _rules(
        check_secrets.scan_text("x.py", "k = 'AKIA" + "ABCDEFGHIJKLMNOP'\n")
    )
    assert "SECRET-ENV-VALUE" in _rules(
        check_secrets.scan_text(".env.uat", "DOCRAY_API_TOKENS=abc123\n")
    )


def test_c7_samples_and_placeholders_pass() -> None:
    assert check_secrets.scan_text(".env.prod.example", "DOCRAY_API_TOKENS=   # SECRET\n") == []
    assert check_secrets.scan_text(".env.test", "POSTGRES_PASSWORD=docray\n") == []


def test_c7_live_tree_passes() -> None:
    assert check_secrets.run(ROOT) == []


# --- C-8 front door ------------------------------------------------------------------


def test_c8_adr_range_fence_and_provenance() -> None:
    registry = 'decisions:\n  - id: "D-001"\n  - id: "D-036"\n'
    assert check_front_door.adr_range("runs through **D-034**", registry)
    assert check_front_door.adr_range("runs through **D-036**", registry) == []
    assert check_front_door.fences("x.md", "```bash\necho\n") != []
    assert check_front_door.fences("x.md", "```bash\necho\n```\n") == []
    bad = 'generated_by: "Claude X via Claude Code — revised from a Codex draft"\n'
    assert "PROVENANCE" in _rules(check_front_door.provenance("x.md", bad))
    assert check_front_door.provenance("x.md", 'generated_by: "Claude X via Claude Code"\n') == []


def test_c8_live_tree_passes() -> None:
    assert check_front_door.run(ROOT) == []


# --- wiring ------------------------------------------------------------------------


def test_gates_are_wired_into_precommit_ci_and_make() -> None:
    precommit = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for script in (
        "check_tracked_ignored.py",
        "check_private_references.py",
        "check_doc_types.py",
        "check_build_context.py",
        "check_secrets.py",
        "check_front_door.py",
    ):
        assert script in precommit, script
        assert script in ci, script
        assert script in makefile, script
    assert "check_commit_message.py" in precommit and "stages: [commit-msg]" in precommit
    assert "check_commit_message.py" in ci
    assert "check_release_package.py" in ci
