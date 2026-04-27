import os
import tempfile
from typing import Optional
from uuid import uuid4

import fitz  # PyMuPDF

from supabase import SupabaseRepository


class SearchService:
    def __init__(self, repo: SupabaseRepository) -> None:
        self.repo = repo

    def highlight_matching_text(self, pdf_path: str, query: str) -> Optional[str]:
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
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
            highlighted_path = tmp_out.name

        doc.save(highlighted_path)
        doc.close()
        try:
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
