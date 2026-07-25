"""Typed, sourced, epistemically-tagged assumptions — the model's only inputs.

Every quantity the capacity model consumes is an :class:`Assumption` registered
in an :class:`AssumptionRegistry`. An assumption carries:

* ``value`` — the number actually used;
* ``source`` — where it came from (:class:`Source`), so the chain
  *application behavior → … → failure thresholds* stays auditable;
* ``citation`` — a repo ``path:line``, a PostgreSQL documentation URL, a
  literature reference, or the sentence "conservative engineering assumption";
* ``epistemic`` — whether downstream conclusions built on it are analytical
  (:attr:`Epistemic.ANALYTICAL`) or must be validated empirically
  (:attr:`Epistemic.EMPIRICAL`);
* ``sensitivity_key`` — non-empty when the sensitivity analysis is allowed to
  perturb it (only meaningful for scalar knobs, not physical constants).

Nothing in the model may use a hidden literal: modules read values through the
registry so the final report can enumerate every input, and so scenario files
(JSON) can override any of them by name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Source(Enum):
    """Provenance class of an assumption."""

    #: Derived from this repository (migrations, code, compose files, docs).
    REPO = "repo"
    #: Documented PostgreSQL behavior, format, or default (with a docs URL).
    PG_DOCS = "postgresql-docs"
    #: Standard published result (queueing theory, storage formats in source).
    LITERATURE = "literature"
    #: Computed inside the model from other assumptions.
    DERIVED = "derived"
    #: Explicit conservative engineering assumption — no authoritative source.
    ASSUMED = "assumed"


class Epistemic(Enum):
    """Whether a value (and conclusions leaning on it) is analytic or needs tests."""

    #: Follows from documented formats/limits or repo facts; arithmetic only.
    ANALYTICAL = "analytical"
    #: Plausible and conservative, but only a real benchmark can confirm it.
    EMPIRICAL = "requires-empirical-validation"


@dataclass(frozen=True)
class Assumption:
    """One named, sourced scalar input to the model."""

    name: str
    value: float
    unit: str
    source: Source
    citation: str
    rationale: str
    epistemic: Epistemic = Epistemic.ANALYTICAL
    #: Non-empty ⇒ the sensitivity pass may scale this value (×0.5 / ×2.0).
    sensitivity_key: str = ""

    def scaled(self, factor: float) -> Assumption:
        """Return a copy with ``value`` multiplied by ``factor`` (sensitivity runs)."""
        return Assumption(
            name=self.name,
            value=self.value * factor,
            unit=self.unit,
            source=self.source,
            citation=self.citation,
            rationale=self.rationale,
            epistemic=self.epistemic,
            sensitivity_key=self.sensitivity_key,
        )


@dataclass
class AssumptionRegistry:
    """Ordered collection of every assumption a model run consumed.

    ``presets`` lets a caller pin values *before* the model defines them
    (scenario overrides, sensitivity perturbations): when :meth:`define` sees
    a preset for the name, the preset value wins and the entry records the
    replaced default, keeping the override auditable.
    """

    entries: dict[str, Assumption] = field(default_factory=dict)
    presets: dict[str, float] = field(default_factory=dict)

    def add(self, assumption: Assumption) -> Assumption:
        """Register ``assumption``; re-adding the same name must be identical."""
        if assumption.name in self.presets:
            preset = self.presets[assumption.name]
            if preset != assumption.value:
                assumption = Assumption(
                    name=assumption.name,
                    value=preset,
                    unit=assumption.unit,
                    source=Source.ASSUMED,
                    citation=(
                        f"override (default {assumption.value} from "
                        f"{assumption.source.value}: {assumption.citation})"
                    ),
                    rationale=assumption.rationale,
                    epistemic=assumption.epistemic,
                    sensitivity_key=assumption.sensitivity_key,
                )
        existing = self.entries.get(assumption.name)
        if existing is not None and existing != assumption:
            raise ValueError(f"conflicting redefinition of assumption {assumption.name!r}")
        self.entries[assumption.name] = assumption
        return assumption

    def define(
        self,
        name: str,
        value: float,
        unit: str,
        source: Source,
        citation: str,
        rationale: str,
        epistemic: Epistemic = Epistemic.ANALYTICAL,
        sensitivity_key: str = "",
    ) -> Assumption:
        """Create-and-register shorthand used by every model module."""
        return self.add(
            Assumption(
                name=name,
                value=value,
                unit=unit,
                source=source,
                citation=citation,
                rationale=rationale,
                epistemic=epistemic,
                sensitivity_key=sensitivity_key,
            )
        )

    def value(self, name: str) -> float:
        """Return the value of a registered assumption (KeyError if absent)."""
        return self.entries[name].value

    def override(self, name: str, value: float) -> None:
        """Replace the value of an existing assumption (scenario/config override)."""
        current = self.entries.get(name)
        if current is None:
            raise KeyError(f"unknown assumption {name!r}; overrides must target a declared input")
        self.entries[name] = Assumption(
            name=current.name,
            value=value,
            unit=current.unit,
            source=Source.ASSUMED,
            citation=f"user override (was {current.value} from {current.source.value})",
            rationale=current.rationale,
            epistemic=Epistemic.EMPIRICAL,
            sensitivity_key=current.sensitivity_key,
        )

    def apply_overrides(self, overrides: dict[str, float]) -> None:
        """Apply a ``{name: value}`` override map (from ``--config`` JSON)."""
        for name, value in overrides.items():
            self.override(name, float(value))

    def sensitivity_candidates(self) -> list[Assumption]:
        """Assumptions the sensitivity pass is allowed to perturb."""
        return [a for a in self.entries.values() if a.sensitivity_key]

    def to_json_obj(self) -> list[dict[str, object]]:
        """Serialize every entry for the machine-readable report."""
        return [
            {
                "name": a.name,
                "value": a.value,
                "unit": a.unit,
                "source": a.source.value,
                "citation": a.citation,
                "rationale": a.rationale,
                "epistemic": a.epistemic.value,
                "sensitive": bool(a.sensitivity_key),
            }
            for a in self.entries.values()
        ]


def load_override_file(path: Path) -> dict[str, float]:
    """Read a ``{assumption-name: value}`` JSON override file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: override file must be a JSON object of name → number")
    out: dict[str, float] = {}
    for key, val in raw.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"{path}: override {key!r} must be a number, got {val!r}")
        out[str(key)] = float(val)
    return out
