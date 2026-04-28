import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from pdf_extract import VALID_EXTENSIONS, extract_file_content,extract_pdf_content
from search_service import SearchService
from supabase_repo import SupabaseRepository
from dotenv import load_dotenv


if load_dotenv:
    load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
CORS(app, resources={r"/api/*": {"origins": "*"}})

repo = SupabaseRepository()
search_service = SearchService(repo)

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

try:
    from gtts import gTTS
except Exception:
    gTTS = None


@dataclass
class ConvertRequest:
    target_language: str
    text: str = ""
    filename: str = ""


@dataclass
class AudioRequest:
    text: str = ""
    filename: str = ""
    target_language: str = ""


def get_cached_document(file_name: str) -> dict | None:
    if not file_name or not repo.configured:
        return None
    try:
        row = repo.fetch_latest_by_filename(file_name)
    except Exception:
        return None
    if not row:
        return None
    return {
        "file_name": row.get("file_name") or file_name,
        "file_type": row.get("file_type") or "Unknown",
        "extracted_text": row.get("extracted_text") or "",
    }


def process_document(file_storage, file_name: str, file_extension: str) -> dict:
    tmp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as tmp_file:
            file_storage.save(tmp_file.name)
            tmp_file_path = tmp_file.name

        storage_path, storage_url = repo.upload_file(
            file_path=tmp_file_path,
            file_name=file_name,
            content_type=file_storage.content_type or "application/octet-stream",
        )

        extracted = extract_file_content(tmp_file_path, file_extension)
        return {
            "file_name": file_name,
            "file_type": extracted.get("file_type", "Unknown"),
            "text": extracted.get("text", ""),
            "tables": extracted.get("tables", []),
            "storage_path": storage_path,
            "storage_url": storage_url,
        }
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.remove(tmp_file_path)
            except OSError:
                pass


def resolve_text(filename: str, text: str) -> str:
    direct_text = (text or "").strip()
    if direct_text:
        return direct_text
    cached = get_cached_document((filename or "").strip())
    if cached:
        return cached.get("extracted_text", "") or ""
    return ""


def _chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    content = (text or "").strip()
    if not content:
        return []
    chunks = []
    buffer = ""
    for paragraph in content.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + max_chars])
                start += max_chars
            continue
        candidate = paragraph if not buffer else f"{buffer}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def translate_text(text: str, target_language: str) -> str:
    if not GoogleTranslator:
        raise RuntimeError("Translation dependency missing. Install 'deep-translator'.")
    target = (target_language or "").strip().lower()
    if not target:
        raise ValueError("target_language is required")
    parts = _chunk_text(text)
    if not parts:
        return ""
    translator = GoogleTranslator(source="auto", target=target)
    translated_parts = [translator.translate(part) for part in parts]
    return "\n\n".join(translated_parts)


def text_to_audio_mp3_bytes(text: str, language: str) -> BytesIO:
    if not gTTS:
        raise RuntimeError("Audio dependency missing. Install 'gTTS'.")
    audio_buffer = BytesIO()
    tts = gTTS(text=text, lang=language or "en")
    tts.write_to_fp(audio_buffer)
    audio_buffer.seek(0)
    return audio_buffer


def _parse_convert_request(payload: dict) -> ConvertRequest:
    return ConvertRequest(
        target_language=(payload.get("target_language") or "").strip(),
        text=(payload.get("text") or "").strip(),
        filename=(payload.get("filename") or "").strip(),
    )

def _parse_audio_request(payload: dict) -> AudioRequest:
    return AudioRequest(
        text=(payload.get("text") or "").strip(),
        filename=(payload.get("filename") or "").strip(),
        target_language=(payload.get("target_language") or "").strip().lower(),
    )

@app.route("/")
def index():
    return jsonify(
        {
            "status": "ok",
            "message": "OCR API is running",
            "cache": "supabase",
            "supabase_configured": repo.configured,
        }
    )

