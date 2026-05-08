"""
Application configuration and constants.
"""

from decimal import Decimal
import logging.config

# API version
API_VERSION = "v1"

# Fee rates — stored as Decimal for monetary precision
MPF_RATE = Decimal("0.003464")  # Merchandise Processing Fee rate (0.3464%)
MPF_CAP = Decimal("575.00")     # Maximum MPF in dollars
HMF_RATE = Decimal("0.00125")   # Harbor Maintenance Fee rate (0.125%)

# Validation limits
MIN_VALUE = Decimal("0.01")
MAX_VALUE = Decimal("100000000.00")  # $100M reasonable maximum
MAX_DESCRIPTION_LENGTH = 500
MIN_DESCRIPTION_LENGTH = 3

# Matching
SIMILARITY_THRESHOLD = 0.3  # Minimum confidence for product match

# Rate limiting
RATE_LIMIT_HOURLY = "100 per hour"
RATE_LIMIT_MINUTELY = "20 per minute"
RATE_LIMIT_CALCULATE = "10 per minute"

# Request size limit (1 MB)
MAX_CONTENT_LENGTH = 1 * 1024 * 1024

# Structured logging configuration
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
        "json": {
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)
