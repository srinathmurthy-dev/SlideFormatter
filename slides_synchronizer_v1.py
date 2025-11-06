import os
import sys
import json
import logging
import uuid
import re
import copy # Added for mutable deep copy of API objects
from io import StringIO, BytesIO
from urllib.parse import urlparse, parse_qs
import io
import requests # Added for fetching image bytes from the Slides API thumbnail URL

# Google API/Auth Imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import HttpError, MediaIoBaseDownload
from google.auth.transport.requests import Request
# ADC/Cloud Imports
from google.cloud import documentai
from google.cloud import storage
import google.auth

# UI/Utility Imports
from IPython.display import display, HTML
import ipywidgets as widgets
from ipywidgets import Layout
from rapidfuzz import fuzz

# --- Configuration Constants ---
# User OAuth (Slides/Drive)
SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive.readonly'
]
CREDENTIALS_FILE = 'credentials.json' # User OAuth Client ID JSON
TOKEN_FILE = 'token.json'             # User Token Cache
REDIRECT_URI = 'http://localhost:8080'

# Document AI/GCS (ADC Configuration)
# <<< IMPORTANT: UPDATED CONSTANTS >>>
GCP_PROJECT_ID = "bacchanal-dev"
GCS_BUCKET_NAME = "whats-up-doc"
DOC_AI_PROCESSOR_ID = "16dc0f104808a6b6"
# <<< END IMPORTANT UPDATE >>>

# --- Validation Constants ---
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

# Create handlers
file_handler = logging.FileHandler(LOG_FILENAME, mode='w', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)

