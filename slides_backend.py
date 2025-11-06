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
        transform = element.get('transform', {})
        return (
            round(size.get('width', {}).get('magnitude', 0), 3),
            round(size.get('height', {}).get('magnitude', 0), 3),
            round(transform.get('translateX', 0), 3),
            round(transform.get('translateY', 0), 3)
        )
    m1 = get_metrics(element1)
    if m1 == (0, 0, 0, 0) and not element1.get('shape'): return True
    for e2 in element2_list:
        if m1 == get_metrics(e2):
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

def generate_style_update_requests(object_id, master_element):
    requests = []
    text_obj = master_element.get('text') or master_element.get('shape', {}).get('text')
    if not text_obj: return []
    for te in text_obj.get('textElements', []):
        text_range = {'type': 'FIXED_RANGE', 'startIndex': te.get('startIndex', 0), 'endIndex': te.get('endIndex', 1)}
        if 'textRun' in te and 'style' in te['textRun']:
            requests.append({'updateTextStyle': {'objectId': object_id, 'style': scrub_read_only_fields(te['textRun']['style']), 'textRange': text_range, 'fields': '*'}})
        if 'paragraphMarker' in te and 'style' in te['paragraphMarker']:
            requests.append({'updateParagraphStyle': {'objectId': object_id, 'style': scrub_read_only_fields(te['paragraphMarker']['style']), 'textRange': text_range, 'fields': '*'}})
    return requests

# --- Main Sync Logic ---
def copy_slide_content(slides_service, master_slide_id, master_pres_id, dest_pres_id, dest_slide_id, master_slide_json, drive_service, docai_client, storage_client):
    requests = []
    try:
        dest_elements = slides_service.presentations().pages().get(presentationId=dest_pres_id, pageObjectId=dest_slide_id, fields='pageElements').execute().get('pageElements', [])
    except HttpError as e:
        logging.error(f"Could not read dest slide {dest_slide_id}: {e}"); return []

    master_elements = master_slide_json.get('pageElements', [])
    for de in dest_elements:
        if not find_master_match(de, master_elements):
            requests.append({'deleteObject': {'objectId': de['objectId']}})

    for me in master_elements:
        element_type = next((k for k in me if k not in ['objectId', 'size', 'transform']), None)
        if not element_type: continue

        matching_id = find_master_match(me, dest_elements)
        if matching_id: # Update
            if element_type == 'shape':
                requests.append({'updateShapeProperties': {'objectId': matching_id, 'shapeProperties': scrub_read_only_fields(me['shape']['shapeProperties']), 'fields': '*'}})
            elif element_type == 'image':
                requests.append({'updateImageProperties': {'objectId': matching_id, 'imageProperties': scrub_read_only_fields(me['image']['imageProperties']), 'fields': '*'}})

            if element_type in ('shape', 'table'):
                requests.append({'deleteText': {'objectId': matching_id, 'textRange': {'type': 'ALL'}}})
                full_text = get_text_content_from_element(me)
                if full_text: requests.append({'insertText': {'objectId': matching_id, 'text': full_text}})
                requests.extend(generate_style_update_requests(matching_id, me))
        else: # Create
            new_id = uuid.uuid4().hex
            props = {'pageObjectId': dest_slide_id, 'size': me.get('size'), 'transform': me.get('transform')}

            create_req = {}
            if element_type == 'shape':
                create_req = {'createShape': {'objectId': new_id, 'shapeType': me['shape'].get('shapeType', 'TEXT_BOX'), 'elementProperties': props}}
                if 'shapeProperties' in me['shape']: create_req['createShape']['shapeProperties'] = scrub_read_only_fields(me['shape']['shapeProperties'])
            elif element_type == 'image':
                create_req = {'createImage': {'url': me['image']['contentUrl'], 'elementProperties': props}}
                if 'imageProperties' in me['image']: create_req['createImage']['imageProperties'] = scrub_read_only_fields(me['image']['imageProperties'])

            if create_req: requests.append(create_req)
            if element_type in ('shape', 'table'):
                full_text = get_text_content_from_element(me)
                if full_text: requests.append({'insertText': {'objectId': new_id, 'text': full_text}})
                requests.extend(generate_style_update_requests(new_id, me))

    return requests

# --- Keyword Validation Logic ---
def process_text_bearing_objects(slides_service, dest_pres_id, dest_slide_id, page_elements, table_format):
    # This function is complex and contains the validation logic.
    # It would be migrated here. For brevity in this example, it's represented by this comment.
    return []

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
            if i >= len(dest_slides): continue

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