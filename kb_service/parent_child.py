
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from kb_service.splitter import TextSplitter
from kb_service.structured import split_structured


@dataclass
class ParentChildResult:
    parents: list[str]                              
    parent_ids: list[str]                           
    parent_metas: list[dict]                        
    children: list[list[str]]                       
    child_ids: list[list[str]]                      
    child_metas: list[list[dict]]                   


def _make_parent_id(source: str, parent_index: int) -> str:
    return hashlib.md5(f"{source}#parent#{parent_index}".encode("utf-8")).hexdigest()


def _make_child_id(source: str, parent_index: int, child_index: int) -> str:
    return hashlib.md5(
        f"{source}#child#{parent_index}#{child_index}".encode("utf-8")
    ).hexdigest()


def split_parent_child(
    content: str,
    source: str = "unknown",
    doc_type: str = "",
    parent_size: int = 1000,
    child_size: int = 400,
    child_overlap: int = 50,
    parent_mode: str = "paragraph",
) -> ParentChildResult:
    parent_splitter = TextSplitter(chunk_size=parent_size, chunk_overlap=0)
    child_splitter = TextSplitter(chunk_size=child_size, chunk_overlap=child_overlap)

    if parent_mode == "full_doc":
        raw_parents = [content] if content.strip() else []
    else:
        if doc_type == "md" or content.lstrip().startswith("#"):
            raw_parents = parent_splitter.split_markdown(content)
        elif doc_type == "txt":
            raw_parents = parent_splitter.split_smart(content)
        else:
            raw_parents = split_structured(content, target_size=parent_size, overlap=0)

    parents: list[str] = []
    parent_ids: list[str] = []
    parent_metas: list[dict] = []
    all_children: list[list[str]] = []
    all_child_ids: list[list[str]] = []
    all_child_metas: list[list[dict]] = []

    for p_idx, parent_text in enumerate(raw_parents):
        parent_text = parent_text.strip()
        if not parent_text:
            continue

        pid = _make_parent_id(source, p_idx)
        parents.append(parent_text)
        parent_ids.append(pid)
        parent_metas.append({
            "source": source,
            "doc_type": doc_type,
            "parent_index": p_idx,
            "parent_id": pid,
            "char_count": len(parent_text),
        })

        if len(parent_text) <= child_size:
            children = [parent_text]
        else:
            children = child_splitter.split_table_aware(parent_text)

        child_ids_this = []
        child_metas_this = []
        for c_idx, child_text in enumerate(children):
            child_text = child_text.strip()
            if not child_text:
                continue
            cid = _make_child_id(source, p_idx, c_idx)
            child_ids_this.append(cid)
            child_metas_this.append({
                "source": source,
                "doc_type": doc_type,
                "parent_index": p_idx,
                "child_index": c_idx,
                "parent_id": pid,         
                "child_id": cid,
                "char_count": len(child_text),
            })

        all_children.append(children)
        all_child_ids.append(child_ids_this)
        all_child_metas.append(child_metas_this)

    return ParentChildResult(
        parents=parents,
        parent_ids=parent_ids,
        parent_metas=parent_metas,
        children=all_children,
        child_ids=all_child_ids,
        child_metas=all_child_metas,
    )



def _test() -> None:
    md = """# 第一章
这是第一章的内容，介绍了背景知识。

## 1.1 子节
这是子节的详细内容，比较长一些，会被切成多个 child chunk。
""" * 3
    result = split_parent_child(md, source="test.md", doc_type="md", parent_size=120, child_size=80)

    print(f"parents: {len(result.parents)}")
    print(f"children per parent: {[len(c) for c in result.children]}")

    result2 = split_parent_child(md, source="test.md", doc_type="md", parent_size=120, child_size=80)
    assert result.parent_ids == result2.parent_ids, "parent IDs 应确定性"
    assert result.child_ids == result2.child_ids, "child IDs 应确定性"
    print("✓ ID 确定性验证通过")

    result3 = split_parent_child(md, source="test.md", parent_mode="full_doc", parent_size=120, child_size=80)
    assert len(result3.parents) == 1, f"full_doc 应有 1 个 parent，实际 {len(result3.parents)}"
    print("✓ full_doc 模式验证通过")

    short = "非常短的一段。"
    result4 = split_parent_child(short, source="test.txt", doc_type="txt", parent_size=1000, child_size=400)
    assert len(result4.parents) == 1
    assert len(result4.children[0]) == 1, "小 parent 应只有一个 child"
    print("✓ 小 parent 不切 child")

    print("\n全部 parent_child 测试通过！")


if __name__ == "__main__":
    _test()
