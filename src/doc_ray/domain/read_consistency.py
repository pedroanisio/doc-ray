"""What each figure on a read surface actually promises, stated as data.

A screen is a view over data, so the consistency guarantee behind a number is a
fact about the interface, not only about the storage layer. doc-ray's corpus band
prints four totals side by side and three different read paths sit underneath
them:

* a **live count** over the RLS-scoped ``documents`` table — exact;
* **per-document rollups** summed out of ``document_stats`` — refreshed by the
  job that changed the counts, so exact from the moment that job commits;
* the **tenant-wide distinct-lexeme scalar** in ``corpus_stats`` — debounced,
  because recomputing it means a distinct scan the read path must not pay for
  (D-024), so it may legitimately lag.

Rendered identically, the debounced figure is a *failure that looks like a
success*: a well-formed, plausible, wrong number. Nothing errors, no alarm
fires, and the only party who can detect it is the user who just wrote. The fix
is not to make every path exact — that would trade the latency budget away for a
guarantee the surface never promised — it is to **declare** what each path
offers, per path, and let the surface say so.

The module is pure: it holds the declaration and turns a ``computed_at`` into a
freshness verdict. Reading the timestamp is the adapter's job; presenting it is
the surface's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

#: Debounce window of ``docray_refresh_stats_for_document``'s ``corpus_max_age``
#: (db/migrations/095_corpus_stats.sql). Asserted against the migration by
#: ``test_read_consistency`` so the declared promise cannot drift from the
#: database that has to keep it.
CORPUS_STATS_DEBOUNCE_SECONDS = 600


class Guarantee(str, Enum):
    """The promise a read path makes about the value it returns."""

    #: Read live from the authoritative relation. Always current.
    EXACT = "exact"
    #: Read from a derived rollup refreshed at the pipeline boundary. Current as
    #: soon as the job that changed the underlying counts commits.
    ROLLUP = "rollup"
    #: Read from a derived value whose recompute is debounced. May lag by up to
    #: ``staleness_bound_seconds``.
    DEBOUNCED = "debounced"


@dataclass(frozen=True)
class ReadPathGuarantee:
    """The declared guarantee behind one figure a surface prints."""

    figure: str
    guarantee: Guarantee
    staleness_bound_seconds: int
    source: str
    rationale: str


@dataclass(frozen=True)
class Freshness:
    """A figure's guarantee applied to the value actually being shown."""

    figure: str
    guarantee: Guarantee
    staleness_bound_seconds: int
    computed_at: datetime | None
    age_seconds: int
    stale: bool
    label: str

    def as_dict(self) -> dict[str, object]:
        """Return the verdict as JSON-ready data for an API or agent consumer."""
        return {
            "figure": self.figure,
            "guarantee": self.guarantee.value,
            "stalenessBoundSeconds": self.staleness_bound_seconds,
            "computedAt": self.computed_at.isoformat() if self.computed_at else None,
            "ageSeconds": self.age_seconds,
            "stale": self.stale,
            "label": self.label,
        }


#: The corpus stat band, declared figure by figure. Adding a number to that band
#: without adding it here is the defect this module exists to prevent.
CORPUS_STAT_GUARANTEES: tuple[ReadPathGuarantee, ...] = (
    ReadPathGuarantee(
        figure="documents",
        guarantee=Guarantee.EXACT,
        staleness_bound_seconds=0,
        source="documents",
        rationale=(
            "An index-only count over the caller's RLS-scoped documents, read live on "
            "every request."
        ),
    ),
    ReadPathGuarantee(
        figure="sentences",
        guarantee=Guarantee.ROLLUP,
        staleness_bound_seconds=0,
        source="document_stats.n_sentences",
        rationale=(
            "Summed from per-document rollups that every count-mutating job refreshes "
            "before it commits, so the total is exact for the documents already processed."
        ),
    ),
    ReadPathGuarantee(
        figure="lexemes",
        guarantee=Guarantee.DEBOUNCED,
        staleness_bound_seconds=CORPUS_STATS_DEBOUNCE_SECONDS,
        source="corpus_stats.n_distinct_lexemes",
        rationale=(
            "Distinct lexemes are not summable across documents, so the tenant scalar is "
            "recomputed rather than accumulated. The recompute is debounced to keep a "
            "17.5M-row scan off the request path (D-024), so this figure may lag."
        ),
    ),
    ReadPathGuarantee(
        figure="occurrences",
        guarantee=Guarantee.ROLLUP,
        staleness_bound_seconds=0,
        source="document_stats.n_lexeme_occurrences",
        rationale=(
            "Summed from per-document rollups refreshed by the job that wrote the "
            "occurrences, so the total is exact for the documents already processed."
        ),
    ),
)

_BY_FIGURE = {declaration.figure: declaration for declaration in CORPUS_STAT_GUARANTEES}


def guarantee_for(figure: str) -> ReadPathGuarantee:
    """Return the declaration for *figure*.

    Raises:
        KeyError: When the figure has no declaration — a surface printing an
            undeclared number is the defect, so this fails loudly rather than
            inventing a promise.
    """
    return _BY_FIGURE[figure]


def _as_utc(moment: datetime) -> datetime:
    """Return *moment* in UTC, reading a naive timestamp as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _humanize(seconds: int) -> str:
    """Render an age the way a reader thinks about it."""
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''} ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def assess(
    declaration: ReadPathGuarantee,
    computed_at: datetime | None,
    now: datetime | None = None,
) -> Freshness:
    """Apply a declaration to the value being shown.

    Args:
        declaration: The figure's declared guarantee.
        computed_at: When the value was computed; ``None`` for a live read, and
            for a debounced value that has never been computed.
        now: Reference time, defaulting to the current UTC instant.

    Returns:
        The freshness verdict, including the sentence a surface can print.
    """
    reference = _as_utc(now) if now is not None else datetime.now(timezone.utc)

    if declaration.guarantee is Guarantee.EXACT:
        return Freshness(
            figure=declaration.figure,
            guarantee=declaration.guarantee,
            staleness_bound_seconds=0,
            computed_at=None,
            age_seconds=0,
            stale=False,
            label="live",
        )

    if declaration.guarantee is Guarantee.ROLLUP:
        return Freshness(
            figure=declaration.figure,
            guarantee=declaration.guarantee,
            staleness_bound_seconds=0,
            computed_at=_as_utc(computed_at) if computed_at else None,
            age_seconds=0,
            stale=False,
            label="exact once the pipeline job for each document has committed",
        )

    if computed_at is None:
        return Freshness(
            figure=declaration.figure,
            guarantee=declaration.guarantee,
            staleness_bound_seconds=declaration.staleness_bound_seconds,
            computed_at=None,
            age_seconds=0,
            stale=True,
            label="never computed",
        )

    stamped = _as_utc(computed_at)
    # Clamp: the database clock can read ahead of the app tier, and a negative
    # age would print as a value computed in the future.
    age = max(0, int((reference - stamped).total_seconds()))
    return Freshness(
        figure=declaration.figure,
        guarantee=declaration.guarantee,
        staleness_bound_seconds=declaration.staleness_bound_seconds,
        computed_at=stamped,
        age_seconds=age,
        stale=age > declaration.staleness_bound_seconds,
        label=f"computed {_humanize(age)}",
    )