# Set format for both handlers
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Configure the root logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG) # Capture all levels
logger.handlers = [] # Clear existing handlers if any
logger.addHandler(file_handler)
logger.addHandler(console_handler) # Add console output

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

    if creds and creds.expired and creds.refresh_token:
        try: creds.refresh(Request()); logging.debug("User creds refreshed before final build.")
        except Exception as e: logging.error(f"Failed final user refresh: {e}"); return None, None

    try:
        slides_service = build('slides', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Failed to build user services: {e}"); return None, None

    logging.info(f"User Services (Slides/Drive) built successfully.")
    return slides_service, drive_service

def get_document_ai_client():
    """Authenticates and returns the Document AI and Storage clients using Application Default Credentials (ADC)."""
    logging.debug("Starting Document AI client initialization (using ADC).")

    try:
        # ADC automatically finds credentials from the environment (user session)
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

    if not slides_service or not drive_service or not docai_client or not storage_client:
        logging.error("Failed to initialize one or more required API clients."); return None, None, None, None

    logging.info("--- ALL API CLIENTS INITIALIZED SUCCESSFULLY ---")
    return slides_service, drive_service, docai_client, storage_client
# --- Utility Functions ---

def extract_id_from_url(url_or_id):
    parts = url_or_id.split('/');
    if 'd' in parts: return parts[parts.index('d') + 1]
    if 'folder' in parts: return parts[parts.index('folder') + 1]
    return url_or_id

def find_destination_files(drive_service, destination_id_or_url):
    logging.debug(f"Finding destination file/folder for: {destination_id_or_url}")
    file_id = extract_id_from_url(destination_id_or_url)
    try:
        file_meta = drive_service.files().get(fileId=file_id, fields='mimeType, name, id').execute()

        if file_meta['mimeType'] == 'application/vnd.google-apps.folder':
            query = f"'{file_id}' in parents and mimeType='application/vnd.google-apps.presentation'"; results = drive_service.files().list(q=query, fields='files(id, name)').execute()
            logging.info(f"Found {len(results.get('files', []))} presentation(s) in destination folder.")
            return [{'id': f['id'], 'name': f['name']} for f in results['files']]

        elif file_meta['mimeType'] == 'application/vnd.google-apps.presentation':
            logging.info(f"Destination is single file: {file_meta['name']}.")
            return [{'id': file_id, 'name': file_meta['name']}]

        else:
            logging.error(f"Destination ID {file_id} is not a Slides file or Folder."); return []

    except HttpError as e:
        logging.error(f"Drive API Error: Could not access destination ID {file_id}. {e}"); return []

def get_slide_page_elements(slides_service, presentation_id):
    logging.debug(f"Fetching page elements for presentation ID: {presentation_id}")
    try:
        presentation = slides_service.presentations().get(
            presentationId=presentation_id,
            fields='title,slides,masters,layouts'
        ).execute()
        logging.debug(f"Successfully retrieved {len(presentation.get('slides', []))} slides.")
        return {slide['objectId']: slide for slide in presentation.get('slides', {})}, presentation
    except HttpError as e:
        logging.error(f"Slides API Error reading presentation {presentation_id}: {e}"); return {}, {}

# --- Helper to get element metrics (Width, Height, X, Y) for matching ---
def find_master_match(element1, element2_list):
    """Checks if element1 exists within element2_list based on size and position (transform).
       Returns the matching destination objectId if found, otherwise None.
    """

    # Helper: Extracts size from 'size' and position (translation) from 'transform'
    def get_element_metrics(element):
        # 1. Get width and height from the 'size' key (uses 'magnitude' as the value in PT)
        size = element.get('size', {})
        width = size.get('width', {}).get('magnitude', 0)
        height = size.get('height', {}).get('magnitude', 0)

        # 2. Get X and Y position (translation) from the 'transform' key
        transform = element.get('transform', {})
        x_pos = transform.get('translateX', 0)
        y_pos = transform.get('translateY', 0)

        # Rounding for reliable comparison
        return (round(width, 3), round(height, 3), round(x_pos, 3), round(y_pos, 3))

    w1, h1, x1, y1 = get_element_metrics(element1)

    logging.debug(f"MATCH: Comparing Master ({element1.get('objectId')}) DIMS: W={w1}, H={h1}, X={x1}, Y={y1}")

    # Skip matching check for placeholder elements (e.g., slide titles, footers)
    if w1 == 0 and h1 == 0 and not element1.get('shape'): return True

    for element2 in element2_list:
        w2, h2, x2, y2 = get_element_metrics(element2)

        logging.debug(f"MATCH:    vs Dest ({element2.get('objectId')}) DIMS: W={w2}, H={h2}, X={x2}, Y={y2}")

        # A match is found if position and size are identical
        if w1 == w2 and h1 == h2 and x1 == x2 and y1 == y2:
            logging.debug(f"MATCH:    *** EXACT MATCH FOUND *** between Master {element1.get('objectId')} and Dest {element2.get('objectId')}")
            return element2.get('objectId') # <-- RETURN THE DESTINATION OBJECT ID

    return None

# --- Document AI Processing ---

def process_image_ocr(image_element, drive_service, docai_client, storage_client, slides_service, dest_pres_id): # Signature updated
    logging.debug("OCR_START: Starting Document AI OCR processing for image element.")
    temp_filename = f"temp_image_{uuid.uuid4().hex[:8]}.png"
    extracted_text = ""
    blob = None

    try:
        # CRITICAL DEBUGGING PRINTS
        logging.debug(f"OCR_DEBUG: Full Element Keys: {list(image_element.keys())}")
        logging.debug(f"OCR_DEBUG: Element ID (objectId): {image_element.get('objectId')}")
        logging.debug(f"OCR_DEBUG: Attempting to access parentObjectId: {image_element.get('parentObjectId')}")

        # 1. Download image data using the reliable Slides API thumbnail method
        image_obj_id = image_element['objectId']
        logging.debug(f"OCR_STEP: Requesting image thumbnail for object ID: {image_obj_id} in Presentation ID: {dest_pres_id}")

        # Get the credentials for authenticated request
        creds = slides_service._http.credentials

        # Request the thumbnail URL for the specific image element within the presentation
        response = slides_service.presentations().pages().getThumbnail(
            presentationId=dest_pres_id,
            pageObjectId=image_element.get('parentObjectId', image_obj_id), # Use safe access
            elementId=image_obj_id,
            thumbnailProperties={'thumbnailSize': 'LARGE', 'mimeType': 'PNG'}
        ).execute()

        thumbnail_url = response.get('contentUrl')
        logging.debug(f"OCR_STEP: Received thumbnail URL: {thumbnail_url[:80]}...")

        # The thumbnail URL is a pre-signed URL and should be fetched directly without extra headers.
        response = requests.get(thumbnail_url)

        if response.status_code == 200:
            image_bytes = response.content
            logging.debug(f"OCR_STEP: Successfully downloaded image bytes from pre-signed URL.")
        else:
            # If the request fails, log the status and the response content for debugging.
            logging.error(f"OCR_ERROR: Failed to download image from URL. Status: {response.status_code}.")
            logging.error(f"OCR_ERROR_CONTENT: {response.text}")
            return extracted_text # Return empty text if download fails

        # 2. Upload to GCS
        bucket = storage_client.bucket(GCS_BUCKET_NAME); blob = bucket.blob(temp_filename)
        blob.upload_from_string(image_bytes, content_type='image/png')
        logging.info(f"OCR_STEP: Image uploaded to GCS: {GCS_BUCKET_NAME}/{temp_filename}")

        # 3. Process via Document AI
        image = documentai.RawDocument(content=image_bytes, mime_type="image/png")
        processor_name = docai_client.processor_path(GCP_PROJECT_ID, "us", DOC_AI_PROCESSOR_ID)

        request = documentai.ProcessRequest(name=processor_name, raw_document=image)
        response = docai_client.process_document(request=request)

        if response.document:
            logging.info("OCR_SUCCESS: Document AI processed successfully.")

            # Helper function to extract text from a specific layout segment
            def get_text_from_segment(segment, full_text):
                if not segment: return ""
                return full_text[segment.start_index:segment.end_index].strip()

            full_text_content = response.document.text
            reconstructed_content = []

            for page in response.document.pages:
                # Process tables first
                for table in page.tables:
                    table_text = ""
                    # Reconstruct header
                    for header_row in table.header_rows:
                        row_cells = [get_text_from_segment(cell.layout.text_anchor, full_text_content) for cell in header_row.cells]
                        table_text += "\t".join(row_cells) + "\n"
                    # Reconstruct body
                    for body_row in table.body_rows:
                        row_cells = [get_text_from_segment(cell.layout.text_anchor, full_text_content) for cell in body_row.cells]
                        table_text += "\t".join(row_cells) + "\n"
                    reconstructed_content.append(table_text)

                # You can add similar logic here for page.paragraphs, page.form_fields etc. if needed
                # For now, we will just add the raw text of the page if no tables are found
                if not page.tables:
                    reconstructed_content.append(get_text_from_segment(page.layout.text_anchor, full_text_content))

            extracted_text = "\n\n".join(reconstructed_content).strip()

    except HttpError as e:
        logging.error(f"OCR_ERROR: Slides/Drive API error during image processing: {e}")
    except Exception as e:
        logging.error(f"OCR_ERROR: Document AI processing failed (Check GCP_PROJECT_ID/Processor_ID/Permissions). Error: {e}")
    finally:
        # 4. Cleanup GCS - Robust deletion attempt
        try:
            if blob and storage_client.bucket(GCS_BUCKET_NAME).blob(temp_filename).exists():
                blob.delete()
                logging.debug("OCR_STEP: Cleaned up GCS object.")
        except Exception as e:
            logging.warning(f"OCR_WARNING: Failed to delete GCS object: {e}")

    return extracted_text

# --- UNIT CONVERSION HELPER ---
EMU_PER_PT = 12700

def convert_emu_to_pt(magnitude, original_unit):
    """Converts EMU magnitudes to PT. Returns magnitude and target unit ('PT')."""
    # The Slides API typically returns EMU, which must be converted to PT for CREATE/UPDATE requests.
    if original_unit == 'EMU' and magnitude != 0:
        return magnitude / EMU_PER_PT, 'PT'
    # If the magnitude is 0, or unit is already PT, assume it's correct.
    return magnitude, original_unit
# ------------------------------

# --- HELPER: GENERATE GRANULAR TEXT/PARAGRAPH STYLE REQUESTS ---
def generate_style_update_requests(object_id, master_element, cell_location=None):
    """Generates detailed requests to update text and paragraph styling."""
    requests = []

    # Determine the correct text object from the master element
    text_obj = None
    if 'text' in master_element:
        text_obj = master_element['text']
    elif 'shape' in master_element and 'text' in master_element['shape']:
        text_obj = master_element['shape']['text']
    elif 'table' in master_element and cell_location:
         # For tables, we need to locate the specific cell's text property
        cell = master_element['table']['tableRows'][cell_location['rowIndex']]['tableCells'][cell_location['columnIndex']]
        if 'text' in cell:
            text_obj = cell['text']

    if not text_obj or 'textElements' not in text_obj:
        return []

    # Process each text element to generate style update requests
    for element in text_obj['textElements']:
        start_index = element.get('startIndex', 0)
        end_index = element.get('endIndex', 1)

        # 1. Update Text Style (font, color, bold, etc.)
        if 'textRun' in element and 'style' in element['textRun']:
            style = scrub_read_only_fields(element['textRun']['style'])
            if style:
                requests.append({
                    'updateTextStyle': {
                        'objectId': object_id,
                        'cellLocation': cell_location,
                        'style': style,
                        'textRange': {'type': 'FIXED_RANGE', 'startIndex': start_index, 'endIndex': end_index},
                        'fields': '*' # Use a wildcard field mask for simplicity to update all fields in the style
                    }
                })

        # 2. Update Paragraph Style (alignment, spacing, etc.)
        if 'paragraphMarker' in element and 'style' in element['paragraphMarker']:
            style = scrub_read_only_fields(element['paragraphMarker']['style'])
            if style:
                requests.append({
                    'updateParagraphStyle': {
                        'objectId': object_id,
                        'cellLocation': cell_location,
                        'style': style,
                        'textRange': {'type': 'FIXED_RANGE', 'startIndex': start_index, 'endIndex': end_index},
                        'fields': '*' # Use a wildcard field mask for simplicity
                    }
                })

    return requests
# --------------------------------------------------------

# --- SCRUBBING FUNCTION: Removes known read-only fields (AGGRESSIVE) ---
def scrub_read_only_fields(properties):
    """Recursively removes known read-only fields from a dictionary."""

    # More targeted list of fields that are truly read-only or managed by the API.
    # This allows styling and transform properties to be preserved.
    READ_ONLY_FIELDS = [
        'parentObjectId', 'propertyState', 'resolvedSize', 'resolvedTransform',
        'isPlaceholder', 'placeholderId', 'kind', 'source', 'parentTextRange',
        'content', # This is part of the textRun, but content is handled by insertText
        'text' # The 'text' object itself is complex; content is handled separately.
    ]

    if isinstance(properties, dict):
        new_properties = {}
        for key, value in properties.items():
            # Convert key to snake_case for comparison (to catch both)
            snake_key = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()

            if snake_key not in READ_ONLY_FIELDS and key not in READ_ONLY_FIELDS:
                if isinstance(value, (dict, list)):
                    new_properties[key] = scrub_read_only_fields(value)
                else:
                    new_properties[key] = value
            else:
                logging.debug(f"SCRUB: Removing read-only field: {key}")
        return new_properties
    elif isinstance(properties, list):
        return [scrub_read_only_fields(item) for item in properties]
    else:
        return properties
# -------------------------------------------------------------------------

# --- HELPER: Extracts text content from any element (Shape, Table Cell) ---
def get_text_content_from_element(element):
    """Robustly extracts all text content from an element's nested text property."""
    full_text = ""

    # 1. Check for standard text property (used in certain base API responses)
    if 'text' in element and 'textElements' in element['text']:
        text_obj = element['text']
    # 2. Check for text nested inside element types (shape, table cell, etc.)
    else:
        element_type_key = next((k for k in element.keys() if k not in ['objectId', 'size', 'transform', 'elementProperties']), None)

        if element_type_key and 'text' in element.get(element_type_key, {}):
             text_obj = element[element_type_key]['text']
        else:
             return "" # No text property found

    if 'textElements' in text_obj:
        for text_element in text_obj['textElements']:
            if 'textRun' in text_element:
                full_text += text_element['textRun'].get('content', '')
            elif 'paragraphMarker' in text_element:
                # Append newline to simulate structure breaks
                if full_text and not full_text.endswith('\n'):
                     full_text += '\n'

    return full_text.strip()
# -----------------------------------------------------------------------


# --- NEW HELPER: TABLE HEADER EXTRACTION LOGIC ---
def _get_table_headers(table_element, table_format="Format 1: Row 0/Col 0 Headers"):
    """Extracts headers based on the specified table format."""

    table_prop = table_element['table']
    rows = table_prop['rows']
    cols = table_prop['columns']
    table_rows = table_prop['tableRows']

    row_headers = []
    col_headers = []

    if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)":
        # Format 2: Combines Row 0 and Row 1 text for column header labels (e.g., 'Q3 - Actual')

        # Column Headers: Combine Row 0 and Row 1 content (skipping cell 0,0 and 1,0)
        if rows >= 2:
            row_0_cells = table_rows[0]['tableCells']
            row_1_cells = table_rows[1]['tableCells']

            for c_idx in range(1, cols):
                header_p1 = get_text_content_from_element(row_0_cells[c_idx]).split('\n')[0].strip()
                header_p2 = get_text_content_from_element(row_1_cells[c_idx]).split('\n')[0].strip()

                # The combined header is the unique identifier (keyword)
                combined_header = f"{header_p1} - {header_p2}" if header_p1 and header_p2 else header_p1 or header_p2 or f"[Empty Col Header {c_idx}]"
                col_headers.append(combined_header)

        # Row Headers: Taken from Column 0 (starting from row 2, skipping 0 and 1)
        for r_idx in range(2, rows):
            row_headers.append(get_text_content_from_element(table_rows[r_idx]['tableCells'][0]).strip() or f"[Empty Row Header {r_idx}]")

    else: # Default/Format 1: Simple Header (Row 0/Col 0 Headers)
        # Format 1: Uses Row 0 (skipping col 0) for column headers, Column 0 (skipping row 0) for row headers

        # Column Headers (Row 0, starting from column 1)
        if rows > 0:
            for c_idx in range(1, cols):
                col_headers.append(get_text_content_from_element(table_rows[0]['tableCells'][c_idx]).strip() or f"[Empty Col Header {c_idx}]")

        # Row Headers (Column 0, starting from row 1)
        for r_idx in range(1, rows):
            row_headers.append(get_text_content_from_element(table_rows[r_idx]['tableCells'][0]).strip() or f"[Empty Row Header {r_idx}]")

    return row_headers, col_headers

