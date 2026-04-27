import hashlib
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from urllib.error import HTTPError

from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

from pdf_extract import VALID_EXTENSIONS, extract_file_content
from supabase import SupabaseRepository

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

repo = SupabaseRepository()

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
    if not file_name:
        return None

    if not repo.configured:
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

    combined_text = "\n\n".join(text_results)
    return jsonify(
        {
            "text": combined_text,
            "file_types": file_types,
            "tables": extracted_tables,
            "cache_hits": cache_hits,
            "db_warnings": db_warnings,
        }
    ), 200


@app.route("/api/convert", methods=["POST"])
def api_convert():
    payload = request.get_json(silent=True) or {}
    req = _parse_convert_request(payload)

    if not req.target_language:
        return jsonify({"error": "target_language is required (ISO 639-1, e.g. 'ta', 'hi', 'fr', 'es')"}), 400

    source_text = resolve_text(req.filename, req.text)
    if not source_text.strip():
        return jsonify({"error": "No text available to translate. Provide `text` or valid `filename`."}), 400

    try:
        translated = translate_text(source_text, req.target_language)
    except Exception as exc:
        return jsonify({"error": f"Translation failed: {str(exc)}"}), 500

    return jsonify(
        {
            "success": True,
            "source_filename": req.filename or None,
            "target_language": req.target_language,
            "translated_text": translated,
        }
    ), 200


@app.route("/api/convert/audio", methods=["POST"])
def api_convert_audio():
    if not gTTS:
        return jsonify({"error": "Audio dependency missing. Install 'gTTS'."}), 500

    payload = request.get_json(silent=True) or {}
    req = _parse_audio_request(payload)

    source_text = resolve_text(req.filename, req.text)
    if not source_text.strip():
        return jsonify({"error": "No text available to convert. Provide `text` or valid `filename`."}), 400

    final_text = source_text
    language = req.target_language or "en"

    if req.target_language:
        try:
            final_text = translate_text(source_text, req.target_language)
        except Exception as exc:
            return jsonify({"error": f"Translation failed: {str(exc)}"}), 500

    try:
        tts = gTTS(text=final_text, lang=language)
        audio_buffer = BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)

        stem = os.path.splitext(secure_filename(req.filename))[0] if req.filename else "text"
        text_hash = hashlib.md5(final_text[:400].encode("utf-8", errors="ignore")).hexdigest()[:8]
        audio_filename = f"{stem or 'text'}_{language}_{text_hash}.mp3"
    except Exception as exc:
        return jsonify({"error": f"Audio generation failed: {str(exc)}"}), 500

    return send_file(
        audio_buffer,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=audio_filename,
    )


@app.route("/api/ocr-results", methods=["GET"])
def view_ocr_results():
    if not repo.configured:
        return jsonify({"error": "Supabase is not configured"}), 503

    try:
        data = repo.fetch_recent_results(limit=100)
        return jsonify({"data": data}), 200
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        return jsonify({"error": f"Supabase query failed: {exc.code} {exc.reason} {err_body}"}), 500


@app.route("/api/search", methods=["GET"])
def search_cached_documents():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    if not repo.configured:
        return jsonify({"error": "Supabase is not configured"}), 503

    try:
        rows = repo.search_rows(query, limit=100)
    except Exception as exc:
        return jsonify({"error": f"Search failed: {str(exc)}"}), 500

    if not rows:
        return jsonify({"message": "No results found"}), 200

    results = []
    for row in rows:
        text = (row.get("extracted_text") or "")
        results.append({"pdf": row.get("file_name"), "text": text[:300]})

    return jsonify({"results": results}), 200


if __name__ == "__main__":
    app.run(debug=False)
