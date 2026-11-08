from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge import VectorStore, HybridRetriever
from memory import LongTermMemory



@dataclass
class HealthIssue:
    level: str          
    category: str       
    description: str    
    suggestion: str     
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HealthReport:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks_run: list[str] = field(default_factory=list)
    issues: list[HealthIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "warn")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "info")

    def by_category(self) -> dict[str, list[HealthIssue]]:
        groups: dict[str, list[HealthIssue]] = {}
        for issue in self.issues:
            groups.setdefault(issue.category, []).append(issue)
        return groups

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"  健康巡检报告  {self.timestamp[:19]}",
            f"  运行检查: {', '.join(self.checks_run)}",
            "=" * 60,
            "",
        ]
        total = len(self.issues)
        if total == 0:
            lines.append("  ✅ 未发现任何问题，系统状态良好。")
        else:
            lines.append(f"  共发现 {total} 个问题 "
                         f"（error={self.error_count}, warn={self.warn_count}, info={self.info_count}）")
            lines.append("")
            for cat, items in self.by_category().items():
                e = sum(1 for i in items if i.level == "error")
                w = sum(1 for i in items if i.level == "warn")
                lines.append(f"  [{cat}] {len(items)} 项（e={e}, w={w}）")
                for item in items[:5]:  
                    icon = "❌" if item.level == "error" else ("⚠️" if item.level == "warn" else "ℹ️")
                    lines.append(f"    {icon} {item.description}")
                    lines.append(f"       建议: {item.suggestion}")
                if len(items) > 5:
                    lines.append(f"    ... 还有 {len(items) - 5} 项")
                lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "checks_run": self.checks_run,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "info_count": self.info_count,
            "total_issues": len(self.issues),
            "by_category": {
                cat: [i.to_dict() for i in items]
                for cat, items in self.by_category().items()
            },
            "issues": [i.to_dict() for i in self.issues],
        }



def check_kb_source_consistency(store, docs_dir: str) -> list[HealthIssue]:
    issues: list[HealthIssue] = []

    docs_path = Path(docs_dir)
    source_files: set[str] = set()
    if docs_path.exists():
        for f in docs_path.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".md", ".txt", ".rst"):
                source_files.add(f.name)

    all_chunks = store.get_all_chunks()
    kb_sources: set[str] = set()
    for c in all_chunks:
        src = c.get("source", "")
        if src:
            kb_sources.add(Path(src).name)  

    missing = source_files - kb_sources
    orphan = kb_sources - source_files

    for src in sorted(missing):
        issues.append(HealthIssue(
            level="warn",
            category="kb_consistency",
            description=f"源文件 '{src}' 未在向量库中找到",
            suggestion="运行灌库脚本 scripts/index_documents.py 将新文档加入知识库",
            details={"source": src, "issue_type": "missing"},
        ))

    for src in sorted(orphan):
        issues.append(HealthIssue(
            level="info",
            category="kb_consistency",
            description=f"向量库中有孤立来源 '{src}'，源文件已不存在",
            suggestion="确认是否需要删除该来源的所有 chunk",
            details={"source": src, "issue_type": "orphan"},
        ))

    return issues


def check_chunk_distribution(store) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    all_chunks = store.get_all_chunks()
    if not all_chunks:
        return issues

    counts: dict[str, int] = {}
    for c in all_chunks:
        src = c.get("source", "unknown")
        counts[src] = counts.get(src, 0) + 1

    if len(counts) < 2:
        return issues  

    values = list(counts.values())
    mean = statistics.mean(values)
    if mean == 0:
        return issues

    for src, cnt in sorted(counts.items(), key=lambda x: x[1]):
        if cnt <= 5:
            continue  
        ratio = cnt / mean
        if ratio < 0.5:
            issues.append(HealthIssue(
                level="warn",
                category="kb_quality",
                description=f"来源 '{src}' 只有 {cnt} 个 chunk，远低于均值 {mean:.1f}（{ratio*100:.0f}%）",
                suggestion="检查该文档是否切分异常（格式问题、内容太少或被过滤掉了）",
                details={"source": src, "chunk_count": cnt, "mean_count": round(mean, 1)},
            ))
        elif ratio > 1.5:
            issues.append(HealthIssue(
                level="info",
                category="kb_quality",
                description=f"来源 '{src}' 有 {cnt} 个 chunk，高于均值 {mean:.1f}（{ratio*100:.0f}%）",
                suggestion="这通常是正常的（长文档 chunk 多），如异常可检查切分参数",
                details={"source": src, "chunk_count": cnt, "mean_count": round(mean, 1)},
            ))

    return issues


