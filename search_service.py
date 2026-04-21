import os
from typing import Optional
from uuid import uuid4

import fitz  # PyMuPDF

from supabase import SupabaseRepository


class SearchService:
    def __init__(self, repo: SupabaseRepository, upload_folder: str) -> None:
        self.repo = repo
        self.upload_folder = upload_folder

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
        highlighted_path = os.path.join(self.upload_folder, highlighted_name)
        doc.save(highlighted_path)
        doc.close()
        return highlighted_path

    def ensure_local_pdf_for_highlight(
        self,
        pdf_filename: str,
        storage_path: Optional[str],
        storage_url: Optional[str],
    ) -> Optional[str]:
        local_path = os.path.join(self.upload_folder, pdf_filename)
        if os.path.exists(local_path):
            return local_path

        downloaded = self.repo.download_to_path(
            destination_path=local_path,
            storage_path=storage_path,
            storage_url=storage_url,
        )
        return local_path if downloaded else None

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
                highlighted_files.append(os.path.basename(highlighted_pdf_path))

        if not results:
            return {"message": "No results found"}

        return {"results": results, "highlighted_files": highlighted_files}