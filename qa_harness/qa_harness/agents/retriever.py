from __future__ import annotations

from typing import Any

from core import DEFAULT_MODEL_NAME, Evidence, EvidenceReport
from tools import PermissionLevel, ToolRegistry

from .base import AgentProfile, BaseAgent

RETRIEVER_PROFILE = AgentProfile(
    role="retriever",
    role_description="根据问题检索知识库相关内容并原样摘录证据片段，不回答问题本身。",
    allowed_tools=("retrieve_knowledge", "retrieve_batch", "get_chunk", "list_sources", "web_search"),
    permission=PermissionLevel.READ_ONLY,
)

RETRIEVER_KB_ONLY_PROFILE = AgentProfile(
    role="retriever",
    role_description="根据问题检索知识库相关内容并原样摘录证据片段，不回答问题本身。",
    allowed_tools=("retrieve_knowledge", "retrieve_batch", "get_chunk", "list_sources"),
    permission=PermissionLevel.READ_ONLY,
)

_MATCH_TYPES = ("vector", "bm25", "hybrid", "web")


class RetrieverAgent(BaseAgent):

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        max_loops: int = 6,
        model_name: str = DEFAULT_MODEL_NAME,
        profile: AgentProfile = RETRIEVER_PROFILE,
    ) -> None:
        super().__init__(profile, tool_registry=tool_registry,
                         max_loops=max_loops, model_name=model_name)


    def _collect_provenance(self) -> dict[str, dict[str, Any]]:
        provenance: dict[str, dict[str, Any]] = {}
        for entry in self.tool_trace:
            result = entry.get("result") or {}
            if not isinstance(result, dict) or not result.get("ok"):
                continue

            chunks: list[dict[str, Any]] = []
            if entry["name"] in ("retrieve_knowledge", "retrieve_batch", "web_search"):
                raw_list = result.get("evidences")
                if isinstance(raw_list, list):
                    chunks = raw_list
            elif entry["name"] == "get_chunk":
                evidence = result.get("evidence")
                if isinstance(evidence, dict):
                    chunks = [evidence]

            for ch in chunks:
                if not isinstance(ch, dict):
                    continue
                cid = ch.get("chunk_id")
                if not isinstance(cid, str) or not cid:
                    continue
                if cid not in provenance or (
                    "score" not in provenance[cid] and "score" in ch
                ):
                    provenance[cid] = ch
        return provenance


    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:
        evidences_raw = raw.get("evidences")
        if not isinstance(evidences_raw, list):
            return {"ok": False, "error": "输出字段 evidences 必须是列表（即使一条也没查到，也要返回 []）"}

        gaps_raw = raw.get("gaps", [])
        if not isinstance(gaps_raw, list):
            return {"ok": False, "error": "输出字段 gaps 必须是字符串列表"}

        escalate = raw.get("escalate", False)
        if not isinstance(escalate, bool):
            return {"ok": False, "error": "escalate 必须是布尔值 true 或 false"}
        escalate_reason = str(raw.get("escalate_reason", "") or "")
        if escalate and not escalate_reason.strip():
            return {"ok": False, "error": "escalate=true 时必须填写 escalate_reason 说明为什么要升档"}

        provenance = self._collect_provenance()

        evidences: list[Evidence] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(evidences_raw):
            if not isinstance(item, dict):
                return {"ok": False, "error": f"evidences[{index}] 不是 JSON 对象：{item!r}"}

            cid = item.get("chunk_id")
            if not isinstance(cid, str) or not cid:
                return {"ok": False, "error": f"evidences[{index}] 缺少合法的 chunk_id 字段"}

            if cid not in provenance:
                return {
                    "ok": False,
                    "error": (
                        f"证据 {cid!r} 没有出现在本轮任何工具返回中——"
                        "retriever 禁止编造片段，也禁止引用未被检索到的 chunk_id"
                    ),
                }

            if cid in seen_ids:
                continue  
            seen_ids.add(cid)

            src = provenance[cid]
            match_type = src.get("match_type")
            try:
                evidence = Evidence(
                    chunk_id=cid,
                    content=src["content"],
                    source=src.get("source", "unknown"),
                    doc_type=src.get("doc_type", ""),
                    chunk_index=int(src.get("chunk_index", -1)),
                    score=float(src.get("score", 0.0)),
                    match_type=match_type if match_type in _MATCH_TYPES else "hybrid",
                )
            except (TypeError, ValueError, KeyError) as e:
                return {"ok": False, "error": f"Evidence 构造失败（chunk_id={cid}）：{e}"}
            evidences.append(evidence)

        try:
            report = EvidenceReport(
                evidences=evidences,
                gaps=[str(g) for g in gaps_raw],
                escalate=escalate,
                escalate_reason=escalate_reason,
            )
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"EvidenceReport 构造失败：{e}"}

        return {"ok": True, "data": report}