# --- NEW HELPER: TABLE DATA EXTRACTION FOR DEBUGGING ---
def extract_table_data_for_debug(table_element, dest_slide_id, table_format="Format 1: Row 0/Col 0 Headers"):
    """Extracts table data and logs the header intersection for every data cell."""

    table_prop = table_element['table']
    rows = table_prop['rows']
    cols = table_prop['columns']
    table_rows = table_prop['tableRows']
    table_id = table_element['objectId']

    row_headers, col_headers = _get_table_headers(table_element, table_format)

    logging.debug(f"TABLE_ANALYSIS: Slide ID: {dest_slide_id}. Starting analysis for Table ID: {table_id} (Format: {table_format})")
    logging.debug(f"TABLE_HEADERS: Slide ID: {dest_slide_id}. Row Headers: {row_headers}")
    logging.debug(f"TABLE_HEADERS: Slide ID: {dest_slide_id}. Column Headers: {col_headers}")

    # Determine starting row for data cells
    data_start_row = 2 if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)" else 1

    # Iterate through Data Cells (Starting from the determined data_start_row, column 1)
    for r_idx in range(data_start_row, rows):
        for c_idx in range(1, cols):
            try:
                data_cell = table_rows[r_idx]['tableCells'][c_idx]
                data_value = get_text_content_from_element(data_cell).strip()

                if data_value:
                    # Calculate index offsets
                    row_index_offset = r_idx - data_start_row
                    col_index_offset = c_idx - 1

                    if row_index_offset < len(row_headers) and col_index_offset < len(col_headers):
                        row_label = row_headers[row_index_offset]
                        col_keyword = col_headers[col_index_offset]
                    else:
                        row_label = f"[R{r_idx} Out of Bounds]"
                        col_keyword = f"[C{c_idx} Out of Bounds]"

                    # Print the required intersection debug output
                    logging.info(f"TABLE_INTERSECTION: Slide ID: {dest_slide_id}. Label='{row_label}' / Keyword='{col_keyword}' / Value='{data_value}'")

            except Exception as e:
                logging.error(f"TABLE_ERROR: Failed to process cell R:{r_idx}, C{c_idx}. Error: {e}")


