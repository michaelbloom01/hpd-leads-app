"""Enrich module exports.

Keep imports lazy so lightweight consumers (e.g. DOS cache tasks) can import
submodules without pulling in optional web-crawl dependencies at package import
time.
"""

__all__ = ["Enricher"]


def __getattr__(name: str):
    if name == "Enricher":
        from .enricher import Enricher

        return Enricher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
