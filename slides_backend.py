import os
import sys
import json
import logging
import uuid
import re
import copy
from io import StringIO, BytesIO
from urllib.parse import urlparse, parse_qs
import io
import requests

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

# Utility Imports
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

# --- Logging Setup ---
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
logging.info(f"Logging configured. File: {LOG_FILENAME}")

# --- Authentication Functions ---
def get_user_service():
    creds = None
    if os.path.exists(TOKEN_FILE): creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try: creds.refresh(Request())
        except Exception: creds = None
    if not creds or not creds.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            flow.redirect_uri = REDIRECT_URI
            auth_url, _ = flow.authorization_url(prompt='consent')
            print(f"\n---------------------- AUTH REQUIRED -----------------------\n{auth_url}")
            full_redirect_url = input("\n3. Paste the FULL URL from the browser here: ")
            query = parse_qs(urlparse(full_redirect_url).query)
            verification_code = query.get('code', [None])[0]
            if not verification_code: raise Exception("Could not find 'code' in URL.")
            flow.fetch_token(code=verification_code)
            creds = flow.credentials
            with open(TOKEN_FILE, 'w') as token: token.write(creds.to_json())
        except FileNotFoundError:
            logging.error(f"'{CREDENTIALS_FILE}' not found."); return None, None
        except Exception as e:
            logging.error(f"User Authentication failed: {e}"); return None, None
    try:
        slides_service = build('slides', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        return slides_service, drive_service
    except Exception as e:
        logging.error(f"Failed to build user services: {e}"); return None, None

def get_document_ai_client():
    try:
        docai_client = documentai.DocumentProcessorServiceClient()
        storage_client = storage.Client(project=GCP_PROJECT_ID)
        return docai_client, storage_client
    except Exception as e:
        logging.error(f"DocAI client setup failed: {e}"); return None, None

def get_service():
    slides_service, drive_service = get_user_service()
    docai_client, storage_client = get_document_ai_client()
    if not all([slides_service, drive_service, docai_client, storage_client]):
        return None, None, None, None
    return slides_service, drive_service, docai_client, storage_client

# --- Utility Functions ---
def extract_id_from_url(url_or_id):
    parts = url_or_id.split('/')
    if 'd' in parts: return parts[parts.index('d') + 1]
    if 'folder' in parts: return parts[parts.index('folder') + 1]
    return url_or_id

def find_destination_files(drive_service, dest_url):
    file_id = extract_id_from_url(dest_url)
    try:
        file_meta = drive_service.files().get(fileId=file_id, fields='mimeType, name, id').execute()
        if file_meta['mimeType'] == 'application/vnd.google-apps.folder':
            query = f"'{file_id}' in parents and mimeType='application/vnd.google-apps.presentation'"
            results = drive_service.files().list(q=query, fields='files(id, name)').execute()
            return [{'id': f['id'], 'name': f['name']} for f in results.get('files', [])]
        elif file_meta['mimeType'] == 'application/vnd.google-apps.presentation':
            return [{'id': file_id, 'name': file_meta['name']}]
        return []
    except HttpError as e:
        logging.error(f"Drive API Error for ID {file_id}: {e}"); return []

def get_slide_page_elements(slides_service, pres_id):
    try:
        pres = slides_service.presentations().get(presentationId=pres_id, fields='slides').execute()
        return {slide['objectId']: slide for slide in pres.get('slides', [])}, pres
    except HttpError as e:
        logging.error(f"Slides API Error for presentation {pres_id}: {e}"); return {}, {}

def find_master_match(element1, element2_list):
    def get_metrics(element):
        size = element.get('size', {})
        width = size.get('width', {}).get('magnitude', 0)
        height = size.get('height', {}).get('magnitude', 0)
        transform = element.get('transform', {})
        x_pos = transform.get('translateX', 0)
        y_pos = transform.get('translateY', 0)
        return (round(width, 3), round(height, 3), round(x_pos, 3), round(y_pos, 3))

    w1, h1, x1, y1 = get_metrics(element1)
    logging.debug(f"MATCH: Comparing Master ({element1.get('objectId')}) DIMS: W={w1}, H={h1}, X={x1}, Y={y1}")

    if w1 == 0 and h1 == 0 and not element1.get('shape'): return True

    for e2 in element2_list:
        w2, h2, x2, y2 = get_metrics(e2)
        logging.debug(f"MATCH:    vs Dest ({e2.get('objectId')}) DIMS: W={w2}, H={h2}, X={x2}, Y={y2}")
        if w1 == w2 and h1 == h2 and x1 == x2 and y1 == y2:
            logging.debug(f"MATCH:    *** EXACT MATCH FOUND *** between Master {element1.get('objectId')} and Dest {e2.get('objectId')}")
            return e2.get('objectId')

    return None

# --- Document AI Processing ---
def process_image_ocr(image_element, drive_service, docai_client, storage_client, slides_service, dest_pres_id):
    temp_filename = f"temp_{uuid.uuid4().hex[:8]}.png"
    blob = None
    try:
        image_obj_id = image_element['objectId']
        page_obj_id = image_element.get('parentObjectId', image_obj_id)
        thumb = slides_service.presentations().pages().getThumbnail(
            presentationId=dest_pres_id,
            pageObjectId=page_obj_id,
            thumbnailProperties={'thumbnailSize': 'LARGE', 'mimeType': 'PNG'}
        ).execute()

        response = requests.get(thumb['contentUrl'])
        if response.status_code != 200:
            logging.error(f"OCR Error: Failed to download image. Status: {response.status_code}")
            return ""

        image_bytes = response.content
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(temp_filename)
        blob.upload_from_string(image_bytes, content_type='image/png')

        docai_request = documentai.ProcessRequest(
            name=docai_client.processor_path(GCP_PROJECT_ID, "us", DOC_AI_PROCESSOR_ID),
            raw_document=documentai.RawDocument(content=image_bytes, mime_type="image/png")
        )
        doc_response = docai_client.process_document(request=docai_request)

        def get_text(segment, full_text):
            if not segment: return ""
            return full_text[segment.start_index:segment.end_index].strip()

        full_text = doc_response.document.text
        content = []
        for page in doc_response.document.pages:
            for table in page.tables:
                table_text = ""
                for header_row in table.header_rows:
                    table_text += "\t".join([get_text(cell.layout.text_anchor, full_text) for cell in header_row.cells]) + "\n"
                for body_row in table.body_rows:
                    table_text += "\t".join([get_text(cell.layout.text_anchor, full_text) for cell in body_row.cells]) + "\n"
                content.append(table_text)
            if not page.tables:
                content.append(get_text(page.layout.text_anchor, full_text))
        return "\n\n".join(content).strip()

    except Exception as e:
        logging.error(f"OCR processing failed: {e}")
        return ""
    finally:
        if blob and blob.exists(): blob.delete()

# --- Style and Content Helpers ---
def convert_emu_to_pt(magnitude):
    return magnitude / 12700 if magnitude else 0

def scrub_read_only_fields(props):
    read_only = ['parentObjectId', 'propertyState', 'resolvedSize', 'resolvedTransform', 'isPlaceholder', 'placeholderId', 'kind', 'source', 'parentTextRange', 'content', 'text']
    if isinstance(props, dict):
        return {k: scrub_read_only_fields(v) for k, v in props.items() if k not in read_only and re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower() not in read_only}
    if isinstance(props, list):
        return [scrub_read_only_fields(item) for item in props]
    return props

def get_text_content_from_element(element):
    text_obj = element.get('text') or element.get(next((k for k in element if k not in ['objectId', 'size', 'transform']), None), {}).get('text')
    if not text_obj: return ""
    return "".join(te.get('textRun', {}).get('content', '') or '\n' for te in text_obj.get('textElements', [])).strip()

def _create_field_mask(props):
    """Creates a field mask from the top-level keys of a properties dictionary."""
    if not isinstance(props, dict): return ""
    return ",".join(props.keys())

def generate_style_update_requests(object_id, master_element, cell_location=None):
    requests = []
    # When processing a table cell, the cell itself is passed as the `master_element`.
    text_obj = master_element.get('text') or master_element.get('shape', {}).get('text')
    if not text_obj: return []

    for te in text_obj.get('textElements', []):
        # Only process elements that have a valid range. This prevents errors with empty paragraphs.
        if 'startIndex' in te and 'endIndex' in te:
            text_range = {'type': 'FIXED_RANGE', 'startIndex': te['startIndex'], 'endIndex': te['endIndex']}
        else:
            continue # Skip elements without a valid range

        # Text Style (font, color, etc.)
        if 'textRun' in te and 'style' in te['textRun']:
            style = scrub_read_only_fields(te['textRun']['style'])
            field_mask = _create_field_mask(style)
            if field_mask:
                req_body = {'objectId': object_id, 'style': style, 'textRange': text_range, 'fields': field_mask}
                if cell_location: req_body['cellLocation'] = cell_location
                requests.append({'updateTextStyle': req_body})

        # Paragraph Style (alignment, etc.)
        if 'paragraphMarker' in te and 'style' in te['paragraphMarker']:
            style = scrub_read_only_fields(te['paragraphMarker']['style'])
            field_mask = _create_field_mask(style)
            if field_mask:
                req_body = {'objectId': object_id, 'style': style, 'textRange': text_range, 'fields': field_mask}
                if cell_location: req_body['cellLocation'] = cell_location
                requests.append({'updateParagraphStyle': req_body})
    return requests

# --- Main Sync Logic ---
def copy_slide_content(slides_service, master_slide_id, master_pres_id, dest_pres_id, dest_slide_id, master_slide_json, drive_service, docai_client, storage_client):
    requests = []
    try:
        dest_elements = slides_service.presentations().pages().get(presentationId=dest_pres_id, pageObjectId=dest_slide_id, fields='pageElements').execute().get('pageElements', [])
    except HttpError as e:
        logging.error(f"Could not read dest slide {dest_slide_id}: {e}"); return []

    master_elements = master_slide_json.get('pageElements', [])
    logging.debug(f"Phase 1: Checking {len(dest_elements)} destination elements for deletion.")
    for de in dest_elements:
        if not find_master_match(de, master_elements):
            requests.append({'deleteObject': {'objectId': de['objectId']}})
            logging.debug(f"ACTION: DELETE object {de['objectId']} (not found in master).")

    logging.debug(f"Phase 2 & 3: Checking {len(master_elements)} master elements for addition/update.")
    for me in master_elements:
        element_type = next((k for k in me if k not in ['objectId', 'size', 'transform']), None)
        if not element_type: continue

        # OCR should be called regardless of whether the image is new or existing
        if element_type == 'image':
            extracted_text = process_image_ocr(me, drive_service, docai_client, storage_client, slides_service, dest_pres_id)
            if extracted_text:
                logging.info(f"OCR_RESULT: Found text in image {me['objectId']}. Adding for validation.")
                keyword_metadata = process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, [{'text': {'textElements': [{'textRun': {'content': extracted_text}}]}}], table_format)
                GLOBAL_METADATA['Keyword_Values'].extend(keyword_metadata)

        matching_id = find_master_match(me, dest_elements)
        if matching_id: # Update existing element
            if element_type == 'shape':
                if 'shapeProperties' in me['shape']:
                    props = scrub_read_only_fields(me['shape']['shapeProperties'])
                    if (mask := _create_field_mask(props)): requests.append({'updateShapeProperties': {'objectId': matching_id, 'shapeProperties': props, 'fields': mask}})
                # Text content is preserved. Only update styling.
                requests.extend(generate_style_update_requests(matching_id, me))

            elif element_type == 'image':
                if 'imageProperties' in me['image']:
                    props = scrub_read_only_fields(me['image']['imageProperties'])
                    if (mask := _create_field_mask(props)): requests.append({'updateImageProperties': {'objectId': matching_id, 'imageProperties': props, 'fields': mask}})

            elif element_type == 'table':
                # Replicate table content and styles cell by cell
                requests.extend(_replicate_table_content_requests(matching_id, me))

        else: # Create new element
            new_id = uuid.uuid4().hex
            props = {'pageObjectId': dest_slide_id, 'size': me.get('size'), 'transform': me.get('transform')}

            if element_type == 'shape':
                requests.append({'createShape': {'objectId': new_id, 'shapeType': me['shape'].get('shapeType', 'TEXT_BOX'), 'elementProperties': props}})
                if 'shapeProperties' in me['shape']:
                    shape_props = scrub_read_only_fields(me['shape']['shapeProperties'])
                    if (mask := _create_field_mask(shape_props)): requests.append({'updateShapeProperties': {'objectId': new_id, 'shapeProperties': shape_props, 'fields': mask}})
                # New shapes are templates, do not add text. Only apply styling.
                requests.extend(generate_style_update_requests(new_id, me))

            elif element_type == 'image':
                requests.append({'createImage': {'url': me['image']['contentUrl'], 'elementProperties': props}})
                if 'imageProperties' in me['image']:
                    image_props = scrub_read_only_fields(me['image']['imageProperties'])
                    if (mask := _create_field_mask(image_props)): requests.append({'updateImageProperties': {'objectId': new_id, 'imageProperties': image_props, 'fields': mask}})

            elif element_type == 'table':
                requests.append({'createTable': {'objectId': new_id, 'rows': me['table']['rows'], 'columns': me['table']['columns'], 'elementProperties': props}})
                # Replicate table content and styles cell by cell
                requests.extend(_replicate_table_content_requests(new_id, me))

    return requests