# --- COPY & SYNC LOGIC (WITH KEYWORD DEBUGGING) ---

def copy_slide_content(slides_service, master_slide_id, master_pres_id, dest_pres_id, dest_slide_id, master_slide_json, drive_service, docai_client, storage_client):
    """Performs three-way synchronization (Delete/Add/Update)."""
    requests = []
    logging.info(f"Starting object synchronization for Slide ID: {dest_slide_id}")

    try:
        dest_slide_json = slides_service.presentations().pages().get(
            presentationId=dest_pres_id, pageObjectId=dest_slide_id, fields='pageElements'
        ).execute()
        dest_elements = dest_slide_json.get('pageElements', [])
    except HttpError as e:
        logging.error(f"Could not read destination slide {dest_slide_id}. Error: {e}"); return []

    master_elements = master_slide_json.get('pageElements', [])

    # PHASE 1: DELETE MISSING OBJECTS
    logging.debug(f"Phase 1: Checking {len(dest_elements)} destination elements for deletion.")
    for dest_element in dest_elements:
        if not find_master_match(dest_element, master_elements):
            requests.append({'deleteObject': {'objectId': dest_element['objectId']}}); logging.debug(f"ACTION: DELETE object {dest_element['objectId']} (not found in master).")

    # PHASE 2 & 3: ADD NEW OR UPDATE EXISTING OBJECTS
    logging.debug(f"Phase 2 & 3: Checking {len(master_elements)} master elements for addition/update.")
    for master_element in master_elements:

        # --- CORRECTLY DETERMINE element_type ---
        element_id = master_element['objectId']
        TYPE_KEYS = ('objectId', 'size', 'transform') # Keys to ignore when finding the type

        element_type = None
        for key in master_element.keys():
            if key not in TYPE_KEYS:
                element_type = key # e.g., 'shape', 'image', 'table'
                break

        if not element_type:
            logging.warning(f"UNHANDLED: Could not determine type for object ID {element_id}. Skipping.")
            continue
        # ---------------------------------------------

        # Existence Check - See if the master element already exists in the destination
        matching_dest_id = find_master_match(master_element, dest_elements) # Define variable

        # --- DIMENSIONAL CONVERSION AND UNIT ENFORCEMENT ---
        # *** INITIALIZE element_properties BEFORE USE ***
        element_properties = {'pageObjectId': dest_slide_id}

        master_size = copy.deepcopy(master_element.get('size', {}))
        if master_size:
            if master_size.get('width'):
                mag, unit = convert_emu_to_pt(master_size['width'].get('magnitude', 0), master_size['width'].get('unit', 'EMU'))
                master_size['width']['magnitude'] = round(mag, 3) # Rounding for cleaner JSON
                master_size['width']['unit'] = 'PT'
            if master_size.get('height'):
                mag, unit = convert_emu_to_pt(master_size['height'].get('magnitude', 0), master_size['height'].get('unit', 'EMU'))
                master_size['height']['magnitude'] = round(mag, 3) # Rounding for cleaner JSON
                master_size['height']['unit'] = 'PT'

        master_transform = copy.deepcopy(master_element.get('transform', {}))
        if master_transform:
            if 'translateX' in master_transform:
                master_transform['translateX'], _ = convert_emu_to_pt(master_transform['translateX'], 'EMU')
                master_transform['translateX'] = round(master_transform['translateX'], 3)
            if 'translateY' in master_transform:
                master_transform['translateY'], _ = convert_emu_to_pt(master_transform['translateY'], 'EMU')
                master_transform['translateY'] = round(master_transform['translateY'], 3)
            master_transform['unit'] = 'PT'

        if master_size and (master_size.get('width') or master_size.get('height')): element_properties['size'] = master_size
        if master_transform and ('translateX' in master_transform or 'translateY' in master_transform or 'unit' in master_transform): element_properties['transform'] = master_transform


        # --------------------------------------------------------------------------------
        # FIX: OCR CALL INTEGRATION (Guaranteed execution path for images)
        # --------------------------------------------------------------------------------
        if element_type == 'image':
            logging.debug(f"OCR_ACTION: BEGIN processing image element {element_id}.")
            # The function is invoked here! (Now with the required slides_service and dest_pres_id)
            extracted_text = process_image_ocr(master_element, drive_service, docai_client, storage_client, slides_service, dest_slide_id)

            if extracted_text:
                logging.info(f"OCR_RESULT: Text found ({len(extracted_text)} chars). Adding to validation metadata.")

                # Append the extracted text as a shape/text-element structure for validation processing
                # We use the current hardcoded format string for this standalone validation call
                keyword_metadata = process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, [{'text': {'textElements': [{'textRun': {'content': extracted_text}}]}}], "Format 1: Row 0/Col 0 Headers")
                GLOBAL_METADATA['Keyword_Values'].extend(keyword_metadata)
            else:
                logging.warning("OCR_RESULT: Image processing returned no readable text.")
        # --------------------------------------------------------------------------------


        if matching_dest_id:
            # --- ACTION: UPDATE EXISTING OBJECT ---
            target_id = matching_dest_id
            logging.debug(f"ACTION: UPDATE existing object {target_id} of type {element_type}.")

            # 1. Update Shape/Image/Table properties
            if element_type == 'shape' and 'shapeProperties' in master_element['shape']:
                props = scrub_read_only_fields(master_element['shape']['shapeProperties'])
                requests.append({'updateShapeProperties': {'objectId': target_id, 'shapeProperties': props, 'fields': '*'}})
            elif element_type == 'image' and 'imageProperties' in master_element['image']:
                props = scrub_read_only_fields(master_element['image']['imageProperties'])
                requests.append({'updateImageProperties': {'objectId': target_id, 'imageProperties': props, 'fields': '*'}})

            # 2. Update Text Content and Style
            if element_type in ('shape', 'table'):
                # First, clear existing text to avoid style conflicts
                requests.append({'deleteText': {'objectId': target_id, 'textRange': {'type': 'ALL'}}})

                # Insert the new text content from the master
                full_text = get_text_content_from_element(master_element)
                if full_text:
                    requests.append({'insertText': {'objectId': target_id, 'text': full_text}})

                # Apply detailed text and paragraph styling
                requests.extend(generate_style_update_requests(target_id, master_element))

            GLOBAL_METADATA['SlideID_Object'][target_id] = {'type': element_type.capitalize(), 'dest_id': dest_pres_id, 'page_id': dest_slide_id}

        else:
            # --- ACTION: ADD NEW OBJECT ---
            new_element_id = uuid.uuid4().hex
            logging.debug(f"ACTION: ADD new object {new_element_id} of type {element_type}.")

            create_request_body = {}
            if element_type == 'shape':
                create_request_body = {'createShape': {'objectId': new_element_id, 'shapeType': master_element['shape'].get('shapeType', 'TEXT_BOX'), 'elementProperties': element_properties}}
                if 'shapeProperties' in master_element['shape']:
                    create_request_body['createShape']['shapeProperties'] = scrub_read_only_fields(master_element['shape']['shapeProperties'])
            elif element_type == 'image':
                create_request_body = {'createImage': {'url': master_element['image']['contentUrl'], 'elementProperties': element_properties}}
                if 'imageProperties' in master_element['image']:
                    create_request_body['createImage']['imageProperties'] = scrub_read_only_fields(master_element['image']['imageProperties'])
            elif element_type == 'table':
                create_request_body = {'createTable': {'objectId': new_element_id, 'rows': master_element['table']['rows'], 'columns': master_element['table']['columns'], 'elementProperties': element_properties}}
                if 'tableProperties' in master_element['table']:
                     create_request_body['createTable']['tableProperties'] = scrub_read_only_fields(master_element['table']['tableProperties'])

            if create_request_body:
                requests.append(create_request_body)

            # Apply text content and styling for new objects
            if element_type in ('shape', 'table'):
                full_text = get_text_content_from_element(master_element)
                if full_text:
                    requests.append({'insertText': {'objectId': new_element_id, 'text': full_text}})
                requests.extend(generate_style_update_requests(new_element_id, master_element))

            GLOBAL_METADATA['SlideID_Object'][new_element_id] = {'type': element_type.capitalize(), 'dest_id': dest_pres_id, 'page_id': dest_slide_id}

    logging.info(f"Finished synchronization. Generated {len(requests)} batch requests.")
    return requests

