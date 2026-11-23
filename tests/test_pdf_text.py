"""Bounded PDF text inspection: page selection, parser limits and no OCR."""
from __future__ import annotations

import io
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ordax_dev_agent.pdf_text import PdfTextError, extract_pdf_text


def sample_pdf(texts: list[str]) -> bytes:
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        if not text:
            continue
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): writer._add_object(font),
            }),
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 30 200 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class PdfTextTests(unittest.TestCase):
    def test_pdf_page_selection_and_metadata(self):
        raw = sample_pdf(["First", "Second", "Third", "Fourth"])
        body, details = extract_pdf_text(raw)
        self.assertIn("[Page 1]\nFirst", body)
        self.assertIn("[Page 3]\nThird", body)
        self.assertNotIn("Fourth", body)
        self.assertEqual(4, details["page_count"])
        self.assertEqual(3, details["pages_returned"])
        self.assertFalse(details["ocr_performed"])
        self.assertFalse(details["may_require_ocr"])

        subset, details = extract_pdf_text(raw, page_start=2, page_end=2)
        self.assertEqual("[Page 2]\nSecond", subset)
        self.assertEqual(2, details["page_end"])

    def test_scanned_like_blank_page_signals_ocr_not_done(self):
        raw = sample_pdf([""])
        text, meta = extract_pdf_text(raw)
        self.assertEqual("[Page 1]\n", text)
        self.assertTrue(meta["may_require_ocr"])
        self.assertFalse(meta["ocr_performed"])

    def test_bounded_pdf_fails_closed(self):
        raw = sample_pdf(["One"] * 8)
        for selection in (
            {"page_start": 0}, {"page_start": 1, "page_end": 7},
            {"page_start": 4, "page_end": 3},
            {"page_start": 50}, {"page_start": True},
            {"page_end": "2"}, {"page_start": 1, "page_end": False},
        ):
            with self.subTest(selection=selection), self.assertRaises(PdfTextError):
                extract_pdf_text(raw, **selection)
        for corrupted in (b"broken", b"%PDF-fake", b"%PDF-" + b"X" * (8 * 1024 * 1024)):
            with self.subTest(size=len(corrupted)), self.assertRaises(PdfTextError):
                extract_pdf_text(corrupted)

    def test_pdf_runtime_limits_are_local_to_this_read(self):
        from pypdf import get_configuration
        original = get_configuration()
        raw = sample_pdf(["Ordax"])
        text, _ = extract_pdf_text(raw)
        self.assertIn("Ordax", text)
        self.assertEqual(original, get_configuration())


if __name__ == "__main__":
    unittest.main()