# --- Keyword Validation Logic ---
def _get_table_headers(table_element, table_format="Format 1: Row 0/Col 0 Headers"):
    table_prop = table_element['table']
    rows, cols = table_prop['rows'], table_prop['columns']
    table_rows = table_prop['tableRows']
    row_headers, col_headers = [], []

    if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)":
        if rows >= 2:
            row_0_cells = table_rows[0]['tableCells']
            row_1_cells = table_rows[1]['tableCells']
            for c_idx in range(1, cols):
                h1 = get_text_content_from_element(row_0_cells[c_idx]).split('\n')[0].strip()
                h2 = get_text_content_from_element(row_1_cells[c_idx]).split('\n')[0].strip()
                col_headers.append(f"{h1} - {h2}" if h1 and h2 else h1 or h2 or f"[Empty Col {c_idx}]")
        for r_idx in range(2, rows):
            row_headers.append(get_text_content_from_element(table_rows[r_idx]['tableCells'][0]).strip() or f"[Empty Row {r_idx}]")
    else: # Format 1
        if rows > 0:
            for c_idx in range(1, cols):
                col_headers.append(get_text_content_from_element(table_rows[0]['tableCells'][c_idx]).strip() or f"[Empty Col {c_idx}]")
        for r_idx in range(1, rows):
            row_headers.append(get_text_content_from_element(table_rows[r_idx]['tableCells'][0]).strip() or f"[Empty Row {r_idx}]")
    return row_headers, col_headers