def check_duplicate_chunks(store) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    all_chunks = store.get_all_chunks()
    if not all_chunks:
        return issues

    by_hash: dict[str, list[dict]] = {}
    for c in all_chunks:
        content_hash = hashlib.md5(c["content"].encode("utf-8")).hexdigest()
        by_hash.setdefault(content_hash, []).append(c)

    for content_hash, chunks in by_hash.items():
        if len(chunks) > 1:
            source = chunks[0].get("source", "unknown")
            preview = chunks[0]["content"][:50] + ("..." if len(chunks[0]["content"]) > 50 else "")
            issues.append(HealthIssue(
                level="warn",
                category="kb_quality",
                description=f"发现 {len(chunks)} 个重复 chunk（来源: {source}）",
                suggestion="检查灌库脚本的幂等逻辑，确认 chunk_id 生成是否一致",
                details={
                    "content_hash": content_hash,
                    "duplicate_count": len(chunks),
                    "chunk_ids": [c["chunk_id"] for c in chunks],
                    "source": source,
                    "content_preview": preview,
                },
            ))

    return issues


def check_short_chunks(store, min_chars: int = 20) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    all_chunks = store.get_all_chunks()
    if not all_chunks:
        return issues

    short_chunks = [c for c in all_chunks if len(c["content"].strip()) < min_chars]
    if not short_chunks:
        return issues

    by_source: dict[str, list[dict]] = {}
    for c in short_chunks:
        src = c.get("source", "unknown")
        by_source.setdefault(src, []).append(c)

    total_short = len(short_chunks)
    total_all = len(all_chunks)
    ratio = total_short / total_all

    level = "warn" if ratio > 0.05 else "info"
    issues.append(HealthIssue(
        level=level,
        category="kb_quality",
        description=f"发现 {total_short}/{total_all} 个超短 chunk（<{min_chars} 字），占比 {ratio*100:.1f}%",
        suggestion="占比 >5% 时建议检查切分逻辑，可能存在空段落或异常分割",
        details={
            "total_short": total_short,
            "total_all": total_all,
            "ratio": round(ratio, 4),
            "by_source": {src: len(chunks) for src, chunks in by_source.items()},
        },
    ))
    return issues


def check_bm25_vector_sync(retriever) -> list[HealthIssue]:
    issues: list[HealthIssue] = []

    vector_count = retriever.store.collection.count()
    bm25_count = len(retriever.bm25.chunks) if retriever.bm25 else 0

    if vector_count == 0 and bm25_count == 0:
        return issues

    if vector_count != bm25_count:
        diff = abs(vector_count - bm25_count)
        level = "error" if diff > 10 else "warn"
        issues.append(HealthIssue(
            level=level,
            category="kb_index",
            description=f"BM25 索引 ({bm25_count}) 与向量索引 ({vector_count}) 文档数不一致，差 {diff} 条",
            suggestion="重新灌库确保 BM25 和向量索引同步",
            details={"bm25_count": bm25_count, "vector_count": vector_count, "diff": diff},
        ))
    return issues



