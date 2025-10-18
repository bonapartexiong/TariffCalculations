"""
Tariff Calculation API
Provides customs duty calculations based on product descriptions
"""

import os
import sys
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
import logging.config
from datetime import datetime

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer
from supabase import create_client, Client
from dotenv import load_dotenv
import uuid

# Load environment variables FIRST
load_dotenv()

# Constants
MPF_RATE = 0.003464  # Merchandise Processing Fee rate (0.3464%)
MPF_CAP = 575.00  # Maximum MPF in dollars
HMF_RATE = 0.00125  # Harbor Maintenance Fee rate (0.125%)
MIN_VALUE = 0.01
MAX_VALUE = 100000000.00  # $100M reasonable maximum
MAX_DESCRIPTION_LENGTH = 500
MIN_DESCRIPTION_LENGTH = 3
SIMILARITY_THRESHOLD = 0.3  # Minimum confidence for product match
API_VERSION = "v1"

# Configure structured logging
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        },
        'json': {
            'format': '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'stream': 'ext://sys.stdout'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console']
    }
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


# Custom Exceptions
class TariffCalculationError(Exception):
    """Base exception for tariff calculations"""
    pass


class ProductMatchError(TariffCalculationError):
    """Could not match product description"""
    pass


class ValidationError(TariffCalculationError):
    """Input validation failed"""
    pass


class DatabaseError(TariffCalculationError):
    """Database operation failed"""
    pass


# Data Classes
@dataclass
class ProductMatch:
    """Represents a matched product from tariff database"""
    description: str
    tariff_rate: float
    confidence: float
    index: int


@dataclass
class FeeBreakdown:
    """Breakdown of all fees and duties"""
    duty: float
    merchandise_processing_fee: float
    harbor_maintenance_fee: float
    subtotal: float
    tariff_rate: float
    matched_description: str
    confidence: float


# Services
class TariffDataService:
    """Handles tariff data loading and management"""
    
    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        
    def load_data(self) -> None:
        """Load and validate tariff data from Excel file"""
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            excel_path = os.path.join(current_dir, 'tariffs.xlsx')
            
            if not os.path.exists(excel_path):
                raise FileNotFoundError(f"Tariff file not found at {excel_path}")
            
            # Load Excel file
            self.df = pd.read_excel(excel_path)
            
            # Validate required columns
            required_columns = ['Description', 'Tariff']
            missing_columns = [col for col in required_columns if col not in self.df.columns]
            if missing_columns:
                raise ValueError(f"Missing required columns: {missing_columns}")
            
            # Validate data quality
            if self.df.empty:
                raise ValueError("Tariff data is empty")
            
            if self.df['Description'].isna().any():
                logger.warning("Found null descriptions, removing them")
                self.df = self.df.dropna(subset=['Description'])
            
            if self.df['Tariff'].isna().any():
                raise ValueError("Found null tariff rates")
            
            # Validate tariff rates are reasonable
            if (self.df['Tariff'] < 0).any() or (self.df['Tariff'] > 1).any():
                raise ValueError("Tariff rates must be between 0 and 1")
            
            # Initialize TF-IDF vectorizer
            self.vectorizer = TfidfVectorizer(
                stop_words='english',
                max_features=5000,
                ngram_range=(1, 2)
            )
            self.tfidf_matrix = self.vectorizer.fit_transform(self.df['Description'])
            
            logger.info(f"Successfully loaded {len(self.df)} tariff records")
            
        except Exception as e:
            logger.error(f"Failed to load tariff data: {str(e)}")
            raise
    
    def find_match(self, description: str) -> ProductMatch:
        """Find best matching product from tariff database"""
        if self.vectorizer is None or self.tfidf_matrix is None:
            raise ValueError("Tariff data not loaded")
        
        try:
            # Transform input description
            input_vector = self.vectorizer.transform([description])
            
            # Calculate similarities
            similarities = cosine_similarity(input_vector, self.tfidf_matrix)
            best_match_idx = similarities.argmax()
            confidence = float(similarities[0, best_match_idx])
            
            # Check confidence threshold
            if confidence < SIMILARITY_THRESHOLD:
                raise ProductMatchError(
                    f"Low confidence match (confidence: {confidence:.2f}). "
                    f"Please provide a more specific product description."
                )
            
            # Get matched product
            matched_row = self.df.iloc[best_match_idx]
            
            return ProductMatch(
                description=matched_row['Description'],
                tariff_rate=float(matched_row['Tariff']),
                confidence=confidence,
                index=best_match_idx
            )
            
        except ProductMatchError:
            raise
        except Exception as e:
            logger.error(f"Error finding product match: {str(e)}")
            raise ProductMatchError(f"Unable to match product: {str(e)}")


