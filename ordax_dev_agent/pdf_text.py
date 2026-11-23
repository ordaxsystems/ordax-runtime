"""On-demand, bounded PDF text reader used by the existing computer.text_read.

This adapter is read-only. It does not execute embedded actions, fetch external
resources, render pages, OCR images or run another service. Untrusted document
structures have conservative pypdf parsing and decompression limits.
"""
from __future__ import annotations

import io
from typing import Any


_MAX_PDF_BYTES = 8 * 1024 * 1024
_MAX_PDF_PAGES = 1000
_MAX_PAGES_PER_READ = 6
_MAX_TEXT_BYTES = 256 * 1024
_MAX_PAGE_STREAM = 768 * 1024


class PdfTextError(ValueError):
    """PDF inspection rejected or unsupported."""


def extract_pdf_text(
    raw: bytes,
    *,
    page_start: int = 1,
    page_end: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Read at most six selected pages from a size-bounded PDF.

    The page selection is 1-based and inclusive. Omitted page_end reads three
    pages at most, returning total_pages for explicit continuation.
    """
    if type(page_start) is not int or not 1 <= page_start <= _MAX_PDF_PAGES:
        raise PdfTextError("page_start must be an integer from 1 to 1000")
    if page_end is not None and (
        type(page_end) is not int or not page_start <= page_end <= _MAX_PDF_PAGES
    ):
        raise PdfTextError("page_end must be an integer >= page_start and <= 1000")
    if page_end is not None and page_end - page_start + 1 > _MAX_PAGES_PER_READ:
        raise PdfTextError("PDF page selection exceeds six pages")
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_PDF_BYTES:
        raise PdfTextError("PDF file exceeds the permitted inspection size")
    if not raw.startswith(b"%PDF-"):
        raise PdfTextError("invalid PDF header")

    # This pure-Python dependency is imported only for an actual PDF read. No
    # startup-time PDF parsing, secondary service or host office integration.
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import PdfReadError

    try:
        with apply_configuration(
            maximum_declared_stream_length=1_000_000,
            array_based_stream_maximum_output_length=1_000_000,
            zlib_maximum_output_length=1_000_000,
            zlib_maximum_recovery_input_length=256_000,
            lzw_maximum_output_length=1_000_000,
            run_length_maximum_output_length=1_000_000,
            brotli_maximum_output_length=1_000_000,
            jbig2_maximum_output_length=1_000_000,
            image_maximum_buffer_size=1_000_000,
            stream_filters_maximum_length=6,
            stream_decoding_work_maximum_length=4_000_000,
            stream_parsing_work_maximum_count=70_000,
            array_maximum_element_count=15_000,
            dictionary_maximum_element_count=15_000,
            page_tree_maximum_entries=2_000,
            page_tree_maximum_depth=30,
            page_tree_maximum_page_entries=5_000,
            xform_maximum_invocations_per_extraction=50,
        ):
            with PdfReader(
                io.BytesIO(raw), strict=True, root_object_recovery_limit=1000,
                xref_stream_maximum_entries=10_000,
            ) as reader:
                if reader.is_encrypted:
                    raise PdfTextError("encrypted PDF is not supported")
                total_pages = len(reader.pages)
                if not 1 <= total_pages <= _MAX_PDF_PAGES:
                    raise PdfTextError("PDF page count exceeds inspection limit")
                if page_start > total_pages:
                    raise PdfTextError("page_start is beyond the last PDF page")
                last_page = min(page_end or page_start + 2, total_pages)
                pieces: list[str] = []
                total_encoded = 0
                scanned_only = True
                for number in range(page_start, last_page + 1):
                    page = reader.pages[number - 1]
                    contents = page.get_contents()
                    if contents is not None:
                        decoded = contents.get_data()
                        if len(decoded) > _MAX_PAGE_STREAM:
                            raise PdfTextError("PDF page stream is too large to inspect")
                    text = page.extract_text() or ""
                    if not isinstance(text, str):
                        raise PdfTextError("PDF page extraction returned invalid text")
                    if text.strip():
                        scanned_only = False
                    fragment = f"[Page {number}]\n{text}"
                    total_encoded += len(fragment.encode("utf-8"))
                    if total_encoded > _MAX_TEXT_BYTES:
                        raise PdfTextError("extracted PDF text exceeds inspection limit")
                    pieces.append(fragment)
                return "\n\n".join(pieces), {
                    "format": "pdf",
                    "page_count": total_pages,
                    "page_start": page_start,
                    "page_end": last_page,
                    "pages_returned": len(pieces),
                    "may_require_ocr": scanned_only,
                    "ocr_performed": False,
                }
    except PdfTextError:
        raise
    except (PdfReadError, OSError, KeyError, TypeError, ValueError, OverflowError) as error:
        # Do not expose untrusted PDF fragments or machine-local details.
        raise PdfTextError("PDF document is invalid or cannot be inspected safely") from error
