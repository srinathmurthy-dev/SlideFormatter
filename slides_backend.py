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
    'Net Revenue (PROFORMA)': 'Net Rev (PROFORMA)',
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

# --- All other backend functions from the original script are here ---
# (This includes find_destination_files, get_slide_page_elements,
# find_master_match, process_image_ocr, and all other helpers from the original script)

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
    
    # Helper: Extracts and normalizes size/position metrics to Points (PT)
    def get_element_metrics(element):
        size = element.get('size', {})
        width_val = size.get('width', {}).get('magnitude', 0)
        width_unit = size.get('width', {}).get('unit', 'EMU')
        height_val = size.get('height', {}).get('magnitude', 0)
        height_unit = size.get('height', {}).get('unit', 'EMU')
        
        transform = element.get('transform', {})
        x_pos_val = transform.get('translateX', 0)
        y_pos_val = transform.get('translateY', 0)

        # The API *always* returns transform translations in EMU, so we must convert them.
        # Size, however, can be in either EMU or PT.
        width_pt, _ = convert_emu_to_pt(width_val, width_unit)
        height_pt, _ = convert_emu_to_pt(height_val, height_unit)
        x_pos_pt, _ = convert_emu_to_pt(x_pos_val, 'EMU')
        y_pos_pt, _ = convert_emu_to_pt(y_pos_val, 'EMU')
        
        # Rounding for reliable comparison
        return (round(width_pt, 2), round(height_pt, 2), round(x_pos_pt, 2), round(y_pos_pt, 2))
    
    w1, h1, x1, y1 = get_element_metrics(element1)
    
    logging.debug(f"MATCH: Comparing Master ({element1.get('objectId')}) DIMS: W={w1}, H={h1}, X={x1}, Y={y1}")
    
    for element2 in element2_list:
        w2, h2, x2, y2 = get_element_metrics(element2)
        
        logging.debug(f"MATCH:    vs Dest ({element2.get('objectId')}) DIMS: W={w2}, H={h2}, X={x2}, Y={y2}")
        
        # A match is found if position and size are identical
        if w1 == w2 and h1 == h2 and x1 == x2 and y1 == y2: 
            logging.debug(f"MATCH:    *** EXACT MATCH FOUND *** between Master {element1.get('objectId')} and Dest {element2.get('objectId')}")
            return element2.get('objectId') # <-- RETURN THE DESTINATION OBJECT ID
            
    return None

# --- Document AI Processing & Table Conversion ---

def _get_text_from_docai_layout(layout, full_text):
    """Extracts text from the document's full text based on a layout's text anchor."""
    if layout and layout.text_anchor and layout.text_anchor.text_segments:
        return "".join([full_text[segment.start_index:segment.end_index] for segment in layout.text_anchor.text_segments])
    return ""

def _convert_docai_table_to_slides_format(docai_table, full_text):
    """Converts a Document AI table object into a dictionary that mimics the Google Slides API table structure."""
    all_rows = list(docai_table.header_rows) + list(docai_table.body_rows)
    if not all_rows: return None

    num_rows, num_cols = len(all_rows), max((len(row.cells) for row in all_rows), default=0)
    table_rows_data = []

    for row in all_rows:
        table_cells = []
        for cell in row.cells:
            cell_text = _get_text_from_docai_layout(cell.layout, full_text).strip().replace('\n', ' ')
            table_cells.append({'text': {'textElements': [{'textRun': {'content': cell_text}}]}})
        table_rows_data.append({'tableCells': table_cells})

    return {
        'objectId': f"ocr_table_{uuid.uuid4().hex[:8]}",
        'table': {'rows': num_rows, 'columns': num_cols, 'tableRows': table_rows_data}
    }

