from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()  # Load environment variables

app = Flask(__name__)
CORS(app)

# Supabase configuration with validation
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase URL and Key must be set in .env")

try:
    # Initialize Supabase client with connection test
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    # Test connection by making a simple query
    test_query = supabase.table('Calculations').select("*").limit(1).execute()
    logger.info("Supabase connection successful")
except Exception as e:
    logger.error(f"Failed to initialize Supabase client: {str(e)}")
    raise

# Load tariff data
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(current_dir, 'tariffs.xlsx')
    df = pd.read_excel(excel_path) 
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(df['Description'])
except Exception as e:
    logger.error(f"Failed to load or process tariff data: {str(e)}")
    raise

def log_to_supabase(description: str, value: float):
    """Log calculation attempt to Supabase with improved error handling"""
    try:
        # Validate input
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Invalid description")
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError("Invalid value")

        # Prepare data
        data = {
            "description": description.strip(),
            "value": float(value)
        }
        
        logger.info(f"Attempting to log to Supabase: {data}")
        
        # Insert with error handling
        response = supabase.table('Calculations').insert(data).execute()
        
        if not response.data:
            raise Exception("No data returned from Supabase")
        
        logger.info(f"Successfully logged to Supabase: {response.data}")
        return True
        
    except ValueError as e:
        logger.error(f"Validation error in log_to_supabase: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Failed to log to Supabase: {str(e)}")
        raise

@app.route('/calculate', methods=['POST'])
def calculate_duty():
    try:
        # Validate request data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.json
        if not data or 'description' not in data or 'value' not in data:
            return jsonify({"error": "Missing required fields"}), 400

        input_desc = str(data['description']).strip()
        try:
            value = float(data['value'])
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid value format"}), 400

        # Log to Supabase
        try:
            log_to_supabase(input_desc, value)
        except Exception as e:
            logger.error(f"Supabase logging failed: {str(e)}")
            # Continue processing even if logging fails
        
        # Calculate duty
        input_vector = vectorizer.transform([input_desc])
        similarities = cosine_similarity(input_vector, tfidf_matrix)
        best_match_idx = similarities.argmax()
        
        tariff = df.iloc[best_match_idx]['Tariff']
        duty = value * tariff
        
        # Calculate Merchandise Processing Fee (MPF)
        mpf = min(value * 0.003464, 575.00)  # 0.3464% capped at $575
        
        # Calculate Harbor Maintenance Fee (HMF)
        hmf = value * 0.00125  # 0.125%
        
        # Calculate subtotal
        subtotal = duty + mpf + hmf
        
        return jsonify({
            'matched_description': df.iloc[best_match_idx]['Description'],
            'tariff': tariff,
            'duty': duty,
            'merchandise_processing_fee': mpf,
            'harbor_maintenance_fee': hmf,
            'subtotal': subtotal,
            'footnote': "For precise tariff quotation and formal consultations, reach out to Bo Xiong at BonaparteXiongBo@gmail.com."
        })

    except Exception as e:
        logger.error(f"Error in calculate_duty: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    app.run(debug=False)