# --- Helper Functions for Text/Table Processing ---

def process_table_for_replication(table_id, dest_pres_id, dest_slide_id, table_element):
    """Generates requests for table content, merging, and metadata (Simplified)."""
    requests = []; metadata = []; table_prop = table_element['table']; rows = table_prop['rows']; cols = table_prop['columns']
    logging.debug(f"Processing Table {table_id} content and metadata.")

    for r_idx in range(rows):
        for c_idx in range(cols):
            try:
                # Extract text content from the master element cell
                cell = table_prop['tableRows'][r_idx]['tableCells'][c_idx]; cell_content = get_text_content_from_element(cell).strip()

                if cell_content:
                    # 1. Delete existing text (clean cell for insert)
                    delete_text_request = {
                        'deleteText': {
                            'objectId': table_id,
                            'cellLocation': {'rowIndex': r_idx, 'columnIndex': c_idx},
                            'textRange': {'type': 'ALL'}
                        }
                    }
                    requests.append(delete_text_request)

                    # 2. Insert new text
                    insert_text_request = {
                        'insertText': {
                            'objectId': table_id,
                            'cellLocation': {'rowIndex': r_idx, 'columnIndex': c_idx},
                            'text': cell_content
                        }
                    }
                    requests.append(insert_text_request)

            except Exception as e:
                logging.warning(f"Error parsing table cell {r_idx},{c_idx}: {e}")
    logging.debug("Completed table content parsing.")
    return requests, metadata


