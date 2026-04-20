import os
import shutil
import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from uuid import uuid4

import fitz  # PyMuPDF
import pytesseract
from docx import Document
from flask import Flask, jsonify, request
from PIL import Image
from supabase import Client, create_client
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "/tmp/pdf_search")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

VALID_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "bmp", "gif", "docx"]
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "gif"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_PUBLISHABLE_KEY")
)
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "ocr_results")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "ocr-uploads")
SUPABASE_UPLOAD_PREFIX = os.getenv("SUPABASE_UPLOAD_PREFIX", "documents")
SUPABASE_USE_REST_FALLBACK = bool(SUPABASE_KEY and SUPABASE_KEY.startswith("sb_publishable_"))

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY and not SUPABASE_USE_REST_FALLBACK:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        supabase = None

TESSERACT_AVAILABLE = bool(shutil.which("tesseract"))


@app.route("/")
def index():
    return jsonify(
        {
            "status": "ok",
            "message": "OCR API is running",
            "supabase_configured": bool(supabase),
        }
    )


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    text_chunks = [para.text for para in doc.paragraphs if para.text]

    image_text_chunks = []
    for rel in doc.part.rels.values():
        if "image" not in rel.target_ref:
            continue
        if not TESSERACT_AVAILABLE:
            continue
        img_data = rel.target_part._blob
        image = Image.open(BytesIO(img_data))
        image_text = pytesseract.image_to_string(image)
        if image_text.strip():
            image_text_chunks.append(image_text)

    return "\n".join(text_chunks + image_text_chunks)


def process_image(file_path: str) -> str:
    if not TESSERACT_AVAILABLE:
        raise RuntimeError("Tesseract binary is not available in this runtime.")
    image = Image.open(file_path).convert("L")
    return pytesseract.image_to_string(image)


def extract_text_from_pdf_main(pdf_path: str) -> str:
    text_chunks = []
    doc = fitz.open(pdf_path)

    for page in doc:
        page_text = page.get_text("text")
        if page_text and page_text.strip():
            text_chunks.append(page_text)
            continue

        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        if not TESSERACT_AVAILABLE:
            continue
        ocr_text = pytesseract.image_to_string(img)
        if ocr_text.strip():
            text_chunks.append(ocr_text)

    return "\n".join(text_chunks)


def ensure_supabase_bucket_exists() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    bucket_info_url = f"{SUPABASE_URL}/storage/v1/bucket/{SUPABASE_BUCKET}"
    check_req = Request(
        bucket_info_url,
        method="GET",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    try:
        with urlopen(check_req):
            return
    except HTTPError as exc:
        if exc.code not in (400, 404):
            err_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase bucket check failed: {exc.code} {exc.reason} {err_body}") from exc

    create_bucket_url = f"{SUPABASE_URL}/storage/v1/bucket"
    create_payload = {
        "id": SUPABASE_BUCKET,
        "name": SUPABASE_BUCKET,
        "public": True,
    }
    create_req = Request(
        create_bucket_url,
        data=json.dumps(create_payload).encode("utf-8"),
        method="POST",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(create_req):
            return
    except HTTPError as exc:
        if exc.code == 409:
            return
        err_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase bucket create failed: {exc.code} {exc.reason} {err_body}") from exc


def upload_file_to_supabase_storage(file_path: str, file_name: str, content_type: str) -> tuple[Optional[str], Optional[str]]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None, None
    ensure_supabase_bucket_exists()

    object_name = (
        f"{SUPABASE_UPLOAD_PREFIX}/"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid4().hex[:8]}-{file_name}"
    )
    safe_content_type = content_type or "application/octet-stream"

    with open(file_path, "rb") as file_obj:
        file_bytes = file_obj.read()

    if supabase:
        file_options = {"content-type": safe_content_type, "upsert": "true"}
        bucket = supabase.storage.from_(SUPABASE_BUCKET)
        try:
            bucket.upload(path=object_name, file=file_bytes, file_options=file_options)
        except TypeError:
            bucket.upload(object_name, file_bytes, file_options)

        public_url = None
        try:
            public_url = bucket.get_public_url(object_name)
        except Exception:
            public_url = None
        return object_name, public_url

    encoded_object_name = quote(object_name, safe="/")
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{encoded_object_name}"
    req = Request(
        upload_url,
        data=file_bytes,
        method="POST",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": safe_content_type,
            "x-upsert": "true",
        },
    )

    try:
        with urlopen(req):
            pass
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase storage upload failed: {exc.code} {exc.reason} {err_body}") from exc

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{encoded_object_name}"

    return object_name, public_url


