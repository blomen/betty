"""
Extraction Pipeline Module

Coordinates extraction from Polymarket and betting providers,
performs cross-provider event matching, and stores data in database.
"""

from .orchestrator import ExtractionPipeline
from ..matching.normalizer import generate_canonical_id

__all__ = [
    "ExtractionPipeline",
    "generate_canonical_id",
]
