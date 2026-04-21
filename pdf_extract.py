import json
import os
import tempfile
from io import BytesIO

import fitz  # PyMuPDF
from docx import Document
from ocrspace import ocr_space_file
from PIL import Image
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

VALID_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "bmp", "gif", "docx"]
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "gif"}
OCRSPACE_API_KEY = os.getenv("OCRSPACE_API_KEY", "helloworld")
OCRSPACE_LANGUAGE = os.getenv("OCRSPACE_LANGUAGE", "eng")


def _serialize_table_rows(rows: list[list[str]]) -> str:
    lines = []
    for row in rows:
        cells = [(cell or "").strip().replace("\n", " ") for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_ocrspace_text(response_text: str) -> str:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return ""

    parsed_results = payload.get("ParsedResults") or []
    chunks = []
    for item in parsed_results:
        chunk = (item.get("ParsedText") or "").strip()
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks)


def _ocr_space_from_path(file_path: str) -> str:
    response_text = ocr_space_file(
        filename=file_path,
        api_key=OCRSPACE_API_KEY,
        language=OCRSPACE_LANGUAGE,
    )
    return _parse_ocrspace_text(response_text)


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    text_chunks = [para.text for para in doc.paragraphs if para.text and para.text.strip()]

    image_text_chunks = []
    for rel in doc.part.rels.values():
        if "image" not in rel.target_ref:
            continue
        img_data = rel.target_part._blob
        image = Image.open(BytesIO(img_data)).convert("RGB")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            temp_image_path = tmp_file.name
        try:
            image.save(temp_image_path, format="PNG")
            image_text = _ocr_space_from_path(temp_image_path)
            if image_text.strip():
                image_text_chunks.append(image_text)
        finally:
            if os.path.exists(temp_image_path):
                os.remove(temp_image_path)

    return "\n".join(text_chunks + image_text_chunks)


def extract_text_from_image(file_path: str) -> str:
    return _ocr_space_from_path(file_path)


def _extract_tables_with_pdfplumber(pdf_path: str) -> list[dict]:
    table_items = []
    if pdfplumber is None:
        return table_items

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            extracted_tables = page.extract_tables() or []
            for table_index, rows in enumerate(extracted_tables, start=1):
                if not rows:
                    continue
                table_text = _serialize_table_rows(rows)
                if not table_text:
                    continue
                table_items.append(
                    {
                        "page": page_index,
                        "table_index": table_index,
                        "rows": rows,
                        "text": table_text,
                    }
                )
    return table_items


def extract_pdf_content(pdf_path: str) -> dict:
    text_chunks = []
    table_items = []
    doc = fitz.open(pdf_path)

    for page_index, page in enumerate(doc, start=1):
        page_text = page.get_text("text")
        if page_text and page_text.strip():
            text_chunks.append(page_text)

        if not page_text or not page_text.strip():
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                temp_image_path = tmp_file.name
            try:
                img.save(temp_image_path, format="PNG")
                ocr_text = _ocr_space_from_path(temp_image_path)
                if ocr_text.strip():
                    text_chunks.append(ocr_text)
            finally:
                if os.path.exists(temp_image_path):
                    os.remove(temp_image_path)

    doc.close()
    try:
        table_items = _extract_tables_with_pdfplumber(pdf_path)
    except Exception:
        # Table extraction is best-effort and should not break OCR flow.
        table_items = []

    table_text_chunks = [f"[Table Page {t['page']} #{t['table_index']}]\n{t['text']}" for t in table_items]
    merged_text = "\n\n".join([chunk for chunk in text_chunks + table_text_chunks if chunk.strip()])

    return {
        "text": merged_text,
        "tables": table_items,
    }


def extract_file_content(file_path: str, extension: str) -> dict:
    if extension == "pdf":
        pdf_data = extract_pdf_content(file_path)
        return {
            "file_type": "PDF",
            "text": pdf_data["text"],
            "tables": pdf_data["tables"],
        }

    if extension in IMAGE_EXTENSIONS:
        return {
            "file_type": "Image",
            "text": extract_text_from_image(file_path),
            "tables": [],
        }

    return {
        "file_type": "DOCX",
        "text": extract_text_from_docx(file_path),
        "tables": [],
    }