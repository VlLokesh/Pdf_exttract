import unittest
from unittest.mock import patch

import pdf_extract


class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def extract(self):
        return self._rows


class FakeTables:
    def __init__(self, tables):
        self.tables = tables


class FakePage:
    def __init__(self, text, tables):
        self._text = text
        self._tables = tables

    def get_text(self, mode):
        return self._text

    def find_tables(self):
        return FakeTables(self._tables)


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

    @patch("pdf_extract.fitz.open")
    def test_extract_pdf_content_includes_tables(self, mock_open):
        page = FakePage(
            text="Invoice Header",
            tables=[FakeTable([["Item", "Qty"], ["Pen", "2"]])],
        )
        mock_open.return_value = FakeDoc([page])

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