def process_image_ocr(image_element, drive_service, docai_client, storage_client, slides_service, presentation_id, page_id):
    """Processes an image, detects tables, and returns a list of fake 'page element' objects for validation."""
    logging.debug("OCR_START: Starting Document AI OCR processing for image element.")
    temp_filename, blob, ocr_elements = f"temp_image_{uuid.uuid4().hex[:8]}.png", None, []

    try:
        image_obj_id = image_element['objectId']
        logging.debug(f"OCR_STEP: Requesting slide thumbnail for page ID: {page_id} in Presentation ID: {presentation_id}")

        creds = slides_service._http.credentials
        response = slides_service.presentations().pages().getThumbnail(
            presentationId=presentation_id, pageObjectId=page_id,
            thumbnailProperties_thumbnailSize='LARGE', thumbnailProperties_mimeType='PNG'
        ).execute()

        thumbnail_url = response.get('contentUrl')
        response = requests.get(thumbnail_url, headers={'Authorization': 'Bearer ' + creds.token})
        
        if response.status_code != 200:
            logging.error(f"OCR_ERROR: Failed to download image from URL. Status: {response.status_code}.")
            return []

        image_bytes = response.content
        bucket = storage_client.bucket(GCS_BUCKET_NAME); blob = bucket.blob(temp_filename)
        blob.upload_from_string(image_bytes, content_type='image/png')
        
        processor_name = docai_client.processor_path(GCP_PROJECT_ID, "us", DOC_AI_PROCESSOR_ID)
        request = documentai.ProcessRequest(name=processor_name, raw_document=documentai.RawDocument(content=image_bytes, mime_type="image/png"))
        document = docai_client.process_document(request=request).document

        has_tables = False
        if document.pages:
            for page in document.pages:
                if page.tables:
                    has_tables, _ = True, logging.info(f"OCR_TABLE_DETECT: Found {len(page.tables)} table(s) in image.")
                    for table in page.tables:
                        if fake_table := _convert_docai_table_to_slides_format(table, document.text):
                            ocr_elements.append(fake_table)
        
        if not has_tables and document.text:
            logging.info("OCR_TABLE_DETECT: No tables found, processing as plain text.")
            ocr_elements.append({
                'objectId': f"ocr_text_{uuid.uuid4().hex[:8]}",
                'text': {'textElements': [{'textRun': {'content': document.text.strip()}}]}
            })
            
    except HttpError as e: logging.error(f"OCR_ERROR: Slides/Drive API error: {e}")
    except Exception as e: logging.error(f"OCR_ERROR: Document AI processing failed: {e}")
    finally:
        try:
            if blob and storage_client.bucket(GCS_BUCKET_NAME).blob(temp_filename).exists():
                blob.delete()
        except Exception as e: logging.warning(f"OCR_WARNING: Failed to delete GCS object: {e}")
            
    return ocr_elements

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

# --- HELPER: GENERATE GRANULAR TEXT STYLE REQUESTS ---
def generate_text_style_requests(object_id, text_elements, cell_location=None):
    """
    Generates a list of UpdateTextStyle requests for a given set of textElements,
    focusing on basic font, color, and style properties.
    """
    requests = []
    for text_element in text_elements:
        # We only care about styling for runs of text that have content.
        if 'textRun' in text_element and 'content' in text_element['textRun']:
            style = text_element['textRun'].get('style', {})

            # Extract only the requested styles
            styles_to_apply = {}
            if 'foregroundColor' in style: styles_to_apply['foregroundColor'] = style.get('foregroundColor')
            if 'fontFamily' in style: styles_to_apply['fontFamily'] = style.get('fontFamily')
            if 'fontSize' in style: styles_to_apply['fontSize'] = style.get('fontSize')
            if 'bold' in style: styles_to_apply['bold'] = style.get('bold')
            if 'italic' in style: styles_to_apply['italic'] = style.get('italic')

            # The API requires a start and end index for the range. Skip if they don't exist.
            start_index = text_element.get('startIndex')
            end_index = text_element.get('endIndex')

            if styles_to_apply and start_index is not None and end_index is not None:
                request_body = {
                    'objectId': object_id,
                    'style': styles_to_apply,
                    'textRange': {'startIndex': start_index, 'endIndex': end_index},
                    'fields': ",".join(styles_to_apply.keys())
                }

                # If a cellLocation is provided, this is a table cell.
                if cell_location:
                    request_body['cellLocation'] = cell_location

                requests.append({'updateTextStyle': request_body})

    return requests