def save_ocr_result(
    file_name: str,
    file_type: str,
    extracted_text: str,
    storage_path: Optional[str] = None,
    storage_url: Optional[str] = None,
) -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"saved": False, "error": "Supabase is not configured"}

    payload = {
        "file_name": file_name,
        "file_type": file_type,
        "extracted_text": extracted_text,
    }
    if storage_path:
        payload["storage_path"] = storage_path
    if storage_url:
        payload["storage_url"] = storage_url

    base_payload = {
        "file_name": file_name,
        "file_type": file_type,
        "extracted_text": extracted_text,
    }

    if supabase:
        try:
            supabase.table(SUPABASE_TABLE).insert(payload).execute()
        except Exception:
            # Backward compatible insert when storage columns do not exist yet.
            try:
                supabase.table(SUPABASE_TABLE).insert(base_payload).execute()
            except Exception as exc:
                msg = str(exc)
                if "PGRST205" in msg or "schema cache" in msg:
                    return {
                        "saved": False,
                        "error": f"Table '{SUPABASE_TABLE}' not found. Run SQL setup to create it.",
                    }
                return {"saved": False, "error": msg}
        return {"saved": True, "error": None}

    insert_url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    req = Request(
        insert_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    try:
        with urlopen(req):
            return {"saved": True, "error": None}
    except HTTPError as exc:
        # Backward compatible insert when storage columns do not exist yet.
        fallback_req = Request(
            insert_url,
            data=json.dumps(base_payload).encode("utf-8"),
            method="POST",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        try:
            with urlopen(fallback_req):
                return {"saved": True, "error": None}
        except HTTPError as fallback_exc:
            err_body = fallback_exc.read().decode("utf-8", errors="ignore")
            first_err = exc.read().decode("utf-8", errors="ignore")
            raw_err = f"Supabase insert failed: {fallback_exc.code} {fallback_exc.reason} {err_body or first_err}"
            if "PGRST205" in raw_err or "schema cache" in raw_err:
                return {
                    "saved": False,
                    "error": f"Table '{SUPABASE_TABLE}' not found. Run SQL setup to create it.",
                }
            return {"saved": False, "error": raw_err}


def highlight_matching_text(pdf_path: str, query: str) -> Optional[str]:
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None

    hits = 0
    for page in doc:
        matches = page.search_for(query)
        for match in matches:
            annot = page.add_highlight_annot(match)
            annot.update()
            hits += 1

    if hits == 0:
        doc.close()
        return None

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    highlighted_name = f"{base_name}-highlighted-{uuid4().hex[:8]}.pdf"
    highlighted_path = os.path.join(app.config["UPLOAD_FOLDER"], highlighted_name)
    doc.save(highlighted_path)
    doc.close()
    return highlighted_path


def search_rows(query: str) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase is not configured")

    if supabase:
        response = (
            supabase.table(SUPABASE_TABLE)
            .select("file_name,page_num,extracted_text,storage_path,storage_url")
            .ilike("extracted_text", f"%{query}%")
            .limit(100)
            .execute()
        )
        return response.data or []

    encoded_pattern = quote_plus(f"*{query}*")
    search_url = (
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        f"?select=file_name,page_num,extracted_text,storage_path,storage_url"
        f"&extracted_text=ilike.{encoded_pattern}&limit=100"
    )
    req = Request(
        search_url,
        method="GET",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    with urlopen(req) as res:
        return json.loads(res.read().decode("utf-8"))


def ensure_local_pdf_for_highlight(pdf_filename: str, storage_path: Optional[str], storage_url: Optional[str]) -> Optional[str]:
    local_path = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
    if os.path.exists(local_path):
        return local_path

    if storage_url:
        try:
            with urlopen(storage_url) as res:
                file_bytes = res.read()
            with open(local_path, "wb") as out:
                out.write(file_bytes)
            return local_path
        except Exception:
            pass

    if storage_path and SUPABASE_URL and SUPABASE_KEY:
        encoded_path = quote(storage_path, safe="/")
        object_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{encoded_path}"
        req = Request(
            object_url,
            method="GET",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
        )
        try:
            with urlopen(req) as res:
                file_bytes = res.read()
            with open(local_path, "wb") as out:
                out.write(file_bytes)
            return local_path
        except Exception:
            return None

    return None


def build_search_response(query: str):
    results = []
    highlighted_files = []

    rows = search_rows(query)
    for row in rows:
        pdf_filename = row.get("file_name") or row.get("pdf_filename")
        page_num = row.get("page_num")
        extracted_text = (row.get("extracted_text") or "").strip()

        results.append(
            {
                "pdf": pdf_filename,
                "page": (int(page_num) + 1) if isinstance(page_num, int) else None,
                "text": extracted_text,
                "storage_path": row.get("storage_path"),
                "storage_url": row.get("storage_url"),
            }
        )

        if not pdf_filename:
            continue
        if not pdf_filename.lower().endswith(".pdf"):
            continue

        file_path = ensure_local_pdf_for_highlight(
            pdf_filename=pdf_filename,
            storage_path=row.get("storage_path"),
            storage_url=row.get("storage_url"),
        )
        if not file_path:
            continue

        highlighted_pdf_path = highlight_matching_text(file_path, query)
        if highlighted_pdf_path:
            highlighted_files.append(os.path.basename(highlighted_pdf_path))

    if not results:
        return jsonify({"message": "No results found"}), 200

    return jsonify({"results": results, "highlighted_files": highlighted_files}), 200


@app.route("/api/search", methods=["GET"])
def search_pdfss():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        return build_search_response(query)
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        return jsonify({"error": f"Error occurred while searching: {exc.code} {exc.reason} {err_body}"}), 500
    except Exception as exc:
        return jsonify({"error": f"Error occurred while searching: {str(exc)}"}), 500


@app.route("/api/search", methods=["POST"])
def search_pdfs():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        return build_search_response(query)
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        return jsonify({"error": f"Error occurred while searching: {exc.code} {exc.reason} {err_body}"}), 500
    except Exception as exc:
        return jsonify({"error": f"Error occurred while searching: {str(exc)}"}), 500


@app.route("/api/documentsOCR", methods=["POST"])
def api_documents_ocr():
    if "files" not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No selected files"}), 400

    text_results = []
    file_types = []
    db_warnings = []

    for file in files:
        raw_name = file.filename or ""
        file_name = secure_filename(raw_name)
        if not file_name:
            return jsonify({"error": "Invalid file name"}), 400

        file_extension = file_name.rsplit(".", 1)[1].lower() if "." in file_name else ""
        if file_extension not in VALID_EXTENSIONS:
            return jsonify(
                {
                    "error": "Invalid file format. Allowed: pdf, jpg, jpeg, png, bmp, gif, docx"
                }
            ), 400

        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file_name)
        file.save(file_path)
        content_type = file.content_type or "application/octet-stream"

        try:
            storage_path, storage_url = upload_file_to_supabase_storage(
                file_path=file_path,
                file_name=file_name,
                content_type=content_type,
            )

            if file_extension == "pdf":
                file_type = "PDF"
                text = extract_text_from_pdf_main(file_path)
            elif file_extension in IMAGE_EXTENSIONS:
                file_type = "Image"
                text = process_image(file_path)
            else:
                file_type = "DOCX"
                text = extract_text_from_docx(file_path)

            if not text.strip():
                return jsonify({"error": f"No text found in {file_name}"}), 400

            text_results.append(text)
            file_types.append(file_type)
            db_status = save_ocr_result(
                file_name=file_name,
                file_type=file_type,
                extracted_text=text,
                storage_path=storage_path,
                storage_url=storage_url,
            )
            if not db_status.get("saved"):
                db_warnings.append(
                    {"file_name": file_name, "warning": db_status.get("error", "Unknown DB error")}
                )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    combined_text = "\n\n".join(text_results)
    return jsonify({"text": combined_text, "file_types": file_types, "db_warnings": db_warnings}), 200


@app.route("/api/ocr-results", methods=["GET"])
def view_ocr_results():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"error": "Supabase is not configured"}), 503

    if supabase:
        response = supabase.table(SUPABASE_TABLE).select("*").limit(100).execute()
        return jsonify({"data": response.data}), 200

    select_url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?select=*&limit=100"
    req = Request(
        select_url,
        method="GET",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    try:
        with urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        return jsonify({"error": f"Supabase query failed: {exc.code} {exc.reason} {err_body}"}), 500

    return jsonify({"data": data}), 200


if __name__ == "__main__":
    app.run(debug=True)
