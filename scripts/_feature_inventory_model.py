"""Record model, evidence scoring, and corroboration index for the feature inventory.

Every row in the inventory is a :class:`Feature` carrying the *evidence* that
produced it, not an assertion by a human or a model. The confidence band is a
pure function of those evidence signals (see :data:`SIGNAL_WEIGHTS`), so two runs
over the same tree always agree, and a capability cannot gain confidence without
gaining a verifiable signal.

Signal meanings:

``runtime-registry``
    The capability was enumerated by *executing* the production wiring (GraphQL
    schema build, MCP ``TOOLS`` tuples, Flask ``url_map``, dispatch dicts). It
    cannot appear unless the running system registers it.
``static-declaration``
    Parsed from a declarative, checked-in source of truth (env registry YAML,
    filesystem layout). Real, but one step removed from the running system.
``source-located``
    ``inspect`` resolved the implementation to a file and line.
``documented``
    A docstring or registry description exists.
``tested``
    At least one file in the test suites mentions the capability's identifier.
``handler-bound``
    A declared identifier is wired to an executable handler (job kinds).
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Evidence signal -> weight. The score is the sum of a record's distinct signals.
SIGNAL_WEIGHTS: dict[str, int] = {
    "runtime-registry": 2,
    "static-declaration": 1,
    "source-located": 1,
    "documented": 1,
    "tested": 1,
    "handler-bound": 1,
}

#: Score thresholds for the reported confidence band (inclusive lower bounds).
CONFIDENCE_BANDS: tuple[tuple[int, str], ...] = ((4, "high"), (2, "medium"), (0, "low"))

#: Directories scanned to corroborate a capability with test references.
TEST_ROOTS = ("tests", "src/doc_ray/tests")

#: Shortest probe token accepted by the corroboration index. Anything shorter (``/``,
#: ``md``, ``en``) is a fragment whose matches would carry no information. Collectors
#: additionally quote or parenthesise ambiguous probes ("delete" -> ``"delete"``) so a
#: common English word cannot corroborate a capability by accident.
MIN_PROBE_LEN = 4


@dataclass
class Feature:
    """One mechanically detected capability plus the evidence behind it."""

    id: str
    name: str
    category: str
    surface: str
    description: str = ""
    source_file: str = ""
    source_line: int = 0
    signature: str = ""
    signals: list[str] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)
    test_refs: int = 0
    notes: str = ""

    @property
    def location(self) -> str:
        """``path:line`` for the implementation, or an empty string if unresolved."""
        if not self.source_file:
            return ""
        return f"{self.source_file}:{self.source_line}" if self.source_line else self.source_file

    @property
    def evidence_score(self) -> int:
        """Sum of the weights of this record's distinct evidence signals."""
        return sum(SIGNAL_WEIGHTS.get(s, 0) for s in set(self.signals))

    @property
    def confidence(self) -> str:
        """Confidence band derived from :attr:`evidence_score`, capped by evidence kind.

        Runtime evidence is strictly stronger than a static declaration: a tool the
        server registers provably exists in the running system, while a YAML entry
        only proves someone wrote it down. Records lacking ``runtime-registry`` are
        therefore capped at ``medium`` no matter how many weaker signals they
        accumulate, so the band ordering never implies parity between the two.
        """
        band = "low"
        for threshold, candidate in CONFIDENCE_BANDS:
            if self.evidence_score >= threshold:
                band = candidate
                break
        if band == "high" and "runtime-registry" not in set(self.signals):
            return "medium"
        return band

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable view, evidence included."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "surface": self.surface,
            "description": self.description,
            "source": self.location,
            "signature": self.signature,
            "evidence": {
                "signals": sorted(set(self.signals)),
                "score": self.evidence_score,
                "test_references": self.test_refs,
            },
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class CollectorResult:
    """Outcome of one collector: its records, or the error that stopped it."""

    name: str
    method: str
    features: list[Feature] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the collector completed without raising."""
        return not self.error


def first_line(text: str | None) -> str:
    """First non-empty line of ``text``, whitespace-collapsed."""
    if not text:
        return ""
    for line in text.strip().splitlines():
        if line.strip():
            return re.sub(r"\s+", " ", line.strip())
    return ""


def locate(obj: Any) -> tuple[str, int]:
    """Resolve ``obj`` to a repo-relative source file and its first line.

    Returns ``("", 0)`` when the object is a builtin, a C extension, or otherwise
    has no Python source — an honest "unresolved", never a guess.
    """
    target = inspect.unwrap(obj) if callable(obj) else obj
    try:
        path = inspect.getsourcefile(target) or ""
        # co_firstlineno is the `def` (or first decorator) line exactly; getsourcelines
        # backs up over preceding comments and would point above the definition.
        code = getattr(target, "__code__", None)
        line = code.co_firstlineno if code else inspect.getsourcelines(target)[1]
    except (TypeError, OSError):
        return "", 0
    if not path:
        return "", 0
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT)), line
    except ValueError:
        return str(resolved), line


def signature_of(fn: Any) -> str:
    """Rendered call signature of ``fn``, or an empty string when unavailable."""
    try:
        return f"{getattr(fn, '__name__', '?')}{inspect.signature(inspect.unwrap(fn))}"
    except (TypeError, ValueError):
        return ""


def grep_line(path: Path, pattern: str) -> int:
    """1-based line number of the first ``pattern`` match in ``path``, else 0."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for number, line in enumerate(text.splitlines(), start=1):
        if re.search(pattern, line):
            return number
    return 0


class CorroborationIndex:
    """Reference index over the test suites, used to corroborate detected features.

    A capability that no test mentions is still reported — it is simply reported
    with one fewer evidence signal. The index deliberately matches identifiers
    literally (word-boundary, minimum length) rather than semantically: it answers
    "is this name exercised anywhere?", not "is this behaviour covered?". Probes
    shorter than :data:`MIN_PROBE_LEN` are dropped rather than matched loosely,
    because a short common word would corroborate everything and mean nothing.
    """

    def __init__(self, root: Path = ROOT, test_roots: tuple[str, ...] = TEST_ROOTS) -> None:
        """Load every test source under ``test_roots`` into memory once."""
        self._files: list[str] = []
        for rel in test_roots:
            base = root / rel
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                try:
                    self._files.append(path.read_text(encoding="utf-8"))
                except OSError:
                    continue

    @property
    def file_count(self) -> int:
        """Number of test files loaded into the index."""
        return len(self._files)

    def count(self, probes: list[str]) -> int:
        """Number of indexed test files mentioning any of ``probes``."""
        tokens = [p for p in probes if p and len(p.strip("\"'")) >= MIN_PROBE_LEN]
        if not tokens:
            return 0
        parts = []
        for token in tokens:
            escaped = re.escape(token)
            # Identifier-like probes match on word boundaries; paths/URIs match literally.
            parts.append(rf"\b{escaped}\b" if token.isidentifier() else escaped)
        pattern = re.compile("|".join(parts))
        return sum(1 for text in self._files if pattern.search(text))


def apply_corroboration(features: list[Feature], index: CorroborationIndex) -> None:
    """Attach the ``tested`` signal to every feature the test suites mention."""
    for feature in features:
        feature.test_refs = index.count(feature.probes or [feature.name])
        if feature.test_refs:
            feature.signals.append("tested")
        if feature.source_file:
            feature.signals.append("source-located")
        if feature.description:
            feature.signals.append("documented")
