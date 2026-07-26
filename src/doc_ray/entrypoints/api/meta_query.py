"""Deployment-meta root queries — service version and capability hints.

A small query group split out of ``query.py`` as its own mixin (the
``StructureQueries`` / ``ProjectionQueries`` pattern, D-015), mixed into the ``Query``
root. These are cheap, principal-independent capability reads, not corpus reads.
"""

from __future__ import annotations

import strawberry

from doc_ray.entrypoints.api.types import Info


@strawberry.type
class MetaQueries:
    """Service version and deployment capability hints."""

    @strawberry.field
    def version(self) -> str:
        """Return the running service's semantic version (``doc_ray.__version__``)."""
        from doc_ray import __version__

        return __version__

    @strawberry.field
    def backup_enabled(self, info: Info) -> bool:
        """Whether the in-app backup subsystem is switched on for this deployment.

        Clients read this to decide whether to *offer* backup actions at all;
        it is a capability hint, not an authorization check. ``createBackup`` /
        ``restoreBackup`` enforce the same flag server-side and additionally
        require an admin principal.
        """
        settings = info.context.settings
        return bool(settings is not None and settings.backup_enabled)