@app.route("/api/documentsOCR", methods=["POST"])
def api_documents_ocr():
    if not repo.configured:
        return jsonify({"error": "Supabase is required. Configure SUPABASE_URL and key variables."}), 503
    if "files" not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No selected files"}), 400

    text_results = []
    file_types = []
    extracted_tables = []
    cache_hits = []
    db_warnings = []

    for file in files:
        raw_name = file.filename or ""
        file_name = secure_filename(raw_name)
        if not file_name:
            return jsonify({"error": "Invalid file name"}), 400

        file_extension = file_name.rsplit(".", 1)[1].lower() if "." in file_name else ""
        if file_extension not in VALID_EXTENSIONS:
            return jsonify({"error": "Invalid file format. Allowed: pdf, jpg, jpeg, png, bmp, gif, docx"}), 400

        cached = get_cached_document(file_name)
        if cached and (cached.get("extracted_text") or "").strip():
            text_results.append(cached["extracted_text"])
            file_types.append(cached["file_type"])
            cache_hits.append(file_name)
            continue

        try:
            processed = process_document(file, file_name, file_extension)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        text = processed["text"]
        file_type = processed["file_type"]
        tables = processed.get("tables", [])
        if not text.strip():
            return jsonify({"error": f"No text found in {file_name}"}), 400

        db_status = repo.save_ocr_result(
            file_name=file_name,
            file_type=file_type,
            extracted_text=text,
            storage_path=processed.get("storage_path"),
            storage_url=processed.get("storage_url"),
        )
        if not db_status.get("saved"):
            db_warnings.append({"file_name": file_name, "warning": db_status.get("error", "Unknown DB error")})

        text_results.append(text)
        file_types.append(file_type)
        if tables:
            extracted_tables.append({"file_name": file_name, "tables": tables})

    return jsonify(
        {
            "text": "\n\n".join(text_results),
            "file_types": file_types,
            "tables": extracted_tables,
            "cache_hits": cache_hits,
            "db_warnings": db_warnings,
        }
    ), 200

# API Routes
@app.route('/api/convert', methods=['POST'])
def api_convert():
    payload = request.get_json(silent=True) or {}
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    text = (payload.get("extracted_text") or "").strip()
    target_language = (payload.get("language") or "").strip().lower()

    if not text:
        return jsonify({"error": "extracted_text is required"}), 400

    if target_language:
        try:
            output_text = translate_text(text, target_language)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
    else:
        output_text = text

    return jsonify({"text": output_text}), 200

@app.route("/api/search", methods=["GET"])
def api_search():
    if not repo.configured:
        return jsonify({"error": "Supabase is required. Configure SUPABASE_URL and key variables."}), 503
    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"error": "No search query provided"}), 400
    try:
        payload = search_service.build_search_payload(query)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(payload), 200


@app.route('/api/upload_pdfs', methods=['POST'])
def upload_pdfs():
    if not repo.configured:
        return jsonify({"error": "Supabase is required. Configure SUPABASE_URL and key variables."}), 503

    if 'files' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    files = request.files.getlist('files')
    if not files or all((f.filename or "").strip() == "" for f in files):
        return jsonify({'error': 'No files selected'}), 400

    processed_files = []
    db_warnings = []

    for file in files:
        raw_name = file.filename or ""
        file_name = secure_filename(raw_name)
        if not file_name:
            continue

        file_extension = file_name.rsplit(".", 1)[1].lower() if "." in file_name else ""
        if file_extension != "pdf":
            continue

        try:
            processed = process_document(file, file_name, file_extension)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        text = (processed.get("text") or "").strip()
        if not text:
            continue

        db_status = repo.save_ocr_result(
            file_name=file_name,
            file_type=processed.get("file_type", "PDF"),
            extracted_text=text,
            storage_path=processed.get("storage_path"),
            storage_url=processed.get("storage_url"),
        )
        if not db_status.get("saved"):
            db_warnings.append({"file_name": file_name, "warning": db_status.get("error", "Unknown DB error")})

        processed_files.append(
            {
                "file_name": file_name,
                "storage_url": processed.get("storage_url"),
                "file_type": processed.get("file_type", "PDF"),
            }
        )

    if not processed_files:
        return jsonify({'error': 'No valid PDF files with extractable text'}), 400

    return jsonify(
        {
            'message': 'PDFs uploaded and processed successfully',
            'processed_files': processed_files,
            'db_warnings': db_warnings,
        }
    ), 200

if __name__ == "__main__":
    app.run(debug=False)