class FeeCalculator:
    """Calculates customs duties and fees"""
    
    @staticmethod
    def calculate_fees(value: float, tariff_rate: float, matched_description: str, confidence: float) -> FeeBreakdown:
        """Calculate all fees and duties"""
        try:
            # Calculate custom duty
            duty = value * tariff_rate
            
            # Calculate Merchandise Processing Fee (MPF)
            mpf = min(value * MPF_RATE, MPF_CAP)
            
            # Calculate Harbor Maintenance Fee (HMF)
            hmf = value * HMF_RATE
            
            # Calculate subtotal
            subtotal = duty + mpf + hmf
            
            return FeeBreakdown(
                duty=round(duty, 2),
                merchandise_processing_fee=round(mpf, 2),
                harbor_maintenance_fee=round(hmf, 2),
                subtotal=round(subtotal, 2),
                tariff_rate=tariff_rate,
                matched_description=matched_description,
                confidence=confidence
            )
            
        except Exception as e:
            logger.error(f"Error calculating fees: {str(e)}")
            raise TariffCalculationError(f"Fee calculation failed: {str(e)}")


class CalculationLogger:
    """Handles logging calculations to Supabase"""
    
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.failure_count = 0
        self.max_failures = 10
    
    def log(self, description: str, value: float, request_id: str) -> bool:
        """Log calculation to Supabase with error handling"""
        try:
            data = {
                "description": description[:MAX_DESCRIPTION_LENGTH],  # Truncate if needed
                "value": float(value),
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            response = self.supabase.table('Calculations').insert(data).execute()
            
            if not response.data:
                raise DatabaseError("No data returned from Supabase")
            
            # Reset failure count on success
            self.failure_count = 0
            logger.info(f"Successfully logged calculation for request {request_id}")
            return True
            
        except Exception as e:
            self.failure_count += 1
            logger.error(f"Failed to log to Supabase (failure #{self.failure_count}): {str(e)}")
            
            # Alert if too many failures
            if self.failure_count >= self.max_failures:
                logger.critical(f"Database logging has failed {self.failure_count} times!")
            
            return False


class InputValidator:
    """Validates API inputs"""
    
    @staticmethod
    def validate_calculation_input(data: Dict) -> Tuple[str, float]:
        """Validate input for tariff calculation"""
        if not data:
            raise ValidationError("Request body is empty")
        
        # Validate description
        if 'description' not in data:
            raise ValidationError("Missing required field: description")
        
        description = str(data['description']).strip()
        
        if len(description) < MIN_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Description too short (minimum {MIN_DESCRIPTION_LENGTH} characters)"
            )
        
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Description too long (maximum {MAX_DESCRIPTION_LENGTH} characters)"
            )
        
        # Basic sanitization
        if any(char in description for char in ['<', '>', '{', '}', ';']):
            raise ValidationError("Description contains invalid characters")
        
        # Validate value
        if 'value' not in data:
            raise ValidationError("Missing required field: value")
        
        try:
            value = float(data['value'])
        except (TypeError, ValueError):
            raise ValidationError("Value must be a valid number")
        
        if value < MIN_VALUE:
            raise ValidationError(f"Value must be at least ${MIN_VALUE}")
        
        if value > MAX_VALUE:
            raise ValidationError(f"Value cannot exceed ${MAX_VALUE:,.2f}")
        
        return description, value


# Initialize Flask app
def create_app() -> Flask:
    """Application factory"""
    app = Flask(__name__)
    
    # Configure CORS with specific origins
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    CORS(app, origins=allowed_origins)
    
    # Configure rate limiting
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["100 per hour", "20 per minute"],
        storage_uri="memory://"
    )
    
    # Railway specific configuration
    if os.getenv("RAILWAY_ENVIRONMENT"):
        app.config['PROPAGATE_EXCEPTIONS'] = True
        logger.info("Running in Railway environment")
    
    return app, limiter


app, limiter = create_app()

