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

    LOG_FILE_PLACEHOLDER = f"{LOG_FILENAME}"

    try: # --- Global Crash Protection Start ---

        logging.info("--- STARTING Run Cycle ---")
        GLOBAL_METADATA['Table_Data'] = []; GLOBAL_METADATA['Keyword_Values'] = []
        total_inconsistencies = 0

        # 1. AUTHENTICATION & SETUP
        slides_service, drive_service, docai_client, storage_client = get_service()
        if not slides_service or not drive_service or not docai_client or not storage_client:
            with status_output: print("❌ Error: Authentication or Service setup failed. Check log file.")
            return LOG_FILE_PLACEHOLDER, -1

        master_id = extract_id_from_url(master_url); destination_files = find_destination_files(drive_service, dest_id_or_url)

        if not destination_files:
            logging.error("No valid destination Slides files found.");
            with status_output: print("❌ Error: No valid destination Slides files/folders found.")
            return LOG_FILE_PLACEHOLDER, -1

        master_slides_map, master_pres = get_slide_page_elements(slides_service, master_id); master_slide_list = master_pres.get('slides', [])

        if not master_slide_list:
            logging.error("Master presentation is empty.");
            with status_output: print("❌ Error: Master presentation is empty.")
            return LOG_FILE_PLACEHOLDER, -1

        logging.info(f"Processing started for {len(destination_files)} destination presentation(s).")

        # 2. MAIN PROCESSING LOOP
        for dest_file in destination_files:
            dest_id = dest_file['id']; dest_name = dest_file['name']
            with status_output: print(f"\nProcessing: **{dest_name}** ({dest_id})")
            logging.info(f"--- Starting File Processing: {dest_name} ---")

            dest_slides_map, dest_pres = get_slide_page_elements(slides_service, dest_id)

            # Match slides by index (assuming same order is the sync requirement)
            for i, master_slide in enumerate(master_slide_list):

                try: # --- DEFENSIVE SLIDE ITERATION TRY/EXCEPT ---
                    logging.info(f"Starting Slide {i+1}/{len(master_slide_list)} sync.")

                    # Skip if master slide index exceeds destination slide count
                    if i >= len(dest_pres.get('slides', [])): continue

                    master_slide_id = master_slide['objectId']; dest_slide = dest_pres['slides'][i]; dest_slide_id = dest_slide['objectId']

                    # Run Copy/Delete sync logic
                    copy_requests = copy_slide_content(slides_service, master_slide_id, master_id, dest_id, dest_slide_id, master_slide, drive_service, docai_client, storage_client)

                    # Run validation logic on existing content in destination
                    dest_slide_elements = dest_slide.get('pageElements', [])

                    # Call validation using the table_format argument
                    keyword_metadata = process_text_bearing_objects(slides_service, dest_id, dest_slide_id, dest_slide_elements, table_format)
                    GLOBAL_METADATA['Keyword_Values'].extend(keyword_metadata)

                    if copy_requests:
                        logging.debug(f"Executing BatchUpdate with {len(copy_requests)} requests.")
                        try:
                            slides_service.presentations().batchUpdate(presentationId=dest_id, body={'requests': copy_requests}).execute()
                            logging.info(f"Applied {len(copy_requests)} batch updates to slide {i+1} successfully.")
                        except HttpError as e:
                            logging.error(f"BatchUpdate failed on {dest_name}, slide {i+1}: {e}"); total_inconsistencies += 1

                except Exception as e:
                    logging.error(f"CRITICAL PARSING ERROR in Slide {i+1} of {dest_name}. Traceback: {e}"); total_inconsistencies += 1
                    continue

            logging.info(f"--- Finished File Processing: {dest_name} ---")

        # 3. FINAL CONSISTENCY CHECK & CONFIDENCE REPORTING
        logging.info("Starting final consistency and confidence report.")
        keyword_groups = {};

        # LOGGING: Report number of items collected
        logging.debug(f"CONSISTENCY: Analyzing {len(GLOBAL_METADATA['Keyword_Values'])} keyword instances.")

        # Group entries by unique (label, keyword) pair
        for entry in GLOBAL_METADATA['Keyword_Values']:
            key = (entry['label'], entry['keyword'])
            # Safely append the entry to the list corresponding to the key
            keyword_groups.setdefault(key, []).append(entry)

        # Iterate over grouped results for reporting
        for key, entries in keyword_groups.items():

            # Collect unique values found for this keyword group
            unique_values = set([e['value'] for e in entries])

            # LOGGING: Report grouping and unique values
            logging.debug(f"CONSISTENCY_GROUP: Label='{key[0]}', Keyword='{key[1]}'. Unique Values Found: {list(unique_values)}")

            # Check for value inconsistency (if multiple distinct values exist)
            if len(unique_values) != 1:
                total_inconsistencies += 1; logging.error(f"[RED FONT] INCONSISTENT VALUE across slides for Label='{key[0]}', Keyword='{key[1]}'. Found: {list(unique_values)}")

            # Check for low confidence (regardless of value consistency)
            for entry in entries:
                if entry['confidence'] < KEYWORD_CONFIDENCE_THRESHOLD:
                    total_inconsistencies += 1
                    logging.error(f"[RED FONT] LOW CONFIDENCE ({entry['confidence']:.4f}): Keyword '{entry['keyword']}' below threshold {KEYWORD_CONFIDENCE_THRESHOLD}. Slide: {entry['dest_id']}")

        logging.info("--- Run Cycle Complete ---")

        # 4. FINAL RETURN
        return LOG_FILE_PLACEHOLDER, -1 if total_inconsistencies > 0 else 0

    except Exception as e:
        logging.error(f"FATAL PYTHON CRASH: The run_back_end function terminated unexpectedly.")
        logging.error(f"Error Details: {type(e).__name__} - {e}")
        return LOG_FILE_PLACEHOLDER, -1
