"""
Calculation logging to Supabase with circuit-breaker pattern.
"""

from datetime import datetime, timezone

from supabase import Client

from backend.config import MAX_DESCRIPTION_LENGTH, logger
from backend.exceptions import DatabaseError


class CalculationLogger:
    """Logs tariff calculations to Supabase with failure tracking."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.failure_count = 0
        self.max_failures = 10

    def log(self, description: str, value: float, request_id: str) -> bool:
        """Persist a calculation record. Returns True on success."""
        try:
            data = {
                "description": description[:MAX_DESCRIPTION_LENGTH],
                "value": float(value),
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            response = self.supabase.table("Calculations").insert(data).execute()

            if not response.data:
                raise DatabaseError("No data returned from Supabase insert")

            self.failure_count = 0
            logger.info("Successfully logged calculation for request %s", request_id)
            return True

        except Exception as exc:
            self.failure_count += 1
            logger.error(
                "Failed to log to Supabase (failure #%d): %s",
                self.failure_count,
                exc,
            )

            if self.failure_count >= self.max_failures:
                logger.critical(
                    "Database logging has failed %d consecutive times!",
                    self.failure_count,
                )

            return False