# ----------------------------------------------------

def _create_recursive_field_mask(properties, parent_key=''):
    """
    Recursively traverses a dictionary to create a dot-notated field mask,
    filtering out read-only fields at any nesting level.
    """
    mask_paths = []
    # Per the API docs, these fields are read-only and must be excluded from update masks.
    READ_ONLY_FIELDS = ['placeholder', 'propertyState', 'type']

    for key, value in properties.items():
        # Skip any field that is read-only, regardless of its depth.
        if key in READ_ONLY_FIELDS:
            continue

        current_key = f"{parent_key}.{key}" if parent_key else key

        if isinstance(value, dict):
            mask_paths.extend(_create_recursive_field_mask(value, parent_key=current_key))
        else:
            # We only add the path if it's a leaf node.
            mask_paths.append(current_key)
            
    return mask_paths
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
    """
    Analyzes master and destination slides to generate API requests.
    1.  Structural: Creates/deletes objects.
    2.  Formatting: Applies styles and text.
    3.  ID Map: Returns a map of master-to-destination IDs for pre-existing objects.
    """
    structural_requests, formatting_requests, existing_id_map = [], [], {}
    logging.info(f"Starting object synchronization for Slide ID: {dest_slide_id}")
    
    try:
        dest_slide_json = slides_service.presentations().pages().get(presentationId=dest_pres_id, pageObjectId=dest_slide_id).execute()
        dest_elements = dest_slide_json.get('pageElements', [])
    except HttpError as e:
        logging.error(f"Could not read destination slide {dest_slide_id}. Error: {e}"); return [], [], {}

    master_elements = master_slide_json.get('pageElements', [])

    # Phase 1A: DELETE objects from destination that are not in the master.
    logging.debug(f"Phase 1A: Checking {len(dest_elements)} destination elements for deletion.")
    for dest_element in dest_elements:
        if not find_master_match(dest_element, master_elements):
            structural_requests.append({'deleteObject': {'objectId': dest_element['objectId']}})
            logging.debug(f"ACTION: DELETE object {dest_element['objectId']} (not found in master).")

    # Phase 1B: CREATE master objects that are not in the destination and MAP existing ones.
    logging.debug(f"Phase 1B: Checking {len(master_elements)} elements for creation and ID mapping.")
    for master_element in master_elements:
        dest_match_id = find_master_match(master_element, dest_elements)
        if not dest_match_id:
            new_element_id = master_element['objectId']
            element_type = next((k for k in master_element if k not in ('objectId', 'size', 'transform')), None)
            if not element_type: continue

            element_properties = {'pageObjectId': dest_slide_id}
            if size := copy.deepcopy(master_element.get('size')):
                if 'width' in size: size['width']['magnitude'], size['width']['unit'] = round(convert_emu_to_pt(size['width'].get('magnitude', 0), size['width'].get('unit', 'EMU'))[0], 3), 'PT'
                if 'height' in size: size['height']['magnitude'], size['height']['unit'] = round(convert_emu_to_pt(size['height'].get('magnitude', 0), size['height'].get('unit', 'EMU'))[0], 3), 'PT'
                element_properties['size'] = size
            
            if transform := copy.deepcopy(master_element.get('transform')):
                if 'translateX' in transform: transform['translateX'], _ = convert_emu_to_pt(transform['translateX'], 'EMU')
                if 'translateY' in transform: transform['translateY'], _ = convert_emu_to_pt(transform['translateY'], 'EMU')
                transform['unit'] = 'PT'
                element_properties['transform'] = transform

            create_request = None
            if element_type == 'shape': create_request = {'createShape': {'objectId': new_element_id, 'shapeType': master_element['shape'].get('shapeType', 'TEXT_BOX'), 'elementProperties': element_properties}}
            elif element_type == 'image': create_request = {'createImage': {'url': master_element['image']['contentUrl'], 'elementProperties': element_properties}}
            elif element_type == 'table': create_request = {'createTable': {'objectId': new_element_id, 'rows': master_element['table']['rows'], 'columns': master_element['table']['columns'], 'elementProperties': element_properties}}
            if create_request: structural_requests.append(create_request)
        else:
            existing_id_map[master_element['objectId']] = dest_match_id

    # Phase 2: Generate formatting requests using MASTER IDs as placeholders.
    logging.debug(f"Phase 2: Checking {len(master_elements)} master elements for formatting.")
    for master_element in master_elements:
        master_id = master_element['objectId']
        element_type = next((k for k in master_element if k not in ('objectId', 'size', 'transform')), None)

        if element_type == 'image':
            if ocr_elements := process_image_ocr(master_element, drive_service, docai_client, storage_client, slides_service, master_pres_id, master_slide_id):
                GLOBAL_METADATA['Keyword_Values'].extend(process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, ocr_elements, "Format 1: Row 0/Col 0 Headers"))

        if element_type == 'shape' and (props := master_element.get('shape', {}).get('shapeProperties')):
            styles_to_apply = {k: props[k] for k in ('shapeBackgroundFill', 'outline') if k in props}
            if styles_to_apply:
                formatting_requests.append({'updateShapeProperties': {'objectId': master_id, 'shapeProperties': styles_to_apply, 'fields': ",".join(_create_recursive_field_mask(styles_to_apply))}})

        if master_element.get(element_type, {}).get('text'):
            text_elements = master_element[element_type]['text']['textElements']
            if element_type == 'table':
                formatting_requests.extend(process_table_for_replication(master_id, dest_pres_id, dest_slide_id, master_element))
            elif element_type == 'shape':
                if full_text := get_text_content_from_element(master_element).strip():
                    formatting_requests.append({'deleteText': {'objectId': master_id, 'textRange': {'type': 'ALL'}}})
                    formatting_requests.append({'insertText': {'objectId': master_id, 'text': full_text}})
                formatting_requests.extend(generate_text_style_requests(master_id, text_elements))

    logging.info(f"Finished sync analysis. Generated {len(structural_requests)} structural, {len(formatting_requests)} formatting requests.")
    return structural_requests, formatting_requests, existing_id_map

# --- Helper Functions for Text/Table Processing ---

def process_table_for_replication(table_id, dest_pres_id, dest_slide_id, table_element):
    """
    Generates API requests to replicate the text content and styling for each
    cell in a master table.
    """
    requests = []
    table_prop = table_element['table']
    rows, cols = table_prop['rows'], table_prop['columns']

    logging.debug(f"Processing Table {table_id} text content and styling.")
    
    for r_idx in range(rows):
        for c_idx in range(cols):
            try:
                cell = table_prop['tableRows'][r_idx]['tableCells'][c_idx]
                cell_content = get_text_content_from_element(cell) # Keep original whitespace
                
                # Define the location for all operations on this cell
                cell_location = {'rowIndex': r_idx, 'columnIndex': c_idx}

                # 1. Clear all existing text in the destination cell
                requests.append({'deleteText': {'objectId': table_id, 'cellLocation': cell_location, 'textRange': {'type': 'ALL'}}})

                if cell_content:
                    # 2. Insert the new, unstyled text from the master
                    requests.append({'insertText': {'objectId': table_id, 'cellLocation': cell_location, 'text': cell_content}})
                    
                    # 3. Apply specific styles to the text runs within the cell
                    if cell.get('text', {}).get('textElements'):
                        text_elements = cell['text']['textElements']
                        requests.extend(
                            generate_text_style_requests(table_id, text_elements, cell_location)
                        )

            except Exception as e:
                logging.warning(f"Error parsing table cell {r_idx},{c_idx} for text/style replication: {e}")

    logging.debug(f"Generated {len(requests)} text and style requests for table {table_id}.")
    return requests


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
                    
                    structural_reqs, formatting_reqs, existing_id_map = copy_slide_content(
                        slides_service, master_slide_id, master_id, dest_id, dest_slide_id,
                        master_slide, drive_service, docai_client, storage_client
                    )
                    
                    # This final_id_map will hold ALL master-to-destination mappings.
                    final_id_map = existing_id_map.copy()
                    logging.debug(f"ID_MAP_INIT: Started with {len(final_id_map)} pre-existing object mappings.")

                    # --- Execute Structural Changes ---
                    if structural_reqs:
                        try:
                            response = slides_service.presentations().batchUpdate(presentationId=dest_id, body={'requests': structural_reqs}).execute()

                            # --- Update the ID map with newly created object IDs ---
                            replies = response.get('replies', [])
                            create_requests = [(i, req) for i, req in enumerate(structural_reqs) if 'create' in next(iter(req))]
                            for i, req in create_requests:
                                if i < len(replies):
                                    action_key = next(iter(req))
                                    master_id_val = req[action_key].get('objectId')
                                    if action_key in replies[i] and (new_id := replies[i][action_key].get('objectId')):
                                        final_id_map[master_id_val] = new_id
                                        logging.debug(f"ID_MAP_CREATE: Mapped new master ID '{master_id_val}' -> '{new_id}'")

                            logging.info(f"Structural updates applied. Final ID map has {len(final_id_map)} entries.")

                        except HttpError as e:
                            logging.error(f"STRUCTURAL BatchUpdate failed on slide {i+1}: {e}"); total_inconsistencies += 1; continue

                    # --- Remap and Execute Formatting Changes ---
                    if formatting_reqs:
                        for req in formatting_reqs:
                            action_key = next(iter(req))
                            if 'objectId' in req[action_key]:
                                master_obj_id = req[action_key]['objectId']
                                if master_obj_id in final_id_map:
                                    req[action_key]['objectId'] = final_id_map[master_obj_id]
                                else:
                                    logging.warning(f"ID_REMAP_FAIL: Master object ID {master_obj_id} not found in final map. Formatting may fail.")


                        logging.debug(f"Executing BatchUpdate with {len(formatting_reqs)} remapped FORMATTING requests.")

                        # --- Enhanced Per-Request Logging ---
                        logging.debug("--- START: Individual Formatting Requests ---")
                        for idx, req in enumerate(formatting_reqs):
                            logging.debug(f"  Request [{idx+1}/{len(formatting_reqs)}]: {json.dumps(req, indent=2)}")
                        logging.debug("--- END: Individual Formatting Requests ---")
                        # ------------------------------------

                        try:
                            slides_service.presentations().batchUpdate(presentationId=dest_id, body={'requests': formatting_reqs}).execute()
                            logging.info(f"Applied {len(formatting_reqs)} formatting batch updates successfully.")
                        except HttpError as e:
                            logging.error(f"--- START: FORMATTING BATCHUPDATE FAILURE (Slide {i+1} of {dest_name}) ---")
                            logging.error(f"  Raw Error Content: {e.content.decode()}")
                            try:
                                error_details = json.loads(e.content.decode())
                                failing_requests = error_details.get("error", {}).get("details", [{}])[0].get("badRequest", {}).get("invalidRequests", [])
                                if failing_requests:
                                    logging.error("  --- Failing Request Payloads ---")
                                    for req_info in failing_requests:
                                        logging.error(json.dumps(req_info, indent=2))
                                    logging.error("  ---------------------------------")
                                else:
                                     logging.error("  Could not extract specific failing requests from the error details.")
                            except Exception as json_e:
                                logging.error(f"  Could not parse JSON from HttpError content: {json_e}")
                            logging.error(f"  Full Error Traceback: {e}")
                            logging.error(f"--- END: FORMATTING BATCHUPDATE FAILURE ---")
                            total_inconsistencies += 1

                    # Run validation logic on the updated content in the destination slide
                    dest_slide_elements = dest_slide.get('pageElements', [])
                    keyword_metadata = process_text_bearing_objects(slides_service, dest_id, dest_slide_id, dest_slide_elements, table_format)
                    GLOBAL_METADATA['Keyword_Values'].extend(keyword_metadata)
                    
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