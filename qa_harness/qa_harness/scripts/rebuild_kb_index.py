import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.kb_index import build_kb_index  # noqa: E402


def main() -> None:
    g_n, f_n = build_kb_index()
    print(f"重建完成：glossary {g_n} 条，fewshot {f_n} 条")


if __name__ == "__main__":
    main()