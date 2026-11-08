import asyncio
import os
import threading
from pathlib import Path

import pymupdf  

try:
    from dotenv import load_dotenv
    _PKG_ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(_PKG_ROOT / ".env")
    load_dotenv(_PKG_ROOT / "qa_harness" / ".env")
    load_dotenv(_PKG_ROOT / "DataAnalyzeAgent" / "DataAnalyzeAgent" / ".env")
except ImportError:
    pass  

USE_DOC_PARSER = os.environ.get("USE_DOC_PARSER", "true").lower() == "true"


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: dict = {}
    def _runner():
        try:
            result_box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - 原样透传到调用方
            result_box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value")



def load_text_file(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def load_markdown(path: str) -> str:
    content = load_text_file(path)
    return content  



SCANNED_PDF_TEXT_THRESHOLD = 50
SCANNED_PDF_MIN_PAGES = 3

_ppstructure_engine = None


def _get_ppstructure_engine():
    global _ppstructure_engine
    if _ppstructure_engine is not None:
        return _ppstructure_engine

    import os
    os.environ['PADDLEX_DISABLE_LANGCHAIN_SHIM'] = '1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

    try:
        from paddleocr import PPStructureV3
    except ImportError:
        raise ImportError(
            "检测到扫描版 PDF，但未安装 PaddleOCR。\n"
            "请执行：pip install paddleocr paddlepaddle paddlex[ocr]"
        )

    _ppstructure_engine = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_seal_recognition=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        lang='ch',
    )
    return _ppstructure_engine


def _sample_pdf_pages(doc, n_sample: int = 5) -> list[int]:
    total = doc.page_count
    if total <= n_sample:
        return list(range(total))
    mid = total // 2
    return [0, 1, mid - 1, mid, total - 1]


def _is_scanned_pdf(path: str) -> bool:
    doc = pymupdf.open(path)
    try:
        if doc.page_count == 0:
            return True
        pages = _sample_pdf_pages(doc)
        ok = sum(1 for i in pages if len(doc[i].get_text().strip()) >= SCANNED_PDF_TEXT_THRESHOLD)
        return ok < SCANNED_PDF_MIN_PAGES
    finally:
        doc.close()


def _detect_heading(span: dict, page_font_sizes: list) -> int:
    if not page_font_sizes:
        return 0
    from collections import Counter
    base_size = Counter(round(s, 1) for s in page_font_sizes).most_common(1)[0][0]
    size = span.get("size", base_size)
    flags = span.get("flags", 0)
    is_bold = bool(flags & 16)  

    if size >= base_size * 1.8:
        return 1
    if size >= base_size * 1.5:
        return 2
    if size >= base_size * 1.25 and is_bold:
        return 3
    if size >= base_size * 1.1 and is_bold:
        return 4
    return 0


def _extract_page_structure(page) -> str:
    md_parts = []

    table_bboxes = []
    try:
        tables = page.find_tables()
        for tab in tables.tables:
            table_bboxes.append(pymupdf.Rect(tab.bbox))
            rows = tab.extract()
            if not rows:
                continue
            header = rows[0]
            md_table = ["| " + " | ".join(str(c or "") for c in header) + " |"]
            md_table.append("|" + "|".join(["---"] * len(header)) + "|")
            for row in rows[1:]:
                md_table.append("| " + " | ".join(str(c or "") for c in row) + " |")
            md_parts.append(("\n".join(md_table), table_bboxes[-1].y0))
    except Exception:
        pass

    text_dict = page.get_text("dict")
    spans_info = []  
    font_sizes = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text", "").strip()
                if not t:
                    continue
                size = span.get("size", 12)
                font_sizes.append(size)
                spans_info.append({
                    "y0": span["bbox"][1],
                    "x0": span["bbox"][0],
                    "bbox": pymupdf.Rect(span["bbox"]),
                    "text": t,
                    "size": size,
                    "flags": span.get("flags", 0),
                })

    text_spans = []
    for s in spans_info:
        in_table = any(s["bbox"].intersects(tb) for tb in table_bboxes)
        if not in_table:
            text_spans.append(s)

    text_spans.sort(key=lambda s: (round(s["y0"], 1), s["x0"]))

    merged_lines = []
    for s in text_spans:
        if merged_lines and abs(s["y0"] - merged_lines[-1]["y0"]) < 2:
            merged_lines[-1]["spans"].append(s)
        else:
            merged_lines.append({"y0": s["y0"], "spans": [s]})

    for line in merged_lines:
        spans = sorted(line["spans"], key=lambda x: x["x0"])
        text = " ".join(sp["text"] for sp in spans).strip()
        if not text:
            continue
        largest = max(spans, key=lambda sp: sp["size"])
        level = _detect_heading(largest, font_sizes)
        if level == 0:
            md_parts.append((text, line["y0"]))
        else:
            md_parts.append(("#" * level + " " + text, line["y0"]))

    md_parts.sort(key=lambda p: p[1])
    return "\n\n".join(p[0] for p in md_parts)