def process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, page_elements, table_format="Format 1: Row 0/Col 0 Headers"):
    """Processes text for keyword/value extraction and calculates fuzzy confidence."""
    metadata = []
    # FIX: Updated regex to capture dual values like -$30M (-3%)
    VALUE_PATTERN = re.compile(r'(\([\+\-]?[\d\.,]+%\)|[\+\-]?\$?[\d\.,]+[KMT]?\s?[\(]*[\+\-]?[\d\.,]+%\)*|[\+\-]?\$?[\d\.,]+[KMT]?|\([\+\-]?[\d\.,]+%\))', re.IGNORECASE)

    for element in page_elements:
        element_id = element.get('objectId', uuid.uuid4().hex)
        element_type_key = next((k for k in element.keys() if k not in ['objectId', 'size', 'transform', 'elementProperties']), None)


        # --- TABLE LOGIC CHECK (Handles Format 1 and 2) ---
        if element_type_key == 'table':
            table_prop = element['table']
            table_rows = table_prop['tableRows']
            rows = table_prop['rows']
            cols = table_prop['columns']

            # Delegate to the specific table debug analysis helper for debugging
            extract_table_data_for_debug(element, dest_slide_id, table_format)

            row_headers, col_headers = _get_table_headers(element, table_format)

            # Determine starting row for data cells
            data_start_row = 2 if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)" else 1

            # Iterate through Data Cells (Starting from the determined data_start_row, column 1)
            for r_idx in range(data_start_row, rows):
                for c_idx in range(1, cols):
                    try:
                        data_cell = table_rows[r_idx]['tableCells'][c_idx]
                        line = get_text_content_from_element(data_cell).strip()

                        if line:
                            # Calculate index offsets
                            row_index_offset = r_idx - data_start_row
                            col_index_offset = c_idx - 1

                            if row_index_offset < len(row_headers) and col_index_offset < len(col_headers):
                                row_label = row_headers[row_index_offset]
                                col_keyword = col_headers[col_index_offset]
                            else:
                                row_label = f"[R{r_idx} Out of Bounds]"
                                col_keyword = f"[C{c_idx} Out of Bounds]"

                            value_match = VALUE_PATTERN.search(line)

                            if value_match:
                                value = value_match.group(0).replace('(', '-').replace(')', '').replace('$', '').strip()
                                conf = 1.0 # Max confidence since the structure is guaranteed

                                # Add valid table data as keyword metadata
                                metadata.append({
                                    'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                    'object_id': element_id, 'label': row_label, 'keyword': col_keyword, 'value': value,
                                    'confidence': conf
                                })

                    except Exception as e:
                        logging.error(f"TABLE_ERROR: Failed to process cell R:{r_idx}, C{c_idx}. Error: {e}")

            continue # Skip linear text analysis for tables

        # --- SHAPE/LINEAR TEXT ANALYSIS START ---

        full_text = get_text_content_from_element(element)

        if not full_text.strip():
            continue

        lines = full_text.split('\n')
        product_area_label = "No Label Found"

        # LOGGING: START ANALYSIS
        logging.debug(f"KEYWORD_ANALYSIS: Slide ID: {dest_slide_id}. Element {element_id}. Text: '{full_text.strip()[:50]}...'")
        # ----------------------------------

        for line_idx, line in enumerate(lines):
            if line.strip():
                for keyword, alias in FIN_KEYWORDS.items():
                    search_terms = [keyword, alias]

                    best_confidence = 0
                    best_term = ""

                    for term in search_terms:
                        # Fuzzy matching logic
                        confidence = fuzz.partial_ratio(term.lower(), line.lower()) / 100.0

                        # LOGGING: Report every score
                        logging.debug(f"   MATCH_ATTEMPT: Slide ID: {dest_slide_id}. Line {line_idx+1}: '{line.strip()[:30]}...' -> Term '{term}' Score: {confidence:.4f}")

                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_term = term

                    if best_confidence >= KEYWORD_CONFIDENCE_THRESHOLD:

                        # --- DUAL-VALUE LOGIC CHECK ---
                        # If the keyword does NOT end with '$' or '%', assume it's the combined dual value.
                        is_dual_value = not (best_term.endswith('$') or best_term.endswith('%'))

                        # Find the actual keyword match location to anchor the value search
                        anchor_match = re.search(r'\b(' + re.escape(best_term) + r'|' + re.escape(alias) + r')\s*[:\s]*', line, re.IGNORECASE)

                        if anchor_match:
                            # Start value search just after the keyword/colon
                            match_end_index = anchor_match.end(); search_start = min(match_end_index, len(line))

                            if is_dual_value:
                                # DUAL VALUE CASE: Capture the rest of the line as the dual value
                                value = line[search_start:].strip()

                                # Find Dollar value
                                dollar_match = re.search(r'[\+\-]?\$?[\d\.,]+[KMT]?', value)
                                value_dollar = dollar_match.group(0).strip() if dollar_match else ""

                                # Find Percentage value
                                percent_match = re.search(r'(\([\+\-]?[\d\.,]+%\)|[\+\-]?[\d\.,]+%)', value)
                                value_percent = percent_match.group(0).strip() if percent_match else ""

                                conf = round(best_confidence, 4)

                                # Log and add DOLLAR entry (e.g., YoY -> YoY $)
                                if value_dollar:
                                    logging.info(f"   KEYWORD_FOUND (Dual-$): Slide ID: {dest_slide_id}. Keyword='{keyword} $' Value='{value_dollar}' Confidence={conf:.4f}")
                                    metadata.append({
                                        'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                        'object_id': element_id, 'label': product_area_label, 'keyword': f"{keyword} $", 'value': value_dollar,
                                        'confidence': conf
                                    })

                                # Log and add PERCENT entry (e.g., YoY -> YoY %)
                                if value_percent:
                                    logging.info(f"   KEYWORD_FOUND (Dual-%): Slide ID: {dest_slide_id}. Keyword='{keyword} %' Value='{value_percent}' Confidence={conf:.4f}")
                                    metadata.append({
                                        'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                        'object_id': element_id, 'label': product_area_label, 'keyword': f"{keyword} %", 'value': value_percent,
                                        'confidence': conf
                                    })
                            else:
                                # SINGLE VALUE CASE: Find standard value after keyword
                                value_match = VALUE_PATTERN.search(line, search_start)
                                if value_match:
                                    value = value_match.group(0).replace('(', '-').replace(')', '').replace('$', '').strip()
                                    conf = round(best_confidence, 4)

                                    # LOGGING: Successful match
                                    logging.info(f"   KEYWORD_FOUND: Slide ID: {dest_slide_id}. Keyword='{keyword}' Value='{value}' Confidence={conf:.4f} in Line {line_idx+1}")

                                    metadata.append({
                                        'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                        'object_id': element_id, 'label': product_area_label, 'keyword': keyword, 'value': value,
                                        'confidence': conf
                                    })

                            # Move to the next keyword/line after a successful match
                            break

    return metadata