def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def check_duplicate_memories(memory, threshold: float = 0.92) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    total = memory.collection.count()
    if total < 2:
        return issues

    try:
        results = memory.collection.get(
            include=["documents", "metadatas", "embeddings"],
        )
    except Exception:
        return issues

    ids = results.get("ids", [])
    docs = results.get("documents", [])
    metas = results.get("metadatas", [])
    embeddings = results.get("embeddings", [])

    if not embeddings or len(embeddings) != len(ids):
        return issues

    clusters: list[dict] = []  

    for i in range(len(ids)):
        emb = embeddings[i]
        best_cluster = -1
        best_sim = 0.0

        for ci, cluster in enumerate(clusters):
            sim = _cosine_similarity(emb, cluster["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_cluster = ci

        if best_sim >= threshold and best_cluster >= 0:
            cluster = clusters[best_cluster]
            cluster["indices"].append(i)
            n = len(cluster["indices"])
            cluster["centroid"] = [
                (cluster["centroid"][j] * (n - 1) + emb[j]) / n
                for j in range(len(emb))
            ]
        else:
            clusters.append({
                "indices": [i],
                "centroid": list(emb),
            })

    for ci, cluster in enumerate(clusters):
        indices = cluster["indices"]
        if len(indices) < 2:
            continue

        contents = [docs[i] for i in indices]
        preview = contents[0][:60] + ("..." if len(contents[0]) > 60 else "")
        sources = [metas[i].get("user_id", "unknown") if metas[i] else "unknown" for i in indices]

        issues.append(HealthIssue(
            level="warn",
            category="memory_quality",
            description=f"发现 {len(indices)} 条相似记忆（相似度 > {threshold}）",
            suggestion="合并重复记忆，或归档冗余条目",
            details={
                "cluster_id": ci,
                "size": len(indices),
                "similarity_threshold": threshold,
                "memory_ids": [ids[i] for i in indices],
                "content_preview": preview,
                "user_ids": list(set(sources)),
            },
        ))

    return issues


def check_stale_memories(
    memory,
    stale_days: int = 30,
    low_importance_threshold: float = 0.3,
) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    total = memory.collection.count()
    if total == 0:
        return issues

    try:
        results = memory.collection.get(
            include=["documents", "metadatas"],
            where={"status": "active"},
        )
    except Exception:
        return issues

    ids = results.get("ids", [])
    metas = results.get("metadatas", [])
    docs = results.get("documents", [])

    now = datetime.now(timezone.utc)
    stale_unaccessed: list[dict] = []
    low_quality: list[dict] = []

    for i in range(len(ids)):
        meta = metas[i] or {}
        access_count = int(meta.get("access_count", 0))
        importance = float(meta.get("importance", 0.5))
        created_str = meta.get("created_at", "")

        if access_count == 0 and created_str:
            try:
                created = datetime.fromisoformat(created_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                days_old = (now - created).days
                if days_old >= stale_days:
                    stale_unaccessed.append({
                        "id": ids[i],
                        "content_preview": docs[i][:50] if docs[i] else "",
                        "days_old": days_old,
                        "importance": importance,
                    })
            except (ValueError, TypeError):
                pass

        if importance < low_importance_threshold and access_count <= 1:
            low_quality.append({
                "id": ids[i],
                "content_preview": docs[i][:50] if docs[i] else "",
                "importance": importance,
                "access_count": access_count,
            })

    if stale_unaccessed:
        issues.append(HealthIssue(
            level="info",
            category="memory_quality",
            description=f"发现 {len(stale_unaccessed)} 条记忆超过 {stale_days} 天从未被访问",
            suggestion="考虑归档这些长期未使用的记忆，减少记忆库噪音",
            details={
                "count": len(stale_unaccessed),
                "stale_days": stale_days,
                "samples": stale_unaccessed[:5],
            },
        ))

    if low_quality:
        issues.append(HealthIssue(
            level="info",
            category="memory_quality",
            description=f"发现 {len(low_quality)} 条低重要性记忆（<{low_importance_threshold} 且访问≤1次）",
            suggestion="考虑清理或降级这些低价值记忆",
            details={
                "count": len(low_quality),
                "importance_threshold": low_importance_threshold,
                "samples": low_quality[:5],
            },
        ))

    return issues



def run_health_check(
    store=None,
    retriever=None,
    memory=None,
    docs_dir: str = "data/docs",
    checks: list[str] | None = None,
) -> HealthReport:
    report = HealthReport()
    all_checks = {
        "kb_consistency": lambda: check_kb_source_consistency(store, docs_dir) if store else [],
        "kb_quality": lambda: (
            check_chunk_distribution(store) +
            check_duplicate_chunks(store) +
            check_short_chunks(store)
        ) if store else [],
        "kb_index": lambda: check_bm25_vector_sync(retriever) if retriever else [],
        "memory_quality": lambda: (
            check_duplicate_memories(memory) +
            check_stale_memories(memory)
        ) if memory else [],
    }

    selected = checks or list(all_checks.keys())
    report.checks_run = selected

    for name in selected:
        if name not in all_checks:
            continue
        try:
            issues = all_checks[name]()
            report.issues.extend(issues)
        except Exception as e:
            report.issues.append(HealthIssue(
                level="error",
                category="check_failure",
                description=f"检查 '{name}' 执行失败: {e}",
                suggestion="检查依赖组件是否正常初始化",
                details={"error": str(e), "check_name": name},
            ))

    return report


store = VectorStore()
retriever = HybridRetriever(store)
memory = LongTermMemory()

report = run_health_check(
    store=store,
    retriever=retriever,
    memory=memory,
    docs_dir= "data/docs" ,
)
print (report.summary())