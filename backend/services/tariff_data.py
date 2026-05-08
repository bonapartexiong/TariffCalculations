"""
Tariff data loading and product matching via TF-IDF + cosine similarity.
"""

import os
from typing import Optional

import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer

from backend.config import SIMILARITY_THRESHOLD, logger
from backend.exceptions import ProductMatchError
from backend.models import ProductMatch


class TariffDataService:
    """Handles tariff data loading and product matching."""

    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None

    def load_data(self) -> None:
        """Load and validate tariff data from Excel file.

        Looks for tariffs.xlsx in the project root (one level above backend/).
        """
        try:
            # __file__ is backend/services/tariff_data.py
            # dirname 1 → backend/services/
            # dirname 2 → backend/
            # dirname 3 → project root (where tariffs.xlsx lives)
            services_dir = os.path.dirname(os.path.abspath(__file__))
            backend_dir = os.path.dirname(services_dir)
            project_root = os.path.dirname(backend_dir)
            excel_path = os.path.join(project_root, "tariffs.xlsx")

            if not os.path.exists(excel_path):
                raise FileNotFoundError(f"Tariff file not found at {excel_path}")

            self.df = pd.read_excel(excel_path)

            required_columns = ["Description", "Tariff"]
            missing = [c for c in required_columns if c not in self.df.columns]
            if missing:
                raise ValueError(f"Missing required columns: {missing}")

            if self.df.empty:
                raise ValueError("Tariff data is empty")

            if self.df["Description"].isna().any():
                logger.warning("Found null descriptions — dropping them")
                self.df = self.df.dropna(subset=["Description"])

            if self.df["Tariff"].isna().any():
                raise ValueError("Found null tariff rates")

            if (self.df["Tariff"] < 0).any():
                raise ValueError("Tariff rates must be non-negative")

            # Auto-normalize rates stored as percentages (e.g. 10 → 0.10)
            if (self.df["Tariff"] > 1).any():
                over_1 = (self.df["Tariff"] > 1).sum()
                logger.warning(
                    "Found %d tariff rates > 1 — dividing by 100 to normalize",
                    over_1,
                )
                self.df["Tariff"] = self.df["Tariff"].apply(
                    lambda r: r / 100 if r > 1 else r
                )

            self.vectorizer = TfidfVectorizer(
                stop_words="english",
                max_features=5000,
                ngram_range=(1, 2),
            )
            self.tfidf_matrix = self.vectorizer.fit_transform(
                self.df["Description"]
            )

            logger.info("Successfully loaded %d tariff records", len(self.df))

        except Exception:
            logger.exception("Failed to load tariff data")
            raise

    def find_match(self, description: str) -> ProductMatch:
        """Find the best matching product from the tariff database."""
        if self.vectorizer is None or self.tfidf_matrix is None:
            raise ValueError("Tariff data not loaded")

        try:
            input_vector = self.vectorizer.transform([description])
            similarities = cosine_similarity(input_vector, self.tfidf_matrix)
            best_idx = similarities.argmax()
            confidence = float(similarities[0, best_idx])

            if confidence < SIMILARITY_THRESHOLD:
                raise ProductMatchError(
                    f"Low confidence match (confidence: {confidence:.2f}). "
                    "Please provide a more specific product description."
                )

            matched_row = self.df.iloc[best_idx]

            return ProductMatch(
                description=matched_row["Description"],
                tariff_rate=float(matched_row["Tariff"]),
                confidence=confidence,
                index=best_idx,
            )

        except ProductMatchError:
            raise
        except Exception as exc:
            logger.error("Error finding product match: %s", exc)
            raise ProductMatchError(f"Unable to match product: {exc}")