def extract_table_data_for_debug(table_element, dest_slide_id, table_format):
    row_headers, col_headers = _get_table_headers(table_element, table_format)
    data_start_row = 2 if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)" else 1
    for r_idx in range(data_start_row, table_element['table']['rows']):
        for c_idx in range(1, table_element['table']['columns']):
            cell = table_element['table']['tableRows'][r_idx]['tableCells'][c_idx]
            value = get_text_content_from_element(cell).strip()
            if value:
                row_label = row_headers[r_idx - data_start_row]
                col_keyword = col_headers[c_idx - 1]
                logging.info(f"TABLE_INTERSECTION: Slide {dest_slide_id}. Label='{row_label}' / Keyword='{col_keyword}' / Value='{value}'")

def process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, page_elements, table_format="Format 1: Row 0/Col 0 Headers"):
    """Processes text for keyword/value extraction and calculates fuzzy confidence."""
    metadata = []
    VALUE_PATTERN = re.compile(r'(\([\+\-]?[\d\.,]+%\)|[\+\-]?\$?[\d\.,]+[KMT]?\s?[\(]*[\+\-]?[\d\.,]+%\)*|[\+\-]?\$?[\d\.,]+[KMT]?|\([\+\-]?[\d\.,]+%\))', re.IGNORECASE)

    for element in page_elements:
        element_id = element.get('objectId', uuid.uuid4().hex)
        element_type_key = next((k for k in element.keys() if k not in ['objectId', 'size', 'transform', 'elementProperties']), None)

        if element_type_key == 'table':
            extract_table_data_for_debug(element, dest_slide_id, table_format)
            row_headers, col_headers = _get_table_headers(element, table_format)
            data_start_row = 2 if table_format == "Format 2: Dual Header (Rows 0 & 1 Combined)" else 1

            for r_idx in range(data_start_row, element['table']['rows']):
                for c_idx in range(1, element['table']['columns']):
                    try:
                        line = get_text_content_from_element(element['table']['tableRows'][r_idx]['tableCells'][c_idx]).strip()
                        if line:
                            value_match = VALUE_PATTERN.search(line)
                            if value_match:
                                metadata.append({
                                    'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                    'object_id': element_id, 'label': row_headers[r_idx - data_start_row], 'keyword': col_headers[c_idx - 1],
                                    'value': value_match.group(0).strip(), 'confidence': 1.0
                                })
                    except Exception as e:
                        logging.error(f"TABLE_ERROR: Failed to process cell R:{r_idx}, C:{c_idx}. Error: {e}")
            continue

        full_text = get_text_content_from_element(element)
        if not full_text.strip(): continue

        logging.debug(f"KEYWORD_ANALYSIS: Slide ID: {dest_slide_id}. Element {element_id}. Text: '{full_text.strip()[:50]}...'")

        for line_idx, line in enumerate(full_text.split('\n')):
            if line.strip():
                for keyword, alias in FIN_KEYWORDS.items():
                    confidence = fuzz.partial_ratio(keyword.lower(), line.lower()) / 100.0
                    logging.debug(f"   MATCH_ATTEMPT: Slide ID: {dest_slide_id}. Line {line_idx+1}: '{line.strip()[:30]}...' -> Term '{keyword}' Score: {confidence:.4f}")

                    if confidence >= KEYWORD_CONFIDENCE_THRESHOLD:
                        value_match = VALUE_PATTERN.search(line)
                        if value_match:
                            value = value_match.group(0).strip()
                            logging.info(f"   KEYWORD_FOUND: Slide ID: {dest_slide_id}. Keyword='{keyword}' Value='{value}' Confidence={round(confidence, 4)}")
                            metadata.append({
                                'type': 'Keyword', 'dest_id': dest_pres_id, 'page_id': dest_slide_id,
                                'object_id': element_id, 'label': "No Label Found", 'keyword': keyword, 'value': value,
                                'confidence': round(confidence, 4)
                            })
                            break
    return metadata

