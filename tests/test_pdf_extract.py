import unittest
from unittest.mock import patch

import pdf_extract


class FakePage:
    def __init__(self, text):
        self._text = text

    def get_text(self, mode):
        return self._text


class FakeDoc:
    def __init__(self, pages):
        self._pages = pages

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        return None


class PdfExtractTests(unittest.TestCase):
    def test_serialize_table_rows(self):
        rows = [[" A ", "B\nC"], ["", "   "]]
        serialized = pdf_extract._serialize_table_rows(rows)
        self.assertEqual(serialized, "A | B C")

    @patch("pdf_extract._extract_tables_with_pdfplumber")
    @patch("pdf_extract.fitz.open")
    def test_extract_pdf_content_includes_tables(self, mock_open, mock_extract_tables):
        page = FakePage(text="Invoice Header")
        mock_open.return_value = FakeDoc([page])
        mock_extract_tables.return_value = [
            {
                "page": 1,
                "table_index": 1,
                "rows": [["Item", "Qty"], ["Pen", "2"]],
                "text": "Item | Qty\nPen | 2",
            }
        ]

        result = pdf_extract.extract_pdf_content("sample.pdf")

        self.assertIn("Invoice Header", result["text"])
        self.assertIn("[Table Page 1 #1]", result["text"])
        self.assertIn("Item | Qty", result["text"])
        self.assertEqual(len(result["tables"]), 1)

    @patch("pdf_extract.extract_text_from_docx")
    def test_extract_file_content_docx_dispatch(self, mock_docx):
        mock_docx.return_value = "docx content"

        result = pdf_extract.extract_file_content("x.docx", "docx")

        self.assertEqual(result["file_type"], "DOCX")
        self.assertEqual(result["text"], "docx content")
        self.assertEqual(result["tables"], [])


if __name__ == "__main__":
    unittest.main()
