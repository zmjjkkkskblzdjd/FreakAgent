from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from .config import config
from .schemas import ParseOptions


class ParseTimeoutError(Exception):
    pass


class ParseFailedError(Exception):
    pass


@dataclass
class ParseOutcome:
    markdown: str
    page_count: Optional[int]
    duration_ms: int


_converters: "OrderedDict[tuple, object]" = OrderedDict()
_converters_lock = threading.Lock()
_MAX_CONVERTER_VARIANTS = 4


def _build_converter(do_ocr: bool, do_table_structure: bool, do_formula: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = do_table_structure
    pipeline_options.do_formula_enrichment = do_formula

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def _detect_pdf_has_text_layer(data: bytes) -> bool:
    try:
        import pymupdf
    except ImportError:
        return True  

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        n = doc.page_count
        if n == 0:
            return False
        if n <= 5:
            indices = list(range(n))
        else:
            mid = n // 2
            indices = [0, 1, mid - 1, mid, n - 1]
        ok = sum(1 for i in indices if len(doc[i].get_text().strip()) >= 50)
        return ok >= 3
    finally:
        doc.close()


def _get_converter(options: ParseOptions):
    key = (options.do_ocr, options.do_table_structure, options.do_formula)
    with _converters_lock:
        converter = _converters.get(key)
        if converter is None:
            converter = _build_converter(*key)
            _converters[key] = converter
            while len(_converters) > _MAX_CONVERTER_VARIANTS:
                _converters.popitem(last=False)
        else:
            _converters.move_to_end(key)
    return converter


def warmup() -> None:
    _get_converter(ParseOptions())


def model_loaded() -> bool:
    return bool(_converters)


def _convert_sync(data: bytes, filename: str, options: ParseOptions) -> ParseOutcome:
    from docling.datamodel.base_models import DocumentStream
    from docling_core.types.doc import ImageRefMode

    if filename.lower().endswith(".pdf"):
        has_text_layer = _detect_pdf_has_text_layer(data)
        effective_ocr = options.do_ocr and not has_text_layer
    else:
        effective_ocr = options.do_ocr

    converter = _get_converter(options.model_copy(update={"do_ocr": effective_ocr}))
    source = DocumentStream(name=filename, stream=BytesIO(data))

    convert_kwargs: dict = {}
    if options.page_range is not None:
        start, end = options.page_range
        convert_kwargs["page_range"] = (start, end)

    started = time.perf_counter()
    try:
        result = converter.convert(source, **convert_kwargs)
    except Exception as exc:  
        raise ParseFailedError(f"{type(exc).__name__}: {exc}") from None
    duration_ms = int((time.perf_counter() - started) * 1000)

    image_mode = (
        ImageRefMode.EMBEDDED if options.extract_images else ImageRefMode.PLACEHOLDER
    )
    markdown = result.document.export_to_markdown(image_mode=image_mode)

    page_count = None
    try:
        page_count = len(result.pages) if result.pages is not None else None
    except Exception:
        page_count = None

    return ParseOutcome(markdown=markdown, page_count=page_count, duration_ms=duration_ms)


async def convert_to_markdown(
    data: bytes, filename: str, options: ParseOptions
) -> ParseOutcome:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_convert_sync, data, filename, options),
            timeout=config.PARSE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise ParseTimeoutError(
            f"解析超过 {config.PARSE_TIMEOUT}s 上限"
        ) from None
