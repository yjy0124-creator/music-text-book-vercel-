import json
import tempfile
import unittest
from pathlib import Path

import textbook_parser as parser
from viewer_generator import generate_review_html


class UtilityTests(unittest.TestCase):
    def test_bbox_is_clamped_and_normalized(self):
        box, normalized = parser._bbox([-5, 10, 120, 90], 100, 100)
        self.assertEqual(box, [0.0, 10.0, 100, 90.0])
        self.assertEqual(normalized, [0.0, 0.1, 1.0, 0.9])

    def test_unknown_always_requires_review(self):
        element = parser._element(
            element_id="p0001_unknown_0001", kind="unknown", page_no=1,
            bbox=[0, 0, 10, 10], width=100, height=100,
            method="none", confidence=0,
        )
        self.assertTrue(element["review_required"])
        self.assertIn("UNKNOWN_TYPE", {r["code"] for r in element["review_reasons"]})

    def test_two_column_reading_order(self):
        elements = []
        for i, box in enumerate(([0, 0, 40, 10], [0, 20, 40, 30],
                                 [60, 0, 100, 10], [60, 20, 100, 30]), 1):
            elements.append(parser._element(
                element_id=str(i), kind="paragraph", page_no=1, bbox=box,
                width=100, height=100, method="test", confidence=1,
            ))
        parser._assign_reading_order(elements, 100)
        self.assertEqual([e["id"] for e in elements], ["1", "2", "3", "4"])

    def test_write_json_keeps_korean(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data.json"
            parser._write_json(path, {"상태": "확인 필요"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["상태"], "확인 필요")


class IntegrationTests(unittest.TestCase):
    def test_parse_synthetic_pdf(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab not installed")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "sample.pdf"
            doc = canvas.Canvas(str(pdf), pagesize=(300, 400))
            doc.setFont("Helvetica", 22)
            doc.drawString(30, 360, "Sample title")
            doc.setFont("Helvetica", 11)
            doc.drawString(30, 310, "Body paragraph")
            doc.save()
            result = parser.parse_pdf(pdf, root / "out", parser.ParserConfig(dpi=72, ocr=False))
            data = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(data["page_count"], 1)
            self.assertTrue(data["pages"][0]["elements"])
            self.assertTrue((result.parent / "review.json").exists())

            try:
                import jsonschema
            except ImportError:
                return
            schema = json.loads((Path(__file__).parents[1] / "document.schema.json").read_text(encoding="utf-8"))
            jsonschema.validate(data, schema)

            html = generate_review_html(root / "out")
            content = html.read_text(encoding="utf-8")
            self.assertIn("교과서 PDF 구조화 검수", content)
            self.assertIn("Sample title", content)
            self.assertNotIn("fetch(", content)


if __name__ == "__main__":
    unittest.main()