# --- Main Execution Function (FULL DEFINITION IN BLOCK 2) ---

def run_back_end(master_url, dest_id_or_url, table_format):

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
# --- UI Elements (Final Corrected Definition) ---

# 1. Status Output Area (MUST COME FIRST)
status_output = widgets.Output()

# 2. Logo and Title (Using widgets.HTML for compatibility)
logo_html = widgets.HTML(
    '<div style="float:left; width: 2in; height: 1in; background-color: #4285F4; color: white; text-align: center; line-height: 1in; font-size: 10px;"></div>'
)
title_html = widgets.HTML(
    '<div style="text-align: center; font-size: 24px; font-weight: bold; padding-bottom: 10px;">Slides Synchronization & Validation Tool</div>'
)
header = widgets.VBox([title_html, logo_html])


# 3. Input Controls
master_url_input = widgets.Text(
    description='Master:',
    placeholder='Google Slides URL/ID or File Name',
    value='https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA'
)
destination_url_input = widgets.Text(
    description='Destination:',
    placeholder='Slides URL/ID or Folder URL/ID',
    value='https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
)

# --- NEW: TABLE FORMAT DROPDOWN ---
table_format_dropdown = widgets.Dropdown(
    options=['Format 1: Row 0/Col 0 Headers', 'Format 2: Dual Header (Rows 0 & 1 Combined)'],
    value='Format 1: Row 0/Col 0 Headers',
    description='Table Format:',
)

