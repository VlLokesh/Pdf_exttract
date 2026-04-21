import shutil
from io import BytesIO

import fitz  # PyMuPDF
import pytesseract
from docx import Document
from PIL import Image

VALID_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "bmp", "gif", "docx"]
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "gif"}
TESSERACT_AVAILABLE = bool(shutil.which("tesseract"))


def _serialize_table_rows(rows: list[list[str]]) -> str:
    lines = []
    for row in rows:
        cells = [(cell or "").strip().replace("\n", " ") for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    text_chunks = [para.text for para in doc.paragraphs if para.text and para.text.strip()]

    image_text_chunks = []
    if TESSERACT_AVAILABLE:
        for rel in doc.part.rels.values():
            if "image" not in rel.target_ref:
                continue
            img_data = rel.target_part._blob
            image = Image.open(BytesIO(img_data))
            image_text = pytesseract.image_to_string(image)
            if image_text.strip():
                image_text_chunks.append(image_text)

    return "\n".join(text_chunks + image_text_chunks)


def extract_text_from_image(file_path: str) -> str:
    if not TESSERACT_AVAILABLE:
        raise RuntimeError("Tesseract binary is not available in this runtime.")
    image = Image.open(file_path).convert("L")
    return pytesseract.image_to_string(image)


def extract_pdf_content(pdf_path: str) -> dict:
    text_chunks = []
    table_items = []
    doc = fitz.open(pdf_path)

    for page_index, page in enumerate(doc, start=1):
        page_text = page.get_text("text")
        if page_text and page_text.strip():
            text_chunks.append(page_text)

        try:
            tables = page.find_tables()
            for table_index, table in enumerate(tables.tables, start=1):
                rows = table.extract()
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
        except Exception:
            # Table extraction is best-effort and should not break OCR flow.
            pass

        if (not page_text or not page_text.strip()) and TESSERACT_AVAILABLE:
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ocr_text = pytesseract.image_to_string(img)
            if ocr_text.strip():
                text_chunks.append(ocr_text)

    doc.close()

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