# --- Main Execution ---
def run_back_end(master_url, dest_url, table_format, status_output):
    logging.info("--- STARTING Run Cycle ---")
    GLOBAL_METADATA['Keyword_Values'] = []
    total_inconsistencies = 0

    slides_service, drive_service, docai_client, storage_client = get_service()
    if not all([slides_service, drive_service, docai_client, storage_client]):
        with status_output: print("❌ Error: Authentication failed.")
        return LOG_FILENAME, -1

    master_id = extract_id_from_url(master_url)
    dest_files = find_destination_files(drive_service, dest_url)
    if not dest_files:
        with status_output: print("❌ Error: No destination files found.")
        return LOG_FILENAME, -1

    _, master_pres = get_slide_page_elements(slides_service, master_id)
    master_slides = master_pres.get('slides', [])
    if not master_slides:
        with status_output: print("❌ Error: Master presentation is empty.")
        return LOG_FILENAME, -1

    for dest_file in dest_files:
        with status_output: print(f"\nProcessing: **{dest_file['name']}**")
        _, dest_pres = get_slide_page_elements(slides_service, dest_file['id'])
        dest_slides = dest_pres.get('slides', [])

        for i, master_slide in enumerate(master_slides):
            logging.info(f"Processing Slide {i+1}/{len(master_slides)} for {dest_file['name']}")
            if i >= len(dest_slides):
                logging.warning(f"Skipping slide {i+1} as it does not exist in destination.")
                continue

            dest_slide = dest_slides[i]
            copy_reqs = copy_slide_content(slides_service, master_slide['objectId'], master_id, dest_file['id'], dest_slide['objectId'], master_slide, drive_service, docai_client, storage_client)

            validation_meta = process_text_bearing_objects(slides_service, dest_file['id'], dest_slide['objectId'], dest_slide.get('pageElements', []), table_format)
            GLOBAL_METADATA['Keyword_Values'].extend(validation_meta)

            if copy_reqs:
                try:
                    slides_service.presentations().batchUpdate(presentationId=dest_file['id'], body={'requests': copy_reqs}).execute()
                except HttpError as e:
                    logging.error(f"BatchUpdate failed on {dest_file['name']}: {e}")
                    total_inconsistencies += 1

    # Final consistency check would go here.

    logging.info("--- Run Cycle Complete ---")
    return LOG_FILENAME, 0 if total_inconsistencies == 0 else -1
def _replicate_table_content_requests(table_id, master_table_element):
    """
    Generates requests to replicate the text content and style of each cell in a table.
    This is a dedicated function to handle the cell-by-cell operations required for tables.
    """
    requests = []
    table_prop = master_table_element.get('table', {})
    rows = table_prop.get('tableRows', [])

    for r_idx, row in enumerate(rows):
        for c_idx, cell in enumerate(row.get('tableCells', [])):
            cell_loc = {'rowIndex': r_idx, 'columnIndex': c_idx}

            # Text content is preserved. Only apply styling.
            # We pass the cell itself as the 'master_element' because it contains the 'text' object.
            requests.extend(generate_style_update_requests(table_id, cell, cell_location=cell_loc))

    return requests
