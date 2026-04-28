import json
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from uuid import uuid4


class SupabaseRepository:
    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL")
        self.key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        )
        self.table = os.getenv("SUPABASE_TABLE", "ocr_results")
        self.bucket = os.getenv("SUPABASE_BUCKET", "ocr-uploads")
        self.upload_prefix = os.getenv("SUPABASE_UPLOAD_PREFIX", "documents")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def _auth_headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }
        if extra:
            headers.update(extra)
        return headers

    def ensure_bucket_exists(self) -> None:
        if not self.configured:
            return

        bucket_info_url = f"{self.url}/storage/v1/bucket/{self.bucket}"
        check_req = Request(bucket_info_url, method="GET", headers=self._auth_headers())
        try:
            with urlopen(check_req):
                return
        except HTTPError as exc:
            if exc.code not in (400, 404):
                err_body = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Supabase bucket check failed: {exc.code} {exc.reason} {err_body}") from exc

        create_bucket_url = f"{self.url}/storage/v1/bucket"
        create_payload = {"id": self.bucket, "name": self.bucket, "public": True}
        create_req = Request(
            create_bucket_url,
            data=json.dumps(create_payload).encode("utf-8"),
            method="POST",
            headers=self._auth_headers({"Content-Type": "application/json"}),
        )

        try:
            with urlopen(create_req):
                return
        except HTTPError as exc:
            if exc.code == 409:
                return
            err_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase bucket create failed: {exc.code} {exc.reason} {err_body}") from exc

    def upload_file(self, file_path: str, file_name: str, content_type: str) -> tuple[Optional[str], Optional[str]]:
        if not self.configured:
            return None, None

        self.ensure_bucket_exists()

        object_name = (
            f"{self.upload_prefix}/"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid4().hex[:8]}-{file_name}"
        )

        with open(file_path, "rb") as file_obj:
            file_bytes = file_obj.read()

        encoded_object_name = quote(object_name, safe="/")
        upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{encoded_object_name}"
        req = Request(
            upload_url,
            data=file_bytes,
            method="POST",
            headers=self._auth_headers(
                {
                    "Content-Type": content_type or "application/octet-stream",
                    "x-upsert": "true",
                }
            ),
        )

        try:
            with urlopen(req):
                pass
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase storage upload failed: {exc.code} {exc.reason} {err_body}") from exc

        public_url = f"{self.url}/storage/v1/object/public/{self.bucket}/{encoded_object_name}"
        return object_name, public_url

    def save_ocr_result(
        self,
        file_name: str,
        file_type: str,
        extracted_text: str,
        storage_path: Optional[str] = None,
        storage_url: Optional[str] = None,
    ) -> dict:
        if not self.configured:
            return {"saved": False, "error": "Supabase is not configured"}

        payload = {
            "file_name": file_name,
            "file_type": file_type,
            "extracted_text": extracted_text,
        }
        if storage_path:
            payload["storage_path"] = storage_path
        if storage_url:
            payload["storage_url"] = storage_url

        insert_url = f"{self.url}/rest/v1/{self.table}"
        req = Request(
            insert_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=self._auth_headers(
                {
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                }
            ),
        )

        base_payload = {
            "file_name": file_name,
            "file_type": file_type,
            "extracted_text": extracted_text,
        }
        fallback_req = Request(
            insert_url,
            data=json.dumps(base_payload).encode("utf-8"),
            method="POST",
            headers=self._auth_headers(
                {
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                }
            ),
        )

        try:
            with urlopen(req):
                return {"saved": True, "error": None}
        except HTTPError:
            try:
                with urlopen(fallback_req):
                    return {"saved": True, "error": None}
            except HTTPError as fallback_exc:
                err_body = fallback_exc.read().decode("utf-8", errors="ignore")
                raw_err = f"Supabase insert failed: {fallback_exc.code} {fallback_exc.reason} {err_body}"
                if "PGRST205" in raw_err or "schema cache" in raw_err:
                    return {
                        "saved": False,
                        "error": f"Table '{self.table}' not found. Run SQL setup to create it.",
                    }
                return {"saved": False, "error": raw_err}

    def search_rows(self, query: str, limit: int = 100) -> list[dict]:
        if not self.configured:
            raise RuntimeError("Supabase is not configured")

        encoded_pattern = quote_plus(f"*{query}*")
        search_url = (
            f"{self.url}/rest/v1/{self.table}"
            f"?select=file_name,page_num,extracted_text,storage_path,storage_url"
            f"&extracted_text=ilike.{encoded_pattern}&limit={limit}"
        )
        req = Request(search_url, method="GET", headers=self._auth_headers())
        with urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))

    def fetch_recent_results(self, limit: int = 100) -> list[dict]:
        if not self.configured:
            raise RuntimeError("Supabase is not configured")

        select_url = f"{self.url}/rest/v1/{self.table}?select=*&limit={limit}"
        req = Request(select_url, method="GET", headers=self._auth_headers())
        with urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))

    def fetch_latest_by_filename(self, file_name: str) -> Optional[dict]:
        if not self.configured:
            raise RuntimeError("Supabase is not configured")
        if not file_name:
            return None

        encoded_name = quote(file_name, safe="")
        ordered_url = (
            f"{self.url}/rest/v1/{self.table}"
            f"?select=*"
            f"&file_name=eq.{encoded_name}"
            f"&order=created_at.desc.nullslast"
            f"&limit=1"
        )
        req = Request(ordered_url, method="GET", headers=self._auth_headers())
        try:
            with urlopen(req) as res:
                rows = json.loads(res.read().decode("utf-8"))
        except HTTPError:
            fallback_url = (
                f"{self.url}/rest/v1/{self.table}"
                f"?select=*"
                f"&file_name=eq.{encoded_name}"
                f"&limit=1"
            )
            fallback_req = Request(fallback_url, method="GET", headers=self._auth_headers())
            with urlopen(fallback_req) as res:
                rows = json.loads(res.read().decode("utf-8"))
        return rows[0] if rows else None

    def download_to_path(
        self,
        destination_path: str,
        storage_path: Optional[str] = None,
        storage_url: Optional[str] = None,
    ) -> bool:
        if storage_url:
            try:
                with urlopen(storage_url) as res:
                    file_bytes = res.read()
                with open(destination_path, "wb") as out:
                    out.write(file_bytes)
                return True
            except Exception:
                pass

        if storage_path and self.configured:
            encoded_path = quote(storage_path, safe="/")
            object_url = f"{self.url}/storage/v1/object/{self.bucket}/{encoded_path}"
            req = Request(object_url, method="GET", headers=self._auth_headers())
            try:
                with urlopen(req) as res:
                    file_bytes = res.read()
                with open(destination_path, "wb") as out:
                    out.write(file_bytes)
                return True
            except Exception:
                return False

        return False
