import re

_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


class TextSplitter:

    def __init__(
            self,
            chunk_size: int = 400,  
            chunk_overlap: int = 50,  
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap


    _HEADING_PATTERNS = [
        (re.compile(r'^(#{1,6})\s+(.+)$'), lambda m: (len(m.group(1)), m.group(2))),
        (re.compile(r'^(第[一二三四五六七八九十百千万\d]+[章节部分篇])\s*(.+)?$'),
         lambda m: (2, f"{m.group(1)} {m.group(2) or ''}".strip())),
        (re.compile(r'^(第[一二三四五六七八九十百千万\d]+[条文])\s*(.+)?$'),
         lambda m: (3, f"{m.group(1)} {m.group(2) or ''}".strip())),
        (re.compile(r'^([一二三四五六七八九十]+[、.])\s*(.+)$'),
         lambda m: (2, m.group(1) + m.group(2))),
        (re.compile(r'^[（(]([一二三四五六七八九十]+)[）)]\s*(.+)$'),
         lambda m: (3, f"（{m.group(1)}）{m.group(2)}")),
        (re.compile(r'^(\d{1,2}\.\d+\.\d+\s)(.+)$'),
         lambda m: (4, m.group(1) + m.group(2))),
        (re.compile(r'^(\d{1,2}\.\d+\s)(.+)$'),
         lambda m: (3, m.group(1) + m.group(2))),
        (re.compile(r'^(\d{1,2}、)(.+)$'),
         lambda m: (4, m.group(1) + m.group(2))),      
        (re.compile(r'^(\d{1,2}\.\s+)(.+)$'),
         lambda m: (4, m.group(1) + m.group(2))),      
        (re.compile(r'^[（(](\d{1,2})[）)]\s*(.+)$'),
         lambda m: (3, f"（{m.group(1)}）{m.group(2)}")),
    ]

    def detect_headings(self, text: str) -> list[tuple[int, str]]:
        results: list[tuple[int, str]] = []
        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue
            if len(stripped) > 60:
                continue
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if next_line and next_line[0] in "。！？.!?":
                continue

            for pattern, extractor in self._HEADING_PATTERNS:
                m = pattern.match(stripped)
                if m:
                    level, heading = extractor(m)
                    results.append((level, heading))
                    break  
        return results

    def normalize_to_markdown(self, text: str, headings: list[tuple[int, str]]) -> str:
        if not headings:
            return text
        heading_map: dict[str, int] = {}
        for level, ht in headings:
            if ht not in heading_map or level < heading_map[ht]:
                heading_map[ht] = level

        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            level = heading_map.get(stripped)
            if level is not None and not stripped.startswith("#"):
                out.append(f"{'#' * level} {stripped}")
            else:
                out.append(line)
        return "\n".join(out)

    def split_smart(self, text: str) -> list[str]:
        headings = self.detect_headings(text)
        if headings:
            normalized = self.normalize_to_markdown(text, headings)
            return self.split_markdown(normalized)
        if any(_TABLE_LINE.match(line) for line in text.split("\n")):
            chunks = self.split_table_aware(text)
            if chunks:
                return chunks
        try:
            from kb_service.structured import split_structured
            chunks = split_structured(text, target_size=self.chunk_size, overlap=self.chunk_overlap)
            if chunks:
                return chunks
        except Exception:
            pass
        return self.split_by_separator(text)


    def split_by_separator(self, text: str, separators: list[str] = None) -> list[str]:
        if separators is None:
            separators = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " "]

        def split_recursive(text: str, sep_idx: int = 0) -> list[str]:
            text = text.strip()
            if not text:
                return []
            if len(text) <= self.chunk_size:
                return [text]
            if sep_idx >= len(separators):
                step = max(1, self.chunk_size - self.chunk_overlap)
                return [text[i:i + self.chunk_size]
                        for i in range(0, len(text), step)
                        if text[i:i + self.chunk_size].strip()]

            sep = separators[sep_idx]
            parts = [p + sep if sep != " " else p + " " for p in text.split(sep)]
            result: list[str] = []
            current = ""
            for part in parts:
                if len(part) > self.chunk_size:
                    if current.strip():
                        result.append(current.strip())
                        current = ""
                    result.extend(split_recursive(part, sep_idx + 1))
                elif len(current) + len(part) <= self.chunk_size:
                    current += part
                else:
                    if current.strip():
                        result.append(current.strip())
                    current = part
            if current.strip():
                result.append(current.strip())
            return result

        return split_recursive(text)


    def split_table_aware(self, text: str) -> list[str]:
        lines = text.split("\n")
        out: list[str] = []
        buf: list[str] = []
        ctx = ""

        def note_ctx(line: str) -> None:
            nonlocal ctx
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]") or stripped.startswith("#"):
                ctx = stripped

        def flush_text() -> None:
            if not buf:
                return
            block = "\n".join(buf).strip()
            buf.clear()
            if block:
                out.extend(self.split_by_separator(block))

        i = 0
        while i < len(lines):
            if not _TABLE_LINE.match(lines[i]):
                note_ctx(lines[i])
                buf.append(lines[i])
                i += 1
                continue
            flush_text()
            rows: list[str] = []
            while i < len(lines) and _TABLE_LINE.match(lines[i]):
                rows.append(lines[i])
                i += 1
            out.extend(self._split_table(rows, ctx))
        flush_text()
        return [c for c in out if c.strip()]

    def _split_table(self, rows: list[str], ctx: str = "") -> list[str]:
        if len(rows) <= 2:
            return ["\n".join(rows)]
        header = rows[0]
        has_sep = bool(_TABLE_SEP.match(rows[1]))
        prefix = header + ("\n" + rows[1] if has_sep else "")
        body = rows[2:] if has_sep else rows[1:]
        if ctx:
            prefix = ctx + "\n" + prefix

        budget = max(1, self.chunk_size - len(prefix) - 1)
        chunks: list[str] = []
        current: list[str] = []
        cur_len = 0
        for row in body:
            row_len = len(row) + 1
            if current and cur_len + row_len > budget:
                chunks.append(prefix + "\n" + "\n".join(current))
                current, cur_len = [], 0
            current.append(row)
            cur_len += row_len
        if current:
            chunks.append(prefix + "\n" + "\n".join(current))
        return chunks or [prefix]

    def split_by_tokens(self, text: str, model: str = "gpt-4.1") -> list[str]:
        import tiktoken

        encoding = tiktoken.encoding_for_model(model)
        tokens = encoding.encode(text)

        max_tokens = self.chunk_size  

        chunks = []
        start = 0

        while start < len(tokens):
            end = min(start + max_tokens, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = encoding.decode(chunk_tokens)
            chunks.append(chunk_text)

            start = end - self.chunk_overlap

        return chunks

    def split_markdown(self, text: str) -> list[str]:
        chunks = []
        current_section = []
        current_headers = []

        lines = text.split("\n")

        for line in lines:
            if line.strip().startswith('<!--') and line.strip().endswith('-->'):
                continue
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)

            if header_match:
                if current_section:
                    content = "\n".join(current_section).strip()
                    if content:
                        header_path = " > ".join(current_headers)
                        full_content = f"[{header_path}]\n{content}" if header_path else content

                        if len(full_content) > self.chunk_size * 2:
                            sub_chunks = self.split_table_aware(full_content)
                            chunks.extend(sub_chunks)
                        else:
                            chunks.append(full_content)

                level = len(header_match.group(1))
                title = header_match.group(2)
                current_headers = current_headers[:level - 1] + [title]
                current_section = [line]
            else:
                current_section.append(line)

        if current_section:
            content = "\n".join(current_section).strip()
            if content:
                header_path = " > ".join(current_headers)
                full_content = f"[{header_path}]\n{content}" if header_path else content

                if len(full_content) > self.chunk_size * 2:
                    sub_chunks = self.split_table_aware(full_content)
                    chunks.extend(sub_chunks)
                else:
                    chunks.append(full_content)

        return [c for c in chunks if c.strip()]

