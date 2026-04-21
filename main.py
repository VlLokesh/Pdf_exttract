import os
from urllib.error import HTTPError

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from pdf_extract import VALID_EXTENSIONS, extract_file_content
from search_service import SearchService
from supabase import SupabaseRepository

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "/tmp/pdf_search")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

repo = SupabaseRepository()
search_service = SearchService(repo=repo, upload_folder=app.config["UPLOAD_FOLDER"])


@app.route("/")
def index():
    return jsonify(
        {
            "status": "ok",
            "message": "OCR API is running",
            "supabase_configured": repo.configured,
        }
    )


@app.route("/api/search", methods=["GET"])
def search_pdfss():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        payload = search_service.build_search_payload(query)
        return jsonify(payload), 200
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
        payload = search_service.build_search_payload(query)
        return jsonify(payload), 200
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
    extracted_tables = []
    db_warnings = []

    for file in files:
        raw_name = file.filename or ""
        file_name = secure_filename(raw_name)
        if not file_name:
            return jsonify({"error": "Invalid file name"}), 400

        file_extension = file_name.rsplit(".", 1)[1].lower() if "." in file_name else ""
        if file_extension not in VALID_EXTENSIONS:
            return jsonify({"error": "Invalid file format. Allowed: pdf, jpg, jpeg, png, bmp, gif, docx"}), 400

        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file_name)
        file.save(file_path)
        content_type = file.content_type or "application/octet-stream"

        try:
            storage_path, storage_url = repo.upload_file(
                file_path=file_path,
                file_name=file_name,
                content_type=content_type,
            )

            extracted = extract_file_content(file_path, file_extension)
            text = extracted["text"]
            file_type = extracted["file_type"]
            tables = extracted.get("tables", [])

            if not text.strip():
                return jsonify({"error": f"No text found in {file_name}"}), 400

            text_results.append(text)
            file_types.append(file_type)
            if tables:
                extracted_tables.append({"file_name": file_name, "tables": tables})

            db_status = repo.save_ocr_result(
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
    return jsonify(
        {
            "text": combined_text,
            "file_types": file_types,
            "tables": extracted_tables,
            "db_warnings": db_warnings,
        }
    ), 200


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


if __name__ == "__main__":
    app.run(debug=True)