go_button = widgets.Button(description='Go', disabled=True)

# 4. File Browsing Simulation
def file_picker(description, file_type):
    def on_click(b):
        status_output.clear_output()
        with status_output:
            print(f"Simulating Drive browsing for {file_type}...")
            print("Please manually enter the ID for now.")
    btn = widgets.Button(description=f"Browse Drive for {file_type}")
    btn.on_click(on_click)
    return widgets.VBox([widgets.Label(description), btn])

# 5. Validation Logic
def validate_inputs():
    is_valid = bool(master_url_input.value.strip() and destination_url_input.value.strip())
    go_button.disabled = not is_valid

    if is_valid:
        go_button.style.button_color = 'lightgreen'
    else:
        go_button.style.button_color = 'white'

master_url_input.observe(lambda change: validate_inputs(), names='value')
destination_url_input.observe(lambda change: validate_inputs(), names='value')
validate_inputs() # Call once to set initial state


# 6. Go Button Handler (Main Sync)
def on_go_clicked(b):

    go_button.disabled = True
    status_output.clear_output()

    master_url = master_url_input.value
    destination_url = destination_url_input.value

    # Get selected table format (extracting just the key part)
    selected_format = table_format_dropdown.value

    with status_output:
        print(f"Status: Starting back-end process (Table Format: **{selected_format}**)...")

    # Pass the selected format to the run_back_end function
    debug_link, status_code = run_back_end(master_url, destination_url, selected_format)

    with status_output:
        print(f"Status: Completed. Debug Log: {debug_link}")

        popup_message = "Completed with no inconsistencies" if status_code == 0 else "**Inconsistencies found (See log for details).**"

        print(f"\n\n--- POP-UP MESSAGE SIMULATION ---\n")
        print(f"MESSAGE: {popup_message}")
        print(f"Clicking OK will attempt to open the Debug Log URL: file:///{os.path.abspath(debug_link)}")
        print(f"--- END POP-UP SIMULATION ---")

    go_button.disabled = False
    validate_inputs()

go_button.on_click(on_go_clicked)


# 8. Main UI structure and display
ui = widgets.VBox([
    header,
    widgets.HBox([master_url_input, file_picker("Master:", "Slides")]),
    widgets.HBox([destination_url_input, file_picker("Destination:", "Slides/Folder")]),
    # Insert new dropdown here
    widgets.HBox([table_format_dropdown]),
    widgets.HBox([go_button]),
    widgets.Label(value="Status: Ready"),
    status_output
], layout=Layout(width='800px', border='1px solid lightgray', padding='10px'))

display(ui)