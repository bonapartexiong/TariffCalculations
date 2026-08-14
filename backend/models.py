"""
Data classes used across the application.
"""

from dataclasses import dataclass


@dataclass
class ProductMatch:
    """Represents a matched product from the tariff database."""
    description: str
    tariff_rate: float
    confidence: float
    index: int
    group: str = ""
    hts_number: str = ""
    match_source: str = "lexical_tfidf"


@dataclass
class FeeBreakdown:
    """Breakdown of all fees and duties."""
    duty: float
    merchandise_processing_fee: float
    harbor_maintenance_fee: float
    subtotal: float
    tariff_rate: float
    matched_description: str
    confidence: float
