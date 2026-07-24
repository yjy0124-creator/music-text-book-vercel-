import tempfile
import unittest
from pathlib import Path

from team_app import TeamStore, _safe_name


class TeamAppTests(unittest.TestCase):
    def test_reference_versions_and_textbooks_are_persistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TeamStore(root)
            first = root / "first.pdf"
            second = root / "second.pdf"
            book = root / "book.pdf"
            for path in (first, second, book):
                path.write_bytes(b"%PDF-1.4\n")
            store.add_reference("curriculum", "first.pdf", first, "a" * 64, 9, "2015", "음악")
            store.add_reference("curriculum", "second.pdf", second, "b" * 64, 9, "2022", "음악")
            store.add_reference("textbook", "book.pdf", book, "c" * 64, 9, "2015", "음악")
            active = store.references()
            self.assertEqual(len([item for item in active if item["kind"] == "curriculum"]), 1)
            self.assertEqual(store.active_reference("curriculum")["original_name"], "second.pdf")
            self.assertEqual(len([item for item in active if item["kind"] == "textbook"]), 1)

    def test_jobs_keep_status_and_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TeamStore(root)
            source = root / "manuscript.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            job_id = store.create_job(
                "manuscript.pdf", source, "d" * 64, 9,
                work_titles="소녀와, 두 바퀴로 가는 자동차",
            )
            self.assertEqual(store.job(job_id)["status"], "queued")
            self.assertEqual(store.job(job_id)["work_titles"], "소녀와, 두 바퀴로 가는 자동차")
            store.update_job(job_id, status="completed", result_path="result.html")
            self.assertEqual(store.job(job_id)["result_path"], "result.html")

    def test_safe_name_removes_directories(self):
        self.assertEqual(_safe_name("../../원고 최종.pdf"), "원고_최종.pdf")

    def test_storage_usage_counts_all_uploaded_pdf_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = TeamStore(Path(temporary))
            first = store.uploads / "references" / "first.pdf"
            second = store.uploads / "manuscripts" / "job" / "second.pdf"
            first.parent.mkdir(parents=True, exist_ok=True)
            second.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"a" * 100)
            second.write_bytes(b"b" * 300)
            usage = store.storage_usage()
            self.assertEqual(usage["used_bytes"], 400)
            self.assertEqual(usage["remaining_bytes"], usage["capacity_bytes"] - 400)

    def test_delete_reference_removes_only_selected_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = TeamStore(Path(temporary) / "data")
            first = store.uploads / "references" / "textbook" / "first.pdf"
            second = store.uploads / "references" / "textbook" / "second.pdf"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"%PDF-1.4\nfirst")
            second.write_bytes(b"%PDF-1.4\nsecond")
            first_item = store.add_reference("textbook", "first.pdf", first, "1" * 64, first.stat().st_size, "", "")
            store.add_reference("textbook", "second.pdf", second, "2" * 64, second.stat().st_size, "", "")
            removed = store.delete_reference(first_item["id"])
            self.assertEqual(removed["original_name"], "first.pdf")
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual([item["original_name"] for item in store.references()], ["second.pdf"])

    def test_reset_removes_app_copies_and_history_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TeamStore(root / "data")
            original = root / "original.pdf"
            original.write_bytes(b"%PDF-1.4\n")
            uploaded = store.uploads / "references" / "curriculum" / "copy.pdf"
            uploaded.parent.mkdir(parents=True, exist_ok=True)
            uploaded.write_bytes(original.read_bytes())
            store.add_reference("curriculum", "original.pdf", uploaded, "e" * 64, 9, "", "")
            store.create_job("original.pdf", original, "f" * 64, 9)
            store.update_job(store.jobs()[0]["id"], status="completed")
            store.reset_all()
            self.assertTrue(original.exists())
            self.assertFalse(uploaded.exists())
            self.assertFalse(store.references())
            self.assertFalse(store.jobs())


if __name__ == "__main__":
    unittest.main()
