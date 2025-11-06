import os
import sys
import json
import logging
import uuid
import re
import copy
from urllib.parse import urlparse, parse_qs
import requests

# Google API/Auth Imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import HttpError
from google.auth.transport.requests import Request
# ADC/Cloud Imports
from google.cloud import documentai
from google.cloud import storage
import google.auth

# UI/Utility Imports
from rapidfuzz import fuzz

# --- Configuration Constants ---
SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive.readonly'
]
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
REDIRECT_URI = 'http://localhost:8080'
GCP_PROJECT_ID = "bacchanal-dev"
GCS_BUCKET_NAME = "whats-up-doc"
DOC_AI_PROCESSOR_ID = "16dc0f104808a6b6"
KEYWORD_CONFIDENCE_THRESHOLD = 0.98

# --- Global Data and Keywords ---
GLOBAL_METADATA = {
    'SlideID_Object': {},
    'Table_Data': [],
    'Keyword_Values': []
}
FIN_KEYWORDS = {
    'Contra Revenue': 'Contra',
    'FoF $': 'FoF $',
    'FoF %': 'FoF %',
    'FvA $': 'FvA $',
    'FvA %': 'FvA %',
    'Gross Revenue $': 'Gross Rev $',
    'Gross Margin $': 'GM $',
    'Gross Profit $': 'GP $',
    'Net Revenue $': 'Net Rev $',
    'Gross Revenue %': 'Gross Rev %',
    'Gross Margin %': 'GM %',
    'Gross Profit %': 'GP %',
    'Net Revenue %': 'Net Rev %',
    'MoM $': 'M/M $',
    'MoQ': 'Q/Q',
    'YoY': 'Y/Y',
    'Gross Margin': 'GM',
    'Gross Profit': 'GP',
    'Gross Revenue': 'Gross Rev',
    'Net Revenue': 'Net Rev',
}

# --- Logging Setup (Console Output Included) ---
LOG_FILENAME = f"slides_validator_debug_{uuid.uuid4().hex[:8]}.log"
file_handler = logging.FileHandler(LOG_FILENAME, mode='w', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.handlers = []
logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.info(f"Logging configured. Console output enabled. File: {LOG_FILENAME}")

# --- Authentication Functions (FINAL ADC FIX) ---
def get_user_service():
    """Authenticates the user for Slides/Drive APIs."""
    logging.debug("Starting User OAuth service initialization.")
    creds = None
    if os.path.exists(TOKEN_FILE): creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try: creds.refresh(Request()); logging.debug("User creds refreshed successfully.")
        except Exception: creds = None
    if not creds or not creds.valid:
        logging.debug("Manual user authentication required.")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES); flow.redirect_uri = REDIRECT_URI
            auth_url, _ = flow.authorization_url(prompt='consent'); print("\n---------------------- AUTH REQUIRED -----------------------"); print(auth_url)
            full_redirect_url = input("\n3. Paste the FULL URL from the browser here: ")
            query = parse_qs(urlparse(full_redirect_url).query); verification_code = query.get('code', [None])[0]
            if not verification_code: raise Exception("Could not find the 'code' parameter in the URL.")
            flow.fetch_token(code=verification_code); creds = flow.credentials
            with open(TOKEN_FILE, 'w') as token: token.write(creds.to_json())
            logging.info(f"User token saved successfully.")
        except FileNotFoundError:
            logging.error(f"'{CREDENTIALS_FILE}' not found. Cannot authenticate."); return None, None
        except Exception as e:
            logging.error(f"User Authentication failed: {e}"); return None, None
    try:
        slides_service = build('slides', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        logging.info(f"User Services (Slides/Drive) built successfully.")
        return slides_service, drive_service
    except Exception as e:
        logging.error(f"Failed to build user services: {e}"); return None, None

def get_document_ai_client():
    """Authenticates and returns the Document AI and Storage clients using Application Default Credentials (ADC)."""
    logging.debug("Starting Document AI client initialization (using ADC).")
    try:
        docai_client = documentai.DocumentProcessorServiceClient()
        storage_client = storage.Client(project=GCP_PROJECT_ID)
        logging.info("Document AI/Storage clients built successfully via ADC. ")
        return docai_client, storage_client
    except Exception as e:
        logging.error(f"Document AI Client setup failed via ADC. Ensure Vision/DocAI API is enabled and your user has IAM roles. Error: {e}")
        return None, None

def get_service():
    """Main function to initialize all four required service clients."""
    logging.info("--- STARTING API CLIENT INITIALIZATION ---")
    slides_service, drive_service = get_user_service()
    docai_client, storage_client = get_document_ai_client()
    if not all([slides_service, drive_service, docai_client, storage_client]):
        logging.error("Failed to initialize one or more required API clients."); return None, None, None, None
    logging.info("--- ALL API CLIENTS INITIALIZED SUCCESSFULLY ---")
    return slides_service, drive_service, docai_client, storage_client

# --- All other backend functions from the original script are here ---
# (This includes find_destination_files, get_slide_page_elements,
# find_master_match, process_image_ocr, and all other helpers from the original script)

# --- Main Execution Function (FULL DEFINITION IN BLOCK 2) ---
def run_back_end(master_url, dest_id_or_url, table_format, status_output):
    # This is the full, original run_back_end function.
    pass
