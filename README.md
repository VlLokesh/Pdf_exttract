# OCR API (Flask + Supabase) for Vercel

This project provides a Flask API to extract text from uploaded documents and store OCR/audio outputs in Supabase.

## What the code does

- Accepts file uploads at `POST /api/documentsOCR`
- Supports:
  - `pdf`
  - `jpg`, `jpeg`, `png`, `bmp`, `gif`
  - `docx`
- Extracts text:
  - PDF: first tries embedded text (`PyMuPDF`), falls back to OCR per page
  - Images: OCR with `pytesseract`
  - DOCX: paragraph text + OCR for embedded images
- Stores each extracted result in Supabase table
- Uploads original documents to Supabase Storage bucket
- Returns recent stored rows at `GET /api/ocr-results`

Core app is in [main.py](/C:/Users/Administrator.DESKTOP-SJ9U4FH/PycharmProjects/PDF/main.py).  
Vercel entrypoint is [api/index.py](/C:/Users/Administrator.DESKTOP-SJ9U4FH/PycharmProjects/PDF/api/index.py).

## Project files

- `main.py` - Flask app and OCR logic
- `api/index.py` - Vercel Python function entry
- `requirements.txt` - production dependencies
- `requirements-dev.txt` - local/dev extras
- `vercel.json` - Vercel routing/build config
- `.env.example` - environment template

## Environment variables

Create `.env` (or set in Vercel Project Settings -> Environment Variables):

```env
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_for_server_writes
SUPABASE_KEY=your_supabase_anon_or_service_role_key_or_sb_publishable_key
SUPABASE_TABLE=ocr_results
SUPABASE_BUCKET=ocr-uploads
SUPABASE_UPLOAD_PREFIX=documents
```

Supabase is required for this API. Without `SUPABASE_URL` and a valid key, OCR/audio endpoints return `503`.

Key handling:

- `SUPABASE_SERVICE_ROLE_KEY` (recommended): preferred automatically for server-side DB/Storage writes.
- JWT key (anon/service-role): uses `supabase-py` client directly.
- `sb_publishable_...` key: automatically uses REST/Storage fallback mode (compatible with this project code).

## Supabase table schema

Create table `ocr_results` (or set your custom `SUPABASE_TABLE`) with columns:

- `id` (optional, auto-generated)
- `file_name` (text)
- `file_type` (text)
- `extracted_text` (text)
- `storage_path` (text, optional but recommended)
- `storage_url` (text, optional)
- `created_at` (optional timestamp, default now)

Create a Supabase Storage bucket named `ocr-uploads` (or your custom `SUPABASE_BUCKET`).

Quick setup:

- Run [supabase_setup.sql](/C:/Users/Administrator.DESKTOP-SJ9U4FH/PycharmProjects/PDF/supabase_setup.sql) in Supabase SQL Editor to create the `ocr_results` table.
- If table already exists and columns are missing, run [supabase_migration_add_missing_columns.sql](/C:/Users/Administrator.DESKTOP-SJ9U4FH/PycharmProjects/PDF/supabase_migration_add_missing_columns.sql).

## Local run

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run Flask app:

```bash
python main.py
```

3. API base URL:

```text
http://127.0.0.1:5000
```

## Vercel deployment

1. Push this project to a Git repository.
2. Import the repo in Vercel.
3. Set environment variables in Vercel dashboard.
4. Deploy.

`vercel.json` routes all requests to `api/index.py`.

## API usage

### 1) Health check

- Method: `GET`
- Path: `/`

Example response:

```json
{
  "status": "ok",
  "message": "OCR API is running",
  "cache": "supabase",
  "supabase_configured": true
}
```

### 2) Extract text from documents

- Method: `POST`
- Path: `/api/documentsOCR`
- Content-Type: `multipart/form-data`
- Form key: `files` (can upload multiple files with the same key)

Example (single file):

```bash
curl -X POST "http://127.0.0.1:5000/api/documentsOCR" \
  -F "files=@sample.pdf"
```

Example (multiple files):

```bash
curl -X POST "http://127.0.0.1:5000/api/documentsOCR" \
  -F "files=@sample.pdf" \
  -F "files=@photo.jpg" \
  -F "files=@doc.docx"
```

Success response:

```json
{
  "text": "combined extracted text...",
  "file_types": ["PDF", "Image", "DOCX"],
  "cache_hits": ["sample.pdf"],
  "db_warnings": []
}
```

Common errors:

- `400` No file part / no selected file / invalid format / no text found
- `500` Internal extraction error

### 3) Read stored OCR rows from Supabase

- Method: `GET`
- Path: `/api/ocr-results`

Example:

```bash
curl "http://127.0.0.1:5000/api/ocr-results"
```

Success response:

```json
{
  "data": [
    {
      "file_name": "sample.pdf",
      "file_type": "PDF",
      "extracted_text": "...",
      "storage_path": "documents/20260420-120000-ab12cd34-sample.pdf",
      "storage_url": "https://<project>.supabase.co/storage/v1/object/public/ocr-uploads/..."
    }
  ]
}
```

If Supabase is not configured:

- `503` with `{"error":"Supabase is required. Configure SUPABASE_URL and key variables."}`

### 4) Search extracted text

- Methods:
  - `GET /api/search?query=<text>`

GET example:

```bash
curl "http://127.0.0.1:5000/api/search?query=invoice"
```

Success response:

```json
{
  "results": [
    {
      "pdf": "sample.pdf",
      "text": "....invoice details...."
    }
  ]
}
```

Notes:

- If nothing matches, response is:
  - `200` with `{"message":"No results found"}`

### 5) Translate text

- Method: `POST`
- Path: `/api/convert`
- Content-Type: `application/json`

Body:

```json
{
  "target_language": "ta",
  "text": "Hello world"
}
```

You can send `filename` instead of `text` to translate text from a previously uploaded/extracted file:

```json
{
  "target_language": "hi",
  "filename": "sample.pdf"
}
```

### 6) Convert text to audio (MP3)

- Method: `POST`
- Path: `/api/convert/audio`
- Content-Type: `application/json`

Body:

```json
{
  "text": "Hello world",
  "target_language": "en"
}
```

Behavior:

- If `target_language` is provided, the API translates first, then generates MP3.
- If `target_language` is omitted, English (`en`) TTS is used.
- Response is a downloadable `audio/mpeg` file.

## Important runtime note (OCR binary)

`pytesseract` requires the `tesseract` system binary.

- Local machine: install Tesseract to enable image/scanned OCR.
- Vercel: Tesseract is usually not present by default.
  - Text-based PDFs still work (embedded text extraction via `PyMuPDF`).
  - Image OCR or scanned PDF OCR may fail unless you use an external OCR service or a runtime that includes Tesseract.

## Allowed upload size

- Max file size per request is `20 MB` (`MAX_CONTENT_LENGTH` in `main.py`).
