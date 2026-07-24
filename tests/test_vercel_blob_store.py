import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vercel_blob_store as blob_store


class _FakeResponse:
    def __init__(self, data: bytes = b""):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class VercelBlobStoreTests(unittest.TestCase):
    def test_enabled_reflects_token_presence(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(blob_store.enabled())
        with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True):
            self.assertTrue(blob_store.enabled())

    def test_push_is_noop_without_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "file.txt"
            path.write_bytes(b"hello")
            with patch.dict("os.environ", {}, clear=True), \
                 patch("urllib.request.urlopen") as urlopen:
                blob_store.push(path, "uploads/file.txt")
                urlopen.assert_not_called()

    def test_push_is_noop_when_file_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.txt"
            with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True), \
                 patch("urllib.request.urlopen") as urlopen:
                blob_store.push(path, "uploads/missing.txt")
                urlopen.assert_not_called()

    def test_push_uploads_file_contents_with_auth_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "file.txt"
            path.write_bytes(b"hello world")
            with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True), \
                 patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen:
                blob_store.push(path, "uploads/file.txt")
                urlopen.assert_called_once()
                request = urlopen.call_args[0][0]
                self.assertEqual(request.full_url, f"{blob_store.BLOB_API_URL}/uploads/file.txt")
                self.assertEqual(request.get_method(), "PUT")
                self.assertEqual(request.get_header("Authorization"), "Bearer token")
                self.assertEqual(request.data, b"hello world")

    def test_pull_if_missing_skips_when_local_file_already_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "file.txt"
            path.write_bytes(b"already here")
            with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True), \
                 patch("urllib.request.urlopen") as urlopen:
                blob_store.pull_if_missing(path, "uploads/file.txt")
                urlopen.assert_not_called()
                self.assertEqual(path.read_bytes(), b"already here")

    def test_pull_if_missing_downloads_and_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "file.txt"
            with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True), \
                 patch("urllib.request.urlopen", return_value=_FakeResponse(b"remote bytes")) as urlopen:
                blob_store.pull_if_missing(path, "uploads/nested/file.txt")
                urlopen.assert_called_once()
                request = urlopen.call_args[0][0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(path.read_bytes(), b"remote bytes")

    def test_pull_if_missing_is_noop_without_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "file.txt"
            with patch.dict("os.environ", {}, clear=True), \
                 patch("urllib.request.urlopen") as urlopen:
                blob_store.pull_if_missing(path, "uploads/file.txt")
                urlopen.assert_not_called()
                self.assertFalse(path.exists())

    def test_delete_is_noop_without_token(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("urllib.request.urlopen") as urlopen:
            blob_store.delete("uploads/file.txt")
            urlopen.assert_not_called()

    def test_delete_sends_delete_request(self):
        with patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "token"}, clear=True), \
             patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen:
            blob_store.delete("uploads/file.txt")
            urlopen.assert_called_once()
            request = urlopen.call_args[0][0]
            self.assertEqual(request.get_method(), "DELETE")
            self.assertEqual(request.full_url, f"{blob_store.BLOB_API_URL}/uploads/file.txt")


if __name__ == "__main__":
    unittest.main()
