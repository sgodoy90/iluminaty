import io

from PIL import Image

from iluminaty.vision import OCREngine


def _make_img_bytes(w: int = 300, h: int = 200) -> bytes:
    img = Image.new("RGB", (w, h), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_extract_region_applies_zoom_factor(monkeypatch):
    ocr = OCREngine()
    seen = {"w": 0, "h": 0}

    def _fake_extract_text(frame_bytes: bytes, frame_hash=None):
        _ = frame_hash
        img = Image.open(io.BytesIO(frame_bytes))
        seen["w"] = img.width
        seen["h"] = img.height
        return {"text": "ok", "blocks": [], "confidence": 1.0, "engine": "fake"}

    monkeypatch.setattr(ocr, "extract_text", _fake_extract_text)
    source = _make_img_bytes()
    result = ocr.extract_region(source, 10, 10, 100, 50, zoom_factor=2.0)

    assert seen["w"] >= 190
    assert seen["h"] >= 95
    assert result["region_zoom_factor"] == 2.0
