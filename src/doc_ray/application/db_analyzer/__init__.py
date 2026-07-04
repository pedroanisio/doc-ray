"""Multi-engine database structure and data-profiling analyzer.

Inspects a database's structure (schemas, tables, columns, keys, indexes,
constraints, sizes) and profiles its data (row counts, null rates, distinct
counts, duplicate candidates, numeric/date/text distributions, outliers),
then renders the findings as Markdown and JSON reports.

Engine support is pluggable through :mod:`doc_ray.application.db_analyzer.engines`;
only PostgreSQL ships today. Run via ``python -m doc_ray.entrypoints.db_analyze``.
"""

from __future__ import annotations