def _load_pdf_text_layer(path: str) -> str:
    doc = pymupdf.open(path)
    try:
        parts = []
        for i, page in enumerate(doc):
            md = _extract_page_structure(page)
            if md.strip():
                parts.append(f"<!-- 第{i + 1}页 -->\n{md}")
        return "\n\n".join(parts)
    finally:
        doc.close()


def _table_html_to_markdown(html_str: str) -> str:
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.current_row = []
            self.current_cell = []
            self.in_cell = False

        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self.current_row = []
            elif tag in ('td', 'th'):
                self.in_cell = True
                self.current_cell = []

        def handle_endtag(self, tag):
            if tag == 'tr':
                if self.current_row:
                    self.rows.append(self.current_row)
            elif tag in ('td', 'th'):
                self.in_cell = False
                self.current_row.append(''.join(self.current_cell).strip())

        def handle_data(self, data):
            if self.in_cell:
                self.current_cell.append(data)

    parser = TableParser()
    parser.feed(html_str)
    if not parser.rows:
        return ''

    header = parser.rows[0]
    md = ['| ' + ' | '.join(header) + ' |']
    md.append('|' + '|'.join(['---'] * len(header)) + '|')
    for row in parser.rows[1:]:
        md.append('| ' + ' | '.join(row) + ' |')
    return '\n'.join(md)


def _detect_heading_level(text: str) -> int:
    import re
    t = text.strip()

    if len(t) <= 12 and not re.match(r'^[第\(（\d一二三四五六七八九十]', t):
        return 1

    if re.match(r'^第[一二三四五六七八九十百\d]+[章节部分]', t):
        return 2

    if re.match(r'^[一二三四五六七八九十]+、', t):
        return 2
    if re.match(r'^\d+\.\s', t):
        return 2

    if re.match(r'^[\(（][一二三四五六七八九十]+[\)）]', t):
        return 3
    if re.match(r'^\d+\.\d+[\s\.]', t):
        return 3

    if re.match(r'^\d+\.\d+\.\d+', t):
        return 4
    if re.match(r'^[\(（]\d+[\)）]', t):
        return 4

    return 2


def _load_pdf_with_ocr(path: str) -> str:
    import numpy as np
    from PIL import Image
    import io

    engine = _get_ppstructure_engine()
    doc = pymupdf.open(path)
    parts = []
    try:
        for i, page in enumerate(doc):
            mat = pymupdf.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            img_array = np.array(img)

            result = engine.predict(img_array)
            if not result:
                continue

            item = result[0]
            page_md = []
            for block in item['parsing_res_list']:
                label = block.label
                content = str(block.content).strip()
                if not content:
                    continue

                if label == 'paragraph_title':
                    level = _detect_heading_level(content)
                    page_md.append("#" * level + " " + content)
                elif label == 'table':
                    md_table = _table_html_to_markdown(content)
                    if md_table:
                        page_md.append(md_table)
                    else:
                        page_md.append(content)
                else:
                    page_md.append(content)

            if page_md:
                parts.append(f"<!-- 第{i + 1}页 · OCR -->\n" + "\n\n".join(page_md))
    finally:
        doc.close()

    return "\n\n".join(parts)


def _load_pdf_legacy(path: str) -> str:
    if _is_scanned_pdf(path):
        return _load_pdf_with_ocr(path)
    return _load_pdf_text_layer(path)


