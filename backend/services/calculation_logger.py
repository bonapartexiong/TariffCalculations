"""
Calculation logging with pluggable backends.

Default backend is "stdout" (free, zero infrastructure). Optional backends:
  - file:     append JSON lines to a local file (free)
  - supabase: persist to Supabase (opt-in; requires the supabase package)
  - none:     disable persistence entirely

Removing the hard dependency on Supabase keeps the default deployment free to
host and lets the API run with no database at all.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from backend.config import MAX_DESCRIPTION_LENGTH, logger


class CalculationLogger:
    """Logs tariff calculations to a configurable backend."""

    def __init__(
        self,
        backend: Optional[str] = None,
        file_path: Optional[str] = None,
        supabase_client=None,
    ):
        backend = (backend or os.getenv("LOGGING_BACKEND", "stdout")).strip().lower()
        self.file_path = file_path or os.getenv(
            "LOGGING_FILE_PATH", "calculations.jsonl"
        )
        self.supabase = supabase_client
        self.failure_count = 0
        self.max_failures = 10

        if backend not in ("stdout", "file", "supabase", "none"):
            logger.warning(
                "Unknown LOGGING_BACKEND=%r — defaulting to stdout", backend
            )
            backend = "stdout"
        self.backend = backend

    def log(self, description: str, value: float, request_id: str) -> bool:
        """Persist a calculation record. Returns True on success."""
        record = {
            "description": description[:MAX_DESCRIPTION_LENGTH],
            "value": float(value),
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            if self.backend == "stdout":
                logger.info("calculation %s", json.dumps(record))
                self.failure_count = 0
                return True

            if self.backend == "none":
                return True

            if self.backend == "file":
                with open(self.file_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
                self.failure_count = 0
                return True

            if self.backend == "supabase":
                if self.supabase is None:
                    logger.warning(
                        "Supabase logging requested but no client is available",
                    )
                    return False
                response = self.supabase.table("Calculations").insert(record).execute()
                if not response.data:
                    raise RuntimeError("No data returned from Supabase insert")
                self.failure_count = 0
                return True

            return False

        except Exception as exc:
            self.failure_count += 1
            logger.error(
                "Failed to log calculation (failure #%d): %s",
                self.failure_count,
                exc,
            )
            if self.failure_count >= self.max_failures:
                logger.critical(
                    "Calculation logging has failed %d consecutive times",
                    self.failure_count,
                )
            return False