# Initialize Supabase client
def initialize_supabase() -> Client:
    """Initialize and validate Supabase connection"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment")
    
    # Validate URL format
    if not supabase_url.startswith("https://"):
        raise ValueError("SUPABASE_URL must be a valid HTTPS URL")
    
    try:
        client = create_client(
            supabase_url=supabase_url,
            supabase_key=supabase_key
        )
        
        # Test connection
        test_query = client.table('Calculations').select("*").limit(1).execute()
        logger.info("Supabase connection successful")
        
        return client
        
    except Exception as e:
        logger.error(f"Supabase initialization failed: {str(e)}")
        raise


# Initialize services
try:
    supabase_client = initialize_supabase()
    tariff_service = TariffDataService()
    tariff_service.load_data()
    fee_calculator = FeeCalculator()
    calculation_logger = CalculationLogger(supabase_client)
    input_validator = InputValidator()
    logger.info("All services initialized successfully")
except Exception as e:
    logger.critical(f"Failed to initialize services: {str(e)}")
    sys.exit(1)


# Middleware
@app.before_request
def before_request():
    """Add request ID for tracking"""
    g.request_id = str(uuid.uuid4())
    logger.info(f"Request {g.request_id}: {request.method} {request.path}")


@app.after_request
def after_request(response):
    """Add security headers and request ID"""
    response.headers['X-Request-ID'] = g.request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# Routes
@app.route(f'/{API_VERSION}/health', methods=['GET'])
def health_check():
    """Comprehensive health check"""
    try:
        health_status = {
            "status": "healthy",
            "version": API_VERSION,
            "timestamp": datetime.utcnow().isoformat(),
            "checks": {
                "tariff_data": tariff_service.df is not None,
                "ml_model": tariff_service.tfidf_matrix is not None,
                "supabase": False
            }
        }
        
        # Test Supabase connection
        try:
            supabase_client.table('Calculations').select("*").limit(1).execute()
            health_status["checks"]["supabase"] = True
        except Exception as e:
            logger.error(f"Supabase health check failed: {str(e)}")
        
        # Determine overall health
        all_healthy = all(health_status["checks"].values())
        status_code = 200 if all_healthy else 503
        
        if not all_healthy:
            health_status["status"] = "degraded"
        
        return jsonify(health_status), status_code
        
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@app.route(f'/{API_VERSION}/calculate', methods=['POST'])
@limiter.limit("10 per minute")
def calculate_duty():
    """Calculate customs duty and fees"""
    try:
        # Validate request
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        # Validate input
        description, value = input_validator.validate_calculation_input(request.json)
        
        # Find matching product
        try:
            match = tariff_service.find_match(description)
        except ProductMatchError as e:
            return jsonify({
                "error": str(e),
                "suggestion": "Try using more specific product details (material, use, category)"
            }), 404
        
        # Calculate fees
        fees = fee_calculator.calculate_fees(
            value=value,
            tariff_rate=match.tariff_rate,
            matched_description=match.description,
            confidence=match.confidence
        )
        
        # Log to Supabase (non-blocking)
        calculation_logger.log(description, value, g.request_id)
        
        # Prepare response
        response_data = {
            'matched_description': fees.matched_description,
            'confidence': round(fees.confidence, 3),
            'tariff': fees.tariff_rate,
            'duty': fees.duty,
            'merchandise_processing_fee': fees.merchandise_processing_fee,
            'harbor_maintenance_fee': fees.harbor_maintenance_fee,
            'subtotal': fees.subtotal,
            'footnote': "For precise tariff quotations and formal consultations, "
                       "reach out to Bo Xiong at BonaparteXiongBo@gmail.com.",
            'request_id': g.request_id
        }
        
        logger.info(f"Request {g.request_id}: Calculation successful (confidence: {fees.confidence:.2f})")
        return jsonify(response_data), 200
        
    except ValidationError as e:
        logger.warning(f"Request {g.request_id}: Validation error - {str(e)}")
        return jsonify({"error": str(e)}), 400
        
    except ProductMatchError as e:
        logger.warning(f"Request {g.request_id}: Match error - {str(e)}")
        return jsonify({"error": str(e)}), 404
        
    except TariffCalculationError as e:
        logger.error(f"Request {g.request_id}: Calculation error - {str(e)}")
        return jsonify({"error": "Unable to calculate tariff"}), 500
        
    except Exception as e:
        logger.exception(f"Request {g.request_id}: Unexpected error - {str(e)}")
        return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded"""
    return jsonify({
        "error": "Rate limit exceeded",
        "message": "Too many requests. Please try again later."
    }), 429


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors"""
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    """Handle 500 errors"""
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


# Main entry point
if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    
    if debug:
        logger.warning("Running in DEBUG mode - DO NOT use in production!")
    
    # For production, use Gunicorn instead: gunicorn -w 4 -b 0.0.0.0:5000 app:app
    app.run(host='0.0.0.0', port=port, debug=debug)