async def _load_pdf_via_service(path: str) -> str:
    from .doc_parser_client import parse_file
    return await parse_file(path)


def load_pdf(path: str) -> str:
    if USE_DOC_PARSER:
        return _run_coro_sync(_load_pdf_via_service(path))
    return _load_pdf_legacy(path)



def _load_word_legacy(path: str) -> str:
    try:
        from docx import Document

        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        tables_text = []
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join([cell.text for cell in row.cells])
                tables_text.append(row_text)

        full_text = "\n\n".join(paragraphs)
        if tables_text:
            full_text += "\n\n【表格数据】\n" + "\n".join(tables_text)

        return full_text

    except ImportError:
        raise ImportError("请安装 python-docx：pip install python-docx")


async def _load_word_via_service(path: str) -> str:
    from .doc_parser_client import parse_file
    return await parse_file(path)


def load_word(path: str) -> str:
    if USE_DOC_PARSER:
        return _run_coro_sync(_load_word_via_service(path))
    return _load_word_legacy(path)



async def _load_file_via_service(path: str) -> str:
    from .doc_parser_client import parse_file
    return await parse_file(path)


def load_via_doc_parser(path: str) -> str:
    if not USE_DOC_PARSER:
        ext = Path(path).suffix.lower()
        raise NotImplementedError(
            f"{ext} 格式需要 doc_parser 微服务支持，请设置 USE_DOC_PARSER=true 后重试"
        )
    return _run_coro_sync(_load_file_via_service(path))



def load_html(path: str) -> str:
    if USE_DOC_PARSER:
        return _run_coro_sync(_load_file_via_service(path))
    from bs4 import BeautifulSoup
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main_content = soup.find("main") or soup.find("article") or soup.find("body")
    text = (main_content or soup).get_text(separator="\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)


def load_csv(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_webpage(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RAGBot/1.0)"
    }

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    main_content = soup.find("main") or soup.find("article") or soup.find("body")

    if main_content:
        text = main_content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)



class DocumentLoader:

    LOADERS = {
        ".txt": load_text_file,
        ".md": load_markdown,
        ".csv": load_csv,
        ".pdf": load_pdf,
        ".docx": load_word,
        ".html": load_html,
        ".htm": load_html,
        ".pptx": load_via_doc_parser,
        ".xlsx": load_via_doc_parser,
        ".png": load_via_doc_parser,
        ".jpg": load_via_doc_parser,
        ".jpeg": load_via_doc_parser,
        ".tif": load_via_doc_parser,
        ".tiff": load_via_doc_parser,
        ".bmp": load_via_doc_parser,
    }

    @classmethod
    def load(cls, source: str) -> dict:
        if source.startswith("http://") or source.startswith("https://"):
            content = load_webpage(source)
            doc_type = "webpage"
        else:
            path = Path(source)
            suffix = path.suffix.lower()

            loader = cls.LOADERS.get(suffix)
            if not loader:
                raise ValueError(f"不支持的文件格式：{suffix}")

            content = loader(str(path))
            doc_type = suffix[1:]  
            if doc_type == "pdf":
                doc_type = "md"
            if USE_DOC_PARSER and doc_type not in ("txt", "md"):
                doc_type = "md"

        return {
            "content": content,
            "source": source,
            "type": doc_type,
            "char_count": len(content),
            "word_count": len(content.split())
        }

    @classmethod
    def load_directory(
            cls,
            dir_path: str,
            extensions: list = None,
            exclude_dirs: list = None,
    ) -> list[dict]:
        extensions = extensions or [ext.lower() for ext in cls.LOADERS.keys()]
        exclude_dirs = set(exclude_dirs) if exclude_dirs else set()
        documents = []

        def _is_excluded(path: Path) -> bool:
            for parent in path.parents:
                if parent.name in exclude_dirs:
                    return True
            return False

        for path in Path(dir_path).rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            if _is_excluded(path):
                continue
            try:
                doc = cls.load(str(path))
                documents.append(doc)
                print(f"✅ 加载：{path.name} ({doc['char_count']} 字符)")
            except Exception as e:
                print(f"❌ 失败：{path.name} - {e}")

        return documents
