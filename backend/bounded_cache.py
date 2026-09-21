"""Insertion-ordered bounded cache.

Several engines keep in-process memoisation dictionaries. When a large workbook
or many concurrent requests arrive, an unbounded dictionary grows without limit.
:class:`BoundedCache` behaves like a normal ``dict`` (so existing code and tests
keep working) but evicts the oldest entry once ``max_size`` is exceeded.
"""

from __future__ import annotations

import os


DEFAULT_MAX_SIZE = int(os.getenv("CACHE_MAX_ENTRIES", "512"))


class BoundedCache(dict):
    """A dict that evicts its oldest entries past ``max_size``."""

    def __init__(self, max_size=DEFAULT_MAX_SIZE, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_size = max(int(max_size), 1)
        self._evictions = 0

    def __setitem__(self, key, value):
        if key in self:
            # Re-insert so the touched entry becomes the newest.
            super().__delitem__(key)
        super().__setitem__(key, value)
        while len(self) > self.max_size:
            oldest = next(iter(self))
            super().__delitem__(oldest)
            self._evictions += 1

    @property
    def evictions(self):
        return self._evictions

    def stats(self):
        return {"size": len(self), "max_size": self.max_size, "evictions": self._evictions}


def install(module, name, max_size=DEFAULT_MAX_SIZE):
    """Replace ``module.name`` with a bounded cache and return it."""
    cache = BoundedCache(max_size)
    setattr(module, name, cache)
    return cache


def registry_stats(registry):
    """Summarise a mapping of cache name to cache object."""
    summary = {}
    for name, cache in registry.items():
        if hasattr(cache, "stats"):
            summary[name] = cache.stats()
        elif isinstance(cache, dict):
            summary[name] = {"size": len(cache), "max_size": None, "evictions": None}
    return summary
