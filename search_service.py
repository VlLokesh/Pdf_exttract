import os
import tempfile
from typing import Optional
from uuid import uuid4

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from supabase_repo import SupabaseRepository


class SearchService:
    def __init__(self, repo: SupabaseRepository) -> None:
        self.repo = repo

    def highlight_matching_text(self, pdf_path: str, query: str) -> Optional[str]:
        query_text = (query or "").strip()
        if not query_text:
            return None

        try:
            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            return None

        hits = 0
        q_lower = query_text.lower()
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_index, pl_page in enumerate(pdf.pages):
                    page_height = float(pl_page.height or 0)
                    rects = []

                    if hasattr(pl_page, "search"):
                        try:
                            matches = pl_page.search(query_text) or []
                            for m in matches:
                                x0 = m.get("x0")
                                x1 = m.get("x1")
                                top = m.get("top")
                                bottom = m.get("bottom")
                                if None in (x0, x1, top, bottom):
                                    continue
                                rects.append((float(x0), float(top), float(x1), float(bottom)))
                        except Exception:
                            rects = []

                    if not rects:
                        words = pl_page.extract_words() or []
                        for w in words:
                            text = (w.get("text") or "").strip().lower()
                            if q_lower not in text:
                                continue
                            x0 = w.get("x0")
                            x1 = w.get("x1")
                            top = w.get("top")
                            bottom = w.get("bottom")
                            if None in (x0, x1, top, bottom):
                                continue
                            rects.append((float(x0), float(top), float(x1), float(bottom)))

                    if not rects or page_index >= len(writer.pages):
                        continue

                    writer_page = writer.pages[page_index]
                    if "/Annots" not in writer_page:
                        writer_page[NameObject("/Annots")] = ArrayObject()

                    annots = writer_page[NameObject("/Annots")]
                    for x0, top, x1, bottom in rects:
                        y_top = max(0.0, page_height - top)
                        y_bottom = max(0.0, page_height - bottom)
                        y1 = max(y_top, y_bottom)
                        y0 = min(y_top, y_bottom)
                        if x1 <= x0 or y1 <= y0:
                            continue

                        quad = ArrayObject(
                            [
                                FloatObject(x0),
                                FloatObject(y1),
                                FloatObject(x1),
                                FloatObject(y1),
                                FloatObject(x0),
                                FloatObject(y0),
                                FloatObject(x1),
                                FloatObject(y0),
                            ]
                        )
                        annot = DictionaryObject()
                        annot.update(
                            {
                                NameObject("/Type"): NameObject("/Annot"),
                                NameObject("/Subtype"): NameObject("/Highlight"),
                                NameObject("/Rect"): ArrayObject(
                                    [
                                        FloatObject(x0),
                                        FloatObject(y0),
                                        FloatObject(x1),
                                        FloatObject(y1),
                                    ]
                                ),
                                NameObject("/QuadPoints"): quad,
                                NameObject("/C"): ArrayObject(
                                    [FloatObject(1), FloatObject(1), FloatObject(0)]
                                ),
                                NameObject("/F"): NumberObject(4),
                                NameObject("/Contents"): TextStringObject(f"Matched: {query_text}"),
                            }
                        )
                        annots.append(writer._add_object(annot))
                        hits += 1
        except Exception:
            return None

        if hits == 0:
            return None

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        highlighted_name = f"{base_name}-highlighted-{uuid4().hex[:8]}.pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
            highlighted_path = tmp_out.name

        try:
            with open(highlighted_path, "wb") as out_fp:
                writer.write(out_fp)
            _, highlighted_url = self.repo.upload_file(
                file_path=highlighted_path,
                file_name=highlighted_name,
                content_type="application/pdf",
            )
            return highlighted_url
        finally:
            if os.path.exists(highlighted_path):
                try:
                    os.remove(highlighted_path)
                except OSError:
                    pass

    def ensure_local_pdf_for_highlight(
        self,
        pdf_filename: str,
        storage_path: Optional[str],
        storage_url: Optional[str],
    ) -> Optional[str]:
        tmp_suffix = os.path.splitext(pdf_filename)[1] if "." in pdf_filename else ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=tmp_suffix) as tmp_pdf:
            local_path = tmp_pdf.name
        downloaded = self.repo.download_to_path(
            destination_path=local_path,
            storage_path=storage_path,
            storage_url=storage_url,
        )
        if downloaded:
            return local_path
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
        return None

    def build_search_payload(self, query: str) -> dict:
        results = []
        highlighted_files = []

        rows = self.repo.search_rows(query)
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

            if not pdf_filename or not pdf_filename.lower().endswith(".pdf"):
                continue

            file_path = self.ensure_local_pdf_for_highlight(
                pdf_filename=pdf_filename,
                storage_path=row.get("storage_path"),
                storage_url=row.get("storage_url"),
            )
            if not file_path:
                continue

            highlighted_pdf_path = self.highlight_matching_text(file_path, query)
            if highlighted_pdf_path:
                highlighted_files.append(highlighted_pdf_path)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

        if not results:
            return {"message": "No results found"}

        return {"results": results, "highlighted_files": highlighted_files}
