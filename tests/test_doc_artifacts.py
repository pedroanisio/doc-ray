"""Checksum gates for released binary documentation artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import check_doc_artifacts as artifacts

ROOT = Path(__file__).resolve().parents[1]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_checksum_gate_detects_source_or_artifact_drift(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    artifact = tmp_path / "guide.pdf"
    source.write_bytes(b"source")
    artifact.write_bytes(b"artifact")
    manifest = tmp_path / "guide.pdf.sha256"
    manifest.write_text(
        f"{_digest(b'source')}  guide.md\n{_digest(b'artifact')}  guide.pdf\n",
        encoding="utf-8",
    )
    assert artifacts.check_checksum_file(manifest) == []

    source.write_bytes(b"changed")
    problems = artifacts.check_checksum_file(manifest)
    assert len(problems) == 1
    assert "guide.md" in problems[0]


def test_released_design_guide_pair_matches_its_manifest() -> None:
    problems = artifacts.check_checksum_file(ROOT / "docs" / "diretrizes-de-design-2027.pdf.sha256")
    assert not problems, "design-guide artifact drift:\n" + "\n".join(problems)


def test_artifact_gate_is_wired_into_local_and_ci_checks() -> None:
    command = "scripts/check_doc_artifacts.py"
    for relative in ("Makefile", ".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        assert command in (ROOT / relative).read_text(encoding="utf-8")
