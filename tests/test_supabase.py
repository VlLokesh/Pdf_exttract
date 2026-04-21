import io
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from supabase import SupabaseRepository


class DummyResponse:
    def __init__(self, data=b"[]"):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SupabaseRepositoryTests(unittest.TestCase):
    def test_configured_false_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            repo = SupabaseRepository()
            self.assertFalse(repo.configured)

    @patch("supabase.urlopen")
    def test_save_ocr_result_success(self, mock_urlopen):
        repo = SupabaseRepository()
        repo.url = "https://example.supabase.co"
        repo.key = "test-key"
        repo.table = "ocr_results"

        mock_urlopen.return_value = DummyResponse()

        result = repo.save_ocr_result("a.pdf", "PDF", "text")
        self.assertEqual(result, {"saved": True, "error": None})

    @patch("supabase.urlopen")
    def test_save_ocr_result_schema_error(self, mock_urlopen):
        repo = SupabaseRepository()
        repo.url = "https://example.supabase.co"
        repo.key = "test-key"
        repo.table = "ocr_results"

        err = HTTPError(
            url="https://example.supabase.co/rest/v1/ocr_results",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"code":"PGRST205"}'),
        )
        err_fallback = HTTPError(
            url="https://example.supabase.co/rest/v1/ocr_results",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"code":"PGRST205"}'),
        )
        mock_urlopen.side_effect = [err, err_fallback]

        result = repo.save_ocr_result("a.pdf", "PDF", "text")
        self.assertFalse(result["saved"])
        self.assertIn("not found", result["error"])

    @patch("supabase.urlopen")
    def test_download_to_path_from_public_url(self, mock_urlopen):
        repo = SupabaseRepository()
        output_path = os.path.join(os.getcwd(), "tests", "_tmp_download.pdf")
        if os.path.exists(output_path):
            os.remove(output_path)

        mock_urlopen.return_value = DummyResponse(data=b"pdf-bytes")

        ok = repo.download_to_path(output_path, storage_url="https://public/file.pdf")

        self.assertTrue(ok)
        with open(output_path, "rb") as file_obj:
            self.assertEqual(file_obj.read(), b"pdf-bytes")

        os.remove(output_path)


if __name__ == "__main__":
    unittest.main()