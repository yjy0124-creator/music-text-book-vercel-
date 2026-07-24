"""로컬 우선 PDF 구조화 파서.

PDF의 원문은 수정하지 않는다. 판독이 불확실한 항목은 추측하지 않고
review_required/review_reasons에 기록한다.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

SCHEMA_VERSION = "1.0.0"
ELEMENT_TYPES = {
    "title", "heading", "paragraph", "caption", "image", "table",
    "music_score", "header", "footer", "page_number", "unknown",
}
REVIEW_MESSAGES = {
    "OCR_UNAVAILABLE": "텍스트층이 없고 OCR을 사용할 수 없어 확인이 필요합니다.",
    "OCR_EMPTY": "OCR을 실행했지만 읽을 수 있는 텍스트를 찾지 못했습니다.",
    "LOW_CONFIDENCE": "추출 또는 분류 신뢰도가 낮아 확인이 필요합니다.",
    "UNKNOWN_TYPE": "요소 종류를 확정할 수 없어 확인이 필요합니다.",
    "TABLE_STRUCTURE_FAILED": "표 영역은 찾았지만 행·열 구조를 안정적으로 복원하지 못했습니다.",
    "TABLE_MERGED_CELL": "병합 셀 또는 불규칙한 셀 경계가 있어 표 구조 확인이 필요합니다.",
    "MUSIC_AMBIGUOUS": "악보로 보이는 시각 요소의 분류가 불확실합니다.",
    "EMPTY_IMAGE": "이미지 영역을 저장하지 못했거나 내용이 비어 있습니다.",
    "OVERLAPPING_ELEMENTS": "서로 크게 겹치는 요소가 있어 읽기 순서 또는 중복 확인이 필요합니다.",
    "READING_ORDER_AMBIGUOUS": "다단 또는 박스형 지면의 읽기 순서가 불확실합니다.",
    "PAGE_EMPTY": "페이지에서 텍스트나 시각 요소를 찾지 못했습니다.",
}


class AIReviewAdapter(Protocol):
    """선택적 AI 재판독 어댑터가 구현해야 하는 인터페이스."""

    def review(self, crop_path: str, element: dict[str, Any]) -> dict[str, Any] | None: ...


@dataclass
class ParserConfig:
    dpi: int = 200
    ocr: bool = True
    ocr_language: str = "korean"
    review_threshold: float = 0.85
    render_pages: bool = True
    ai_review_module: str | None = None


def _require_fitz():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF가 필요합니다. 'python -m pip install -r requirements.txt'를 실행하세요."
        ) from exc
    return fitz


def _fitz_available() -> bool:
    try:
        _require_fitz()
        return True
    except (RuntimeError, ImportError, OSError):
        return False


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError:
        return None
    return Image, ImageDraw, ImageFont


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_id(name: str, digest: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", name).strip("_") or "document"
    return f"{stem[:48]}_{digest[:8]}"


def _bbox(values: Iterable[float], width: float, height: float) -> tuple[list[float], list[float]]:
    x0, y0, x1, y1 = [float(v) for v in values]
    x0, x1 = sorted((max(0.0, x0), min(width, x1)))
    y0, y1 = sorted((max(0.0, y0), min(height, y1)))
    box = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
    normalized = [round(x0 / width, 6), round(y0 / height, 6),
                  round(x1 / width, 6), round(y1 / height, 6)]
    return box, normalized


def _review(code: str) -> dict[str, str]:
    return {"code": code, "message": REVIEW_MESSAGES[code]}


def _element(
    *, element_id: str, kind: str, page_no: int, bbox: Iterable[float],
    width: float, height: float, text: str = "", method: str,
    confidence: float, asset_path: str | None = None,
    reasons: list[dict[str, str]] | None = None, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    box, normalized = _bbox(bbox, width, height)
    reasons = list(reasons or [])
    if kind == "unknown" and not any(r["code"] == "UNKNOWN_TYPE" for r in reasons):
        reasons.append(_review("UNKNOWN_TYPE"))
    result: dict[str, Any] = {
        "id": element_id,
        "type": kind if kind in ELEMENT_TYPES else "unknown",
        "page": page_no,
        "reading_order": 0,
        "bbox": box,
        "normalized_bbox": normalized,
        "text": text.strip(),
        "extraction_method": method,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "review_required": bool(reasons),
        "review_reasons": reasons,
        "asset_path": asset_path,
    }
    if extra:
        result.update(extra)
    return result


def _text_kind(text: str, bbox: list[float], page_height: float,
               max_size: float, median_size: float, size: float) -> tuple[str, float]:
    clean = text.strip()
    y0, y1 = bbox[1], bbox[3]
    if re.fullmatch(r"[-–—]?\s*\d+\s*[-–—]?", clean) and (y0 < page_height * .1 or y1 > page_height * .9):
        return "page_number", .98
    if y0 < page_height * .07:
        return "header", .9
    if y1 > page_height * .94:
        return "footer", .9
    if max_size >= median_size * 1.45 and size >= max_size * .9 and len(clean) <= 100:
        return "title", .9
    if size >= median_size * 1.25 and len(clean) <= 160:
        return "heading", .88
    if len(clean) <= 100 and re.match(r"^(그림|표|악보|자료|출처)\s*\d*", clean):
        return "caption", .93
    return "paragraph", .95


def _extract_text(page: Any, page_no: int, width: float, height: float) -> list[dict[str, Any]]:
    raw = page.get_text("dict", sort=False)
    candidates: list[tuple[dict[str, Any], float, float]] = []
    sizes: list[float] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines: list[str] = []
        block_sizes: list[float] = []
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                if span.get("text", "").strip():
                    block_sizes.append(float(span.get("size", 0)))
            if "".join(parts).strip():
                lines.append("".join(parts).rstrip())
        text = "\n".join(lines).strip()
        if not text:
            continue
        size = max(block_sizes, default=0.0)
        sizes.extend(block_sizes)
        candidates.append((block, size, sum(block_sizes) / len(block_sizes) if block_sizes else 0.0))
    median = sorted(sizes)[len(sizes) // 2] if sizes else 10.0
    maximum = max(sizes, default=median)
    elements = []
    for index, (block, size, _average) in enumerate(candidates, 1):
        text = "\n".join(
            "".join(span.get("text", "") for span in line.get("spans", [])).rstrip()
            for line in block.get("lines", [])
        ).strip()
        box = list(block["bbox"])
        kind, confidence = _text_kind(text, box, height, maximum, median, size)
        elements.append(_element(
            element_id=f"p{page_no:04d}_text_{index:04d}", kind=kind, page_no=page_no,
            bbox=box, width=width, height=height, text=text, method="pdf_text_layer",
            confidence=confidence,
            extra={"style": {"max_font_size": round(size, 3)}},
        ))
    return elements


def _save_crop(page: Any, bbox: list[float], destination: Path, dpi: int) -> bool:
    fitz = _require_fitz()
    destination.parent.mkdir(parents=True, exist_ok=True)
    rect = fitz.Rect(bbox)
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        return False
    try:
        pix = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
        if pix.width < 2 or pix.height < 2:
            return False
        pix.save(str(destination))
        return destination.exists() and destination.stat().st_size > 0
    except Exception:
        return False


def _music_likelihood(image_path: Path) -> float:
    pillow = _require_pillow()
    if not pillow or not image_path.exists():
        return 0.0
    Image, _, _ = pillow
    try:
        image = Image.open(image_path).convert("L")
        if image.width < 80 or image.height < 40:
            return 0.0
        image.thumbnail((1200, 1200))
        pixels = image.load()
        dark_per_row = []
        # PDF 렌더링의 얇은 오선은 안티앨리어싱되어 순수 검정이 아닐 수 있다.
        # 밝은 회색까지 포함하되, 가로 폭의 절반 이상 이어진 선만 후보로 삼는다.
        for y in range(image.height):
            dark_per_row.append(sum(1 for x in range(image.width) if pixels[x, y] < 210) / image.width)
        line_rows = [i for i, ratio in enumerate(dark_per_row) if ratio > .55]
        groups: list[list[int]] = []
        for row in line_rows:
            if not groups or row - groups[-1][-1] > 2:
                groups.append([row])
            else:
                groups[-1].append(row)
        centers = [sum(g) / len(g) for g in groups]
        staff_runs = 0
        for i in range(max(0, len(centers) - 4)):
            gaps = [centers[j + 1] - centers[j] for j in range(i, i + 4)]
            mean = sum(gaps) / 4
            if 2 <= mean <= 30 and max(abs(g - mean) for g in gaps) <= max(2.0, mean * .35):
                staff_runs += 1
        dark_ratio = sum(1 for v in image.getdata() if v < 160) / (image.width * image.height)
        return min(1.0, staff_runs * .28 + (0.2 if .03 < dark_ratio < .35 else 0.0))
    except Exception:
        return 0.0


def _extract_images(page: Any, page_no: int, width: float, height: float,
                    asset_dir: Path, output_root: Path, dpi: int) -> list[dict[str, Any]]:
    elements = []
    seen: set[tuple[int, int, int, int]] = set()
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for index, info in enumerate(infos, 1):
        box = [float(v) for v in info.get("bbox", (0, 0, 0, 0))]
        key = tuple(round(v) for v in box)
        if key in seen or box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        seen.add(key)
        element_id = f"p{page_no:04d}_image_{index:04d}"
        path = asset_dir / f"{element_id}.png"
        saved = _save_crop(page, box, path, dpi)
        likelihood = _music_likelihood(path) if saved else 0.0
        kind = "music_score" if likelihood >= .72 else "image"
        reasons = []
        confidence = .9 if kind == "image" else likelihood
        if .42 <= likelihood < .72:
            reasons.append(_review("MUSIC_AMBIGUOUS"))
            confidence = max(.5, 1 - likelihood)
        if not saved:
            reasons.append(_review("EMPTY_IMAGE"))
            confidence = 0.0
        elements.append(_element(
            element_id=element_id, kind=kind, page_no=page_no, bbox=box,
            width=width, height=height, method="pdf_image_region", confidence=confidence,
            asset_path=path.relative_to(output_root).as_posix() if saved else None,
            reasons=reasons, extra={"music_score_likelihood": round(likelihood, 4)},
        ))
    return elements


def _extract_tables(page: Any, page_no: int, width: float, height: float,
                    asset_dir: Path, output_root: Path, dpi: int) -> list[dict[str, Any]]:
    elements = []
    try:
        finder = page.find_tables()
        tables = list(finder.tables)
    except Exception:
        return elements
    for index, table in enumerate(tables, 1):
        box = list(table.bbox)
        element_id = f"p{page_no:04d}_table_{index:04d}"
        path = asset_dir / f"{element_id}.png"
        saved = _save_crop(page, box, path, dpi)
        music_likelihood = _music_likelihood(path) if saved else 0.0
        if music_likelihood >= .72:
            elements.append(_element(
                element_id=element_id.replace("_table_", "_music_"), kind="music_score",
                page_no=page_no, bbox=box, width=width, height=height,
                method="pdf_vector_region_music_detection", confidence=music_likelihood,
                asset_path=path.relative_to(output_root).as_posix(),
                extra={"music_score_likelihood": round(music_likelihood, 4)},
            ))
            continue
        reasons: list[dict[str, str]] = []
        rows: list[list[str | None]] = []
        try:
            rows = table.extract() or []
        except Exception:
            reasons.append(_review("TABLE_STRUCTURE_FAILED"))
        column_counts = {len(row) for row in rows}
        if not rows or not column_counts or max(column_counts, default=0) < 2:
            if not any(r["code"] == "TABLE_STRUCTURE_FAILED" for r in reasons):
                reasons.append(_review("TABLE_STRUCTURE_FAILED"))
        elif len(column_counts) > 1 or any(cell is None for row in rows for cell in row):
            reasons.append(_review("TABLE_MERGED_CELL"))
        if not saved:
            reasons.append(_review("EMPTY_IMAGE"))
        confidence = .94 if not reasons else .65
        elements.append(_element(
            element_id=element_id, kind="table", page_no=page_no, bbox=box,
            width=width, height=height, method="pdf_vector_table", confidence=confidence,
            asset_path=path.relative_to(output_root).as_posix() if saved else None,
            reasons=reasons, extra={"table": {"rows": rows, "row_count": len(rows),
                                               "column_count": max(column_counts, default=0)}},
        ))
    return elements


def _ocr_page(page: Any, page_no: int, width: float, height: float,
              asset_dir: Path, output_root: Path, dpi: int, language: str) -> list[dict[str, Any]]:
    page_path = asset_dir / f"p{page_no:04d}_ocr_source.png"
    if not _save_crop(page, [0, 0, width, height], page_path, dpi):
        return [_element(
            element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
            bbox=[0, 0, width, height], width=width, height=height, method="none", confidence=0,
            reasons=[_review("OCR_EMPTY")],
        )]
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return [_element(
            element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
            bbox=[0, 0, width, height], width=width, height=height, method="none", confidence=0,
            asset_path=page_path.relative_to(output_root).as_posix(),
            reasons=[_review("OCR_UNAVAILABLE")],
        )]
    try:
        ocr = PaddleOCR(lang=language, use_doc_orientation_classify=True,
                        use_doc_unwarping=False, use_textline_orientation=True)
        result = ocr.predict(str(page_path))
        items: list[tuple[list[list[float]], str, float]] = []
        for page_result in result:
            data = page_result.json if hasattr(page_result, "json") else {}
            data = data.get("res", data) if isinstance(data, dict) else {}
            polys = data.get("dt_polys", [])
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            items.extend(zip(polys, texts, scores))
    except Exception:
        items = []
    scale = dpi / 72.0
    elements = []
    for index, (poly, text, score) in enumerate(items, 1):
        xs = [float(p[0]) / scale for p in poly]
        ys = [float(p[1]) / scale for p in poly]
        reasons = [] if float(score) >= .85 else [_review("LOW_CONFIDENCE")]
        elements.append(_element(
            element_id=f"p{page_no:04d}_ocr_{index:04d}", kind="paragraph", page_no=page_no,
            bbox=[min(xs), min(ys), max(xs), max(ys)], width=width, height=height,
            text=str(text), method="paddleocr", confidence=float(score), reasons=reasons,
        ))
    if not elements:
        elements.append(_element(
            element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
            bbox=[0, 0, width, height], width=width, height=height, method="paddleocr", confidence=0,
            asset_path=page_path.relative_to(output_root).as_posix(), reasons=[_review("OCR_EMPTY")],
        ))
    return elements


def _iou(a: list[float], b: list[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return intersection / (area_a + area_b - intersection) if area_a + area_b > intersection else 0.0


def _assign_reading_order(elements: list[dict[str, Any]], width: float) -> None:
    if not elements:
        return
    narrow = [e for e in elements if e["bbox"][2] - e["bbox"][0] < width * .56]
    left = [e for e in narrow if (e["bbox"][0] + e["bbox"][2]) / 2 < width * .47]
    right = [e for e in narrow if (e["bbox"][0] + e["bbox"][2]) / 2 > width * .53]
    two_columns = len(left) >= 2 and len(right) >= 2
    if two_columns:
        def key(e: dict[str, Any]) -> tuple[float, float, float]:
            box = e["bbox"]
            spanning = box[2] - box[0] >= width * .56
            if spanning:
                return (box[1], -1.0, box[0])
            # 같은 세로 구간에서는 왼쪽 단 전체를 오른쪽 단보다 먼저 둔다.
            column = 0.0 if (box[0] + box[2]) / 2 < width / 2 else 1.0
            return (math.floor(box[1] / max(1.0, width * .8)) * width * .8, column, box[1])
        elements.sort(key=key)
    else:
        elements.sort(key=lambda e: (round(e["bbox"][1], 1), e["bbox"][0]))
    for order, element in enumerate(elements, 1):
        element["reading_order"] = order
    for i, first in enumerate(elements):
        for second in elements[i + 1:]:
            if first["type"] == second["type"] and _iou(first["bbox"], second["bbox"]) > .72:
                for target in (first, second):
                    if not any(r["code"] == "OVERLAPPING_ELEMENTS" for r in target["review_reasons"]):
                        target["review_reasons"].append(_review("OVERLAPPING_ELEMENTS"))
                        target["review_required"] = True


def _render_review_overlay(page_image: Path, elements: list[dict[str, Any]],
                           destination: Path, scale: float) -> None:
    pillow = _require_pillow()
    if not pillow or not page_image.exists():
        return
    Image, ImageDraw, _ = pillow
    image = Image.open(page_image).convert("RGB")
    draw = ImageDraw.Draw(image)
    for element in elements:
        if not element["review_required"]:
            continue
        box = [round(v * scale) for v in element["bbox"]]
        draw.rectangle(box, outline=(220, 30, 30), width=max(2, round(scale)))
        label = ",".join(r["code"] for r in element["review_reasons"])
        draw.rectangle([box[0], max(0, box[1] - 15), box[0] + min(260, len(label) * 7), box[1]], fill=(255, 245, 180))
        draw.text((box[0] + 2, max(0, box[1] - 14)), label, fill=(150, 0, 0))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)


def _load_ai_adapter(module_name: str | None) -> AIReviewAdapter | None:
    if not module_name:
        return None
    module = importlib.import_module(module_name)
    adapter = getattr(module, "adapter", None)
    if adapter is None or not callable(getattr(adapter, "review", None)):
        raise ValueError(f"{module_name} 모듈에 review()를 가진 adapter 객체가 없습니다.")
    return adapter


def _plumber_text(page: Any, page_no: int, width: float, height: float) -> list[dict[str, Any]]:
    """pdfminer 좌표를 공통 요소 형식으로 변환한다."""
    try:
        lines = page.extract_text_lines(return_chars=True, strip=True) or []
    except Exception:
        lines = []
    sizes = [float(char.get("size", 0)) for line in lines for char in line.get("chars", [])
             if str(char.get("text", "")).strip()]
    median = sorted(sizes)[len(sizes) // 2] if sizes else 10.0
    maximum = max(sizes, default=median)
    elements = []
    for index, line in enumerate(lines, 1):
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        box = [float(line["x0"]), float(line["top"]), float(line["x1"]), float(line["bottom"])]
        line_sizes = [float(c.get("size", 0)) for c in line.get("chars", []) if str(c.get("text", "")).strip()]
        size = max(line_sizes, default=median)
        kind, confidence = _text_kind(text, box, height, maximum, median, size)
        elements.append(_element(
            element_id=f"p{page_no:04d}_text_{index:04d}", kind=kind, page_no=page_no,
            bbox=box, width=width, height=height, text=text, method="pdf_text_layer_pdfminer",
            confidence=confidence, extra={"style": {"max_font_size": round(size, 3)}},
        ))
    return elements


def _pil_crop(page_image: Path, bbox: list[float], destination: Path, scale: float) -> bool:
    pillow = _require_pillow()
    if not pillow or not page_image.exists():
        return False
    Image, _, _ = pillow
    try:
        with Image.open(page_image) as image:
            pixel_box = (
                max(0, round(bbox[0] * scale)), max(0, round(bbox[1] * scale)),
                min(image.width, round(bbox[2] * scale)), min(image.height, round(bbox[3] * scale)),
            )
            if pixel_box[2] - pixel_box[0] < 2 or pixel_box[3] - pixel_box[1] < 2:
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.crop(pixel_box).save(destination)
        return destination.exists() and destination.stat().st_size > 0
    except Exception:
        return False


def _plumber_images(page: Any, page_no: int, width: float, height: float,
                    page_image: Path, asset_dir: Path, output_root: Path,
                    scale: float) -> list[dict[str, Any]]:
    elements = []
    for index, image in enumerate(page.images or [], 1):
        box = [float(image["x0"]), float(image["top"]), float(image["x1"]), float(image["bottom"])]
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        element_id = f"p{page_no:04d}_image_{index:04d}"
        path = asset_dir / f"{element_id}.png"
        saved = _pil_crop(page_image, box, path, scale)
        likelihood = _music_likelihood(path) if saved else 0.0
        kind = "music_score" if likelihood >= .72 else "image"
        reasons = []
        confidence = likelihood if kind == "music_score" else .88
        if .42 <= likelihood < .72:
            reasons.append(_review("MUSIC_AMBIGUOUS"))
            confidence = .6
        if not saved:
            reasons.append(_review("EMPTY_IMAGE"))
            confidence = 0
        elements.append(_element(
            element_id=element_id, kind=kind, page_no=page_no, bbox=box,
            width=width, height=height, method="pdf_image_region_pdfminer",
            confidence=confidence, asset_path=path.relative_to(output_root).as_posix() if saved else None,
            reasons=reasons, extra={"music_score_likelihood": round(likelihood, 4)},
        ))
    return elements


def _plumber_tables(page: Any, page_no: int, width: float, height: float,
                    page_image: Path, asset_dir: Path, output_root: Path,
                    scale: float) -> list[dict[str, Any]]:
    try:
        tables = page.find_tables() or []
    except Exception:
        return []
    elements = []
    for index, table in enumerate(tables, 1):
        box = [float(v) for v in table.bbox]
        element_id = f"p{page_no:04d}_table_{index:04d}"
        path = asset_dir / f"{element_id}.png"
        saved = _pil_crop(page_image, box, path, scale)
        music_likelihood = _music_likelihood(path) if saved else 0.0
        if music_likelihood >= .72:
            elements.append(_element(
                element_id=element_id.replace("_table_", "_music_"), kind="music_score",
                page_no=page_no, bbox=box, width=width, height=height,
                method="pdf_vector_region_music_detection", confidence=music_likelihood,
                asset_path=path.relative_to(output_root).as_posix(),
                extra={"music_score_likelihood": round(music_likelihood, 4)},
            ))
            continue
        reasons = []
        try:
            rows = table.extract() or []
        except Exception:
            rows = []
            reasons.append(_review("TABLE_STRUCTURE_FAILED"))
        column_counts = {len(row) for row in rows}
        if not rows or max(column_counts, default=0) < 2:
            if not reasons:
                reasons.append(_review("TABLE_STRUCTURE_FAILED"))
        elif len(column_counts) > 1 or any(cell is None for row in rows for cell in row):
            reasons.append(_review("TABLE_MERGED_CELL"))
        if not saved:
            reasons.append(_review("EMPTY_IMAGE"))
        elements.append(_element(
            element_id=element_id, kind="table", page_no=page_no, bbox=box,
            width=width, height=height, method="pdf_vector_table_pdfminer",
            confidence=.94 if not reasons else .65,
            asset_path=path.relative_to(output_root).as_posix() if saved else None,
            reasons=reasons, extra={"table": {"rows": rows, "row_count": len(rows),
                                               "column_count": max(column_counts, default=0)}},
        ))
    return elements


def _ocr_image(page_image: Path, page_no: int, width: float, height: float,
               output_root: Path, language: str, dpi: int) -> list[dict[str, Any]]:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return [_element(
            element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
            bbox=[0, 0, width, height], width=width, height=height, method="none", confidence=0,
            asset_path=page_image.relative_to(output_root).as_posix(),
            reasons=[_review("OCR_UNAVAILABLE")],
        )]
    try:
        ocr = PaddleOCR(lang=language, use_doc_orientation_classify=True,
                        use_doc_unwarping=False, use_textline_orientation=True)
        predictions = ocr.predict(str(page_image))
        items = []
        for prediction in predictions:
            data = prediction.json if hasattr(prediction, "json") else {}
            data = data.get("res", data) if isinstance(data, dict) else {}
            items.extend(zip(data.get("dt_polys", []), data.get("rec_texts", []),
                             data.get("rec_scores", [])))
    except Exception:
        items = []
    scale = dpi / 72
    elements = []
    for index, (poly, text, score) in enumerate(items, 1):
        xs, ys = [float(p[0]) / scale for p in poly], [float(p[1]) / scale for p in poly]
        reasons = [] if float(score) >= .85 else [_review("LOW_CONFIDENCE")]
        elements.append(_element(
            element_id=f"p{page_no:04d}_ocr_{index:04d}", kind="paragraph", page_no=page_no,
            bbox=[min(xs), min(ys), max(xs), max(ys)], width=width, height=height,
            text=str(text), method="paddleocr", confidence=float(score), reasons=reasons,
        ))
    return elements or [_element(
        element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
        bbox=[0, 0, width, height], width=width, height=height,
        method="paddleocr", confidence=0, asset_path=page_image.relative_to(output_root).as_posix(),
        reasons=[_review("OCR_EMPTY")],
    )]


def _parse_pdf_fallback(source: Path, output_root: Path, config: ParserConfig) -> Path:
    """PyMuPDF 네이티브 모듈을 실행할 수 없을 때의 pdfplumber/pdfium 백엔드."""
    import pdfplumber  # type: ignore
    import pypdfium2 as pdfium  # type: ignore
    from pypdf import PdfReader  # type: ignore

    source = source.resolve()
    digest = _sha256(source)
    document_id = _safe_id(source.stem, digest)
    document_dir = output_root.resolve() / document_id
    pages_dir, assets_root = document_dir / "pages", document_dir / "assets" / document_id
    overlays_dir = document_dir / "review_overlays"
    for directory in (document_dir, pages_dir, assets_root, overlays_dir):
        directory.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(source))
    if reader.is_encrypted:
        raise ValueError(f"암호가 필요한 PDF는 처리할 수 없습니다: {source}")
    pdfium_doc = pdfium.PdfDocument(str(source))
    adapter = _load_ai_adapter(config.ai_review_module)
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "document_id": document_id,
        "source": {"filename": source.name, "absolute_path": str(source), "sha256": digest},
        "metadata": {str(k).lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()},
        "page_count": len(reader.pages), "processing": {**asdict(config), "backend": "pdfplumber+pdfium"},
        "created_at": datetime.now(timezone.utc).isoformat(), "pages": [], "review_summary": {},
    }
    all_reviews = []
    with pdfplumber.open(str(source)) as plumber_doc:
        for page_index, page in enumerate(plumber_doc.pages):
            page_no, width, height = page_index + 1, float(page.width), float(page.height)
            asset_dir = assets_root / f"page_{page_no:04d}"
            asset_dir.mkdir(parents=True, exist_ok=True)
            page_image = pages_dir / f"page_{page_no:04d}.png"
            render = pdfium_doc[page_index].render(scale=config.dpi / 72)
            pil_image = render.to_pil()
            render_size = {"width": pil_image.width, "height": pil_image.height}
            # 자산 crop과 검수 오버레이 때문에 내부적으로는 항상 렌더링한다.
            pil_image.save(page_image)
            elements = _plumber_text(page, page_no, width, height)
            elements.extend(_plumber_images(page, page_no, width, height, page_image, asset_dir,
                                            document_dir, config.dpi / 72))
            elements.extend(_plumber_tables(page, page_no, width, height, page_image, asset_dir,
                                            document_dir, config.dpi / 72))
            if not any(e["text"].strip() for e in elements) and config.ocr:
                elements.extend(_ocr_image(page_image, page_no, width, height,
                                           document_dir, config.ocr_language, config.dpi))
            if not elements:
                elements.append(_element(
                    element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
                    bbox=[0, 0, width, height], width=width, height=height,
                    method="none", confidence=0, reasons=[_review("PAGE_EMPTY")],
                ))
            for element in elements:
                if element["confidence"] < config.review_threshold and not element["review_required"]:
                    element["review_reasons"].append(_review("LOW_CONFIDENCE"))
                    element["review_required"] = True
            _assign_reading_order(elements, width)
            if adapter:
                for element in elements:
                    if element["review_required"] and element.get("asset_path"):
                        update = adapter.review(str(document_dir / element["asset_path"]), dict(element))
                        if update:
                            for key in ("text", "type", "confidence", "review_required", "review_reasons"):
                                if key in update:
                                    element[key] = update[key]
                            element["extraction_method"] += "+ai_review"
            document["pages"].append({
                "page": page_no,
                "pdf_size": {"width": round(width, 3), "height": round(height, 3), "unit": "pt"},
                "render_size": {**render_size, "unit": "px", "dpi": config.dpi},
                "rotation": int(page.rotation or 0),
                "render_path": page_image.relative_to(document_dir).as_posix() if config.render_pages else None,
                "elements": elements,
            })
            for element in elements:
                if element["review_required"]:
                    all_reviews.append({
                        "document_id": document_id, "page": page_no, "element_id": element["id"],
                        "type": element["type"], "bbox": element["bbox"],
                        "asset_path": element.get("asset_path"), "reasons": element["review_reasons"],
                    })
            _render_review_overlay(page_image, elements, overlays_dir / page_image.name, config.dpi / 72)
            if not config.render_pages:
                page_image.unlink(missing_ok=True)
    pdfium_doc.close()
    reason_counts = Counter(reason["code"] for item in all_reviews for reason in item["reasons"])
    element_count = sum(len(page["elements"]) for page in document["pages"])
    document["review_summary"] = {
        "element_count": element_count, "review_required_count": len(all_reviews),
        "review_required_ratio": round(len(all_reviews) / element_count, 4) if element_count else 0,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    _write_json(document_dir / "document.json", document)
    _write_json(document_dir / "review.json", {
        "schema_version": SCHEMA_VERSION, "document_id": document_id,
        "summary": document["review_summary"], "items": all_reviews,
    })
    return document_dir / "document.json"


def parse_pdf(source: Path, output_root: Path, config: ParserConfig) -> Path:
    if not _fitz_available():
        return _parse_pdf_fallback(source, output_root, config)
    fitz = _require_fitz()
    source = source.resolve()
    digest = _sha256(source)
    document_id = _safe_id(source.stem, digest)
    document_dir = output_root.resolve() / document_id
    pages_dir = document_dir / "pages"
    assets_root = document_dir / "assets" / document_id
    overlays_dir = document_dir / "review_overlays"
    for directory in (document_dir, pages_dir, assets_root, overlays_dir):
        directory.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(source))
    if doc.needs_pass:
        doc.close()
        raise ValueError(f"암호가 필요한 PDF는 처리할 수 없습니다: {source}")
    adapter = _load_ai_adapter(config.ai_review_module)
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "source": {"filename": source.name, "absolute_path": str(source), "sha256": digest},
        "metadata": dict(doc.metadata or {}),
        "page_count": doc.page_count,
        "processing": asdict(config),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pages": [],
        "review_summary": {},
    }
    all_reviews: list[dict[str, Any]] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_no = page_index + 1
        rect = page.rect
        width, height = float(rect.width), float(rect.height)
        page_asset_dir = assets_root / f"page_{page_no:04d}"
        page_asset_dir.mkdir(parents=True, exist_ok=True)
        page_image = pages_dir / f"page_{page_no:04d}.png"
        if config.render_pages:
            pix = page.get_pixmap(dpi=config.dpi, alpha=False)
            pix.save(str(page_image))
            render_size = {"width": pix.width, "height": pix.height}
        else:
            render_size = {"width": round(width * config.dpi / 72), "height": round(height * config.dpi / 72)}
        elements = _extract_text(page, page_no, width, height)
        elements.extend(_extract_images(page, page_no, width, height, page_asset_dir, document_dir, config.dpi))
        elements.extend(_extract_tables(page, page_no, width, height, page_asset_dir, document_dir, config.dpi))
        if not any(e["text"].strip() for e in elements) and config.ocr:
            elements.extend(_ocr_page(page, page_no, width, height, page_asset_dir,
                                      document_dir, config.dpi, config.ocr_language))
        if not elements:
            elements.append(_element(
                element_id=f"p{page_no:04d}_unknown_0001", kind="unknown", page_no=page_no,
                bbox=[0, 0, width, height], width=width, height=height,
                method="none", confidence=0, reasons=[_review("PAGE_EMPTY")],
            ))
        for element in elements:
            if element["confidence"] < config.review_threshold and not element["review_required"]:
                element["review_reasons"].append(_review("LOW_CONFIDENCE"))
                element["review_required"] = True
        _assign_reading_order(elements, width)
        if adapter:
            for element in elements:
                if element["review_required"] and element.get("asset_path"):
                    update = adapter.review(str(document_dir / element["asset_path"]), dict(element))
                    if update:
                        for key in ("text", "type", "confidence", "review_required", "review_reasons"):
                            if key in update:
                                element[key] = update[key]
                        element["extraction_method"] += "+ai_review"
        page_record = {
            "page": page_no,
            "pdf_size": {"width": round(width, 3), "height": round(height, 3), "unit": "pt"},
            "render_size": {**render_size, "unit": "px", "dpi": config.dpi},
            "rotation": int(page.rotation),
            "render_path": page_image.relative_to(document_dir).as_posix() if config.render_pages else None,
            "elements": elements,
        }
        document["pages"].append(page_record)
        for element in elements:
            if element["review_required"]:
                all_reviews.append({
                    "document_id": document_id, "page": page_no, "element_id": element["id"],
                    "type": element["type"], "bbox": element["bbox"],
                    "asset_path": element.get("asset_path"), "reasons": element["review_reasons"],
                })
        if config.render_pages:
            _render_review_overlay(page_image, elements, overlays_dir / page_image.name, config.dpi / 72)
    doc.close()
    reason_counts = Counter(reason["code"] for item in all_reviews for reason in item["reasons"])
    element_count = sum(len(page["elements"]) for page in document["pages"])
    document["review_summary"] = {
        "element_count": element_count,
        "review_required_count": len(all_reviews),
        "review_required_ratio": round(len(all_reviews) / element_count, 4) if element_count else 0,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    _write_json(document_dir / "document.json", document)
    _write_json(document_dir / "review.json", {
        "schema_version": SCHEMA_VERSION, "document_id": document_id,
        "summary": document["review_summary"], "items": all_reviews,
    })
    return document_dir / "document.json"


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _pdf_inputs(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".pdf" else []
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted((p for p in path.glob(pattern) if p.is_file()), key=lambda p: str(p).lower())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="교과서 PDF를 편집·검수용 JSON으로 구조화합니다.")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("parse", help="PDF 또는 폴더를 파싱합니다.")
    command.add_argument("input", type=Path, help="PDF 파일 또는 PDF가 있는 폴더")
    command.add_argument("--output", type=Path, default=Path("output/pdf"), help="출력 루트")
    command.add_argument("--recursive", action="store_true", help="하위 폴더까지 PDF 검색")
    command.add_argument("--dpi", type=int, default=200, help="페이지·자산 렌더링 DPI")
    command.add_argument("--no-ocr", action="store_true", help="텍스트층 없는 페이지도 OCR하지 않음")
    command.add_argument("--no-render-pages", action="store_true", help="전체 페이지 PNG 저장 안 함")
    command.add_argument("--review-threshold", type=float, default=.85, help="이 값 미만은 확인 필요")
    command.add_argument("--ai-review-module", help="선택적 AI adapter 객체가 있는 Python 모듈")
    viewer = sub.add_parser("review-html", help="기존 파싱 결과의 HTML 검수 화면을 생성합니다.")
    viewer.add_argument("output", type=Path, nargs="?", default=Path("output/pdf"), help="파싱 결과 루트")
    audit = sub.add_parser("audit", help="원고의 성취기준·학습 목표·활동과 교육과정 연계를 점검합니다.")
    audit.add_argument("input", type=Path, help="점검할 원고 PDF")
    audit.add_argument("--curriculum", type=Path, required=True, help="2022 개정 교육과정 PDF")
    audit.add_argument("--output", type=Path, default=Path("output/audit"), help="점검 결과 루트")
    audit.add_argument("--ai-module", help="선택적 AI 챗봇 adapter.audit(payload) 모듈")
    audit.add_argument("--textbooks", type=Path, default=Path("★타사 교과서"), help="레이아웃 참고 교과서 폴더")
    audit.add_argument("--related-works", type=Path,
                       help="관련 자료(리메이크·다른 버전 등) 키워드→설명 JSON 파일")
    server = sub.add_parser("serve-audit", help="생성된 점검 결과를 localhost에서 엽니다.")
    server.add_argument("output", type=Path, nargs="?", default=Path("output/audit"), help="점검 결과 루트")
    server.add_argument("--host", default="127.0.0.1", help="기본값은 이 컴퓨터에서만 접근 가능")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")
    team = sub.add_parser("team-serve", help="기준 자료 업로드와 원고 분석을 제공하는 팀용 웹 프로그램")
    team.add_argument("--data", type=Path, default=Path("team_data"), help="업로드·결과·이력 저장 폴더")
    team.add_argument("--host", default="127.0.0.1", help="팀 공유 시 0.0.0.0 사용")
    team.add_argument("--port", type=int, default=8780)
    team.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "team-serve":
        try:
            from team_app import serve_team_app
            serve_team_app(args.data, args.host, args.port, not args.no_open)
            return 0
        except Exception as exc:
            print(f"팀용 웹 프로그램 실행 실패: {exc}", file=sys.stderr)
            return 1
    if args.command == "serve-audit":
        try:
            from audit_server import serve_audit
            serve_audit(args.output, args.host, args.port, not args.no_open)
            return 0
        except Exception as exc:
            print(f"로컬 점검 서버 실행 실패: {exc}", file=sys.stderr)
            return 1
    if args.command == "review-html":
        try:
            from viewer_generator import generate_review_html
            result = generate_review_html(args.output)
            print(f"HTML 검수 화면: {result}")
            return 0
        except Exception as exc:
            print(f"HTML 생성 실패: {exc}", file=sys.stderr)
            return 1
    if args.command == "audit":
        if not args.input.exists() or not args.curriculum.exists():
            print("원고 또는 교육과정 PDF 경로가 없습니다.", file=sys.stderr)
            return 2
        try:
            from curriculum_audit import audit_manuscript
            related_works = (
                json.loads(args.related_works.read_text(encoding="utf-8"))
                if args.related_works else None
            )
            result = audit_manuscript(
                args.input, args.curriculum, args.output, args.ai_module, args.textbooks,
                related_works=related_works,
            )
            print(f"교육과정 점검 완료: {result}")
            print(f"HTML 결과: {result.with_suffix('.html')}")
            return 0
        except Exception as exc:
            print(f"교육과정 점검 실패: {exc}", file=sys.stderr)
            return 1
    if not args.input.exists():
        print(f"입력 경로가 없습니다: {args.input}", file=sys.stderr)
        return 2
    if not 72 <= args.dpi <= 600:
        print("--dpi는 72~600 범위여야 합니다.", file=sys.stderr)
        return 2
    if not 0 <= args.review_threshold <= 1:
        print("--review-threshold는 0~1 범위여야 합니다.", file=sys.stderr)
        return 2
    inputs = _pdf_inputs(args.input, args.recursive)
    if not inputs:
        print("처리할 PDF가 없습니다.", file=sys.stderr)
        return 2
    config = ParserConfig(
        dpi=args.dpi, ocr=not args.no_ocr, review_threshold=args.review_threshold,
        render_pages=not args.no_render_pages, ai_review_module=args.ai_review_module,
    )
    failures = 0
    for index, source in enumerate(inputs, 1):
        try:
            result = parse_pdf(source, args.output, config)
            print(f"[{index}/{len(inputs)}] 완료: {source} -> {result}")
        except Exception as exc:
            failures += 1
            print(f"[{index}/{len(inputs)}] 실패: {source}: {exc}", file=sys.stderr)
    if not failures:
        try:
            from viewer_generator import generate_review_html
            print(f"HTML 검수 화면: {generate_review_html(args.output)}")
        except Exception as exc:
            print(f"HTML 생성 경고: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
