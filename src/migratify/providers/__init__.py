"""Music service adapters.

Every provider implements the same :class:`~migratify.providers.base.MusicProvider`
contract as both a source and a destination, which is what makes migration
symmetric.
"""

from migratify.providers.base import MusicProvider

__all__ = ["MusicProvider"]
