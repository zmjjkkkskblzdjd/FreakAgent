import pytest

from memory_service.store import SCHEMA_VERSION, MemoryStore, _HashEmbedding


@pytest.fixture()
def mem(tmp_path):
    return MemoryStore(path=str(tmp_path), embedding=_HashEmbedding())


@pytest.fixture()
def legacy_promote(monkeypatch):
    import memory_service.store as st
    monkeypatch.setattr(st, "_PROMOTE_MIN_FACTS", 1)
    monkeypatch.setattr(st, "_PROMOTE_MIN_AGE_HOURS", 0.0)


def _add_doc(s: MemoryStore, text, meta):
    s._col.upsert(ids=[MemoryStore.doc_id(meta.get("user_id", ""), meta.get("tenant_id", ""), text)],
                  documents=[text], metadatas=[meta])



def test_add_requires_memory_type(mem):
    with pytest.raises(ValueError):
        mem.add("没有标签的记忆", {"user_id": "u1"})          


def test_add_recall_roundtrip(mem):
    mem.add("用户口径：成交额=含退款",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general"})
    hits = mem.recall("成交额的口径",
                      {"$and": [{"user_id": "u1"}, {"memory_type": "general"}]}, top_k=3)
    assert hits and "含退款" in hits[0]["text"]
    assert hits[0]["metadata"]["schema_version"] == SCHEMA_VERSION   
    assert hits[0]["metadata"]["access_count"] == 1                  


def test_add_empty_text_is_noop(mem):
    assert mem.add("  ", {"user_id": "u1"}) == ""


def test_add_is_idempotent(mem):
    for _ in range(3):
        mem.add("同一条记忆", {"user_id": "u1", "tenant_id": "t1", "memory_type": "general"})
    assert mem._col.count() == 1                                     


def test_recall_scoped_by_user_and_tenant(mem):
    mem.add("acme 口径记忆", {"user_id": "u1", "tenant_id": "acme", "memory_type": "sql"})
    mem.add("globex 口径记忆", {"user_id": "u9", "tenant_id": "globex", "memory_type": "sql"})
    hits = mem.recall("口径", {"$and": [{"user_id": "u1"}, {"tenant_id": "acme"}]})
    assert len(hits) == 1 and "acme" in hits[0]["text"]              


def test_version_gate_blocks_old_schema(mem):
    _add_doc(mem, "旧版本记忆", {"user_id": "u1", "tenant_id": "t1",
                                 "schema_version": 0})               
    hits = mem.recall("旧版本", {"$and": [{"user_id": "u1"},
                                          {"schema_version": SCHEMA_VERSION}]})
    assert hits == []                                                



def test_quota_cut_limits_per_type(mem):
    for i in range(3):
        mem.add(f"sql 纠错记忆{i}：口径", {"user_id": "u1", "memory_type": "sql"})
    mem.add("general 便签记忆：口径", {"user_id": "u1", "memory_type": "general"})
    hits = mem.recall("口径",
                      {"$and": [{"user_id": "u1"}, {"schema_version": SCHEMA_VERSION},
                                {"status": "active"}]}, top_k=3)
    types = [h["metadata"]["memory_type"] for h in hits]
    assert types.count("sql") <= 2 and "general" in types           


def test_evict_archives_coldest_then_revive(mem, tmp_path):
    s = MemoryStore(path=str(tmp_path), embedding=_HashEmbedding(), max_entries=2)
    s.add("热记忆：成交额口径", {"user_id": "u1", "memory_type": "general"})
    s.recall("成交额口径", {"$and": [{"user_id": "u1"}, {"status": "active"}]})  
    s.add("冷记忆：某旧事实", {"user_id": "u1", "memory_type": "general"})
    s.add("新记忆：最新事实", {"user_id": "u1", "memory_type": "general"})
    active = [m for m in s._col.get(include=["metadatas"])["metadatas"]
              if m["status"] == "active"]
    assert len(active) == 2                                         
    hit = s.recall("某旧事实", {"$and": [{"user_id": "u1"}, {"status": "active"}]})
    assert all("冷记忆" not in h["text"] for h in hit)              
    archived = [i for i, m in zip(s._col.get(include=["metadatas"])["ids"],
                                  s._col.get(include=["metadatas"])["metadatas"])
                if m["status"] == "archived"]
    s.revive(archived)                                              
    hit2 = s.recall("某旧事实", {"$and": [{"user_id": "u1"}, {"status": "active"}]})
    assert any("冷记忆" in h["text"] for h in hit2)                 


def test_lesson_pool_cap(tmp_path, monkeypatch):
    import memory_service.store as st
    monkeypatch.setitem(st._POOL_CAP, "lesson", 2)
    s = MemoryStore(path=str(tmp_path), embedding=_HashEmbedding(), max_entries=1000)
    for i in range(3):
        s.add(f"教训{i}", {"user_id": "u1", "memory_type": "lesson"})
    got = s._col.get(include=["metadatas", "documents"])
    active = [d for d, m in zip(got["documents"], got["metadatas"])
              if m["memory_type"] == "lesson" and m["status"] == "active"]
    assert len(active) == 2                                          
    assert "教训0" not in active and "教训2" in active               


def test_global_evict_skips_lessons(tmp_path):
    s = MemoryStore(path=str(tmp_path), embedding=_HashEmbedding(), max_entries=2)
    s.add("教训：执行前先核对列名", {"user_id": "u1", "memory_type": "lesson"})
    for i in range(3):
        s.add(f"闲聊记忆{i}", {"user_id": "u1", "memory_type": "general"})
    metas = s._col.get(include=["metadatas"])["metadatas"]
    lessons = [m for m in metas if m["memory_type"] == "lesson"]
    assert len(lessons) == 1 and lessons[0]["status"] == "active"     
    general = [m for m in metas if m["memory_type"] == "general" and m["status"] == "active"]
    assert len(general) == 2                                          


def test_revive_keeps_metadata_fields(mem):
    _id = mem.add("教训：执行前先核对列名",
                  {"user_id": "u1", "tenant_id": "acme", "memory_type": "lesson",
                   "fail_sig": "sig1"})
    assert mem.archive(_id) == {"ok": True}
    mem.revive([_id])
    m = mem._col.get(ids=[_id])["metadatas"][0]
    assert m["status"] == "active"
    assert m["user_id"] == "u1" and m["tenant_id"] == "acme"
    assert m["memory_type"] == "lesson" and m["fail_sig"] == "sig1"


def test_archive_by_id_keeps_other_fields(mem):
    _id = mem.add("待归档记忆：口径", {"user_id": "u1", "memory_type": "sql",
                                       "access_count": 5})
    assert mem.archive(_id) == {"ok": True}
    m = mem._col.get(ids=[_id])["metadatas"][0]
    assert m["status"] == "archived" and m["access_count"] == 5     
    assert mem.archive("不存在id")["ok"] is False



def test_add_keeps_stage_and_subject_metadata(mem):
    mem.add("偏好：默认货币单位=万元",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "candidate", "confidence": 0.8,
             "subject_dim": "默认货币单位", "subject_value": "万元"})
    m = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert m["stage"] == "candidate" and m["confidence"] == 0.8
    assert m["subject_dim"] == "默认货币单位" and m["subject_value"] == "万元"


def test_demote_candidates_penalizes_hot_layer():
    hits = [
        {"text": "候选偏好", "metadata": {"stage": "candidate"}, "distance": 0.10},
        {"text": "正式口径", "metadata": {"stage": "formal"}, "distance": 0.20},
        {"text": "无 stage 历史", "metadata": {}, "distance": 0.30},
    ]
    out = MemoryStore._demote_candidates(hits)
    assert [h["text"] for h in out] == ["正式口径", "无 stage 历史", "候选偏好"]
    assert out[-1]["base_distance"] == 0.10


def test_recall_returns_candidates_with_lower_weight(mem):
    mem.add("偏好：默认货币单位=万元",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "candidate", "confidence": 0.8,
             "subject_dim": "默认货币单位", "subject_value": "万元"})
    hits = mem.recall("默认货币单位",
                      {"$and": [{"user_id": "u1"}, {"tenant_id": "t1"}]}, top_k=2)
    assert hits and hits[0]["metadata"]["stage"] == "candidate"
    assert hits[0]["distance"] >= hits[0]["base_distance"] + 0.24


def test_promote_candidate_supersedes_conflicting_formal(mem, legacy_promote):
    base = {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
            "hint_type": "preference", "subject_dim": "默认货币单位"}
    mem.add("偏好：默认货币单位=人民币",
            {**base, "stage": "formal", "confidence": 0.9, "subject_value": "人民币"})
    mem.add("偏好：默认货币单位=万元",
            {**base, "stage": "candidate", "confidence": 0.85, "subject_value": "万元"})
    stats = mem.promote_candidates()
    assert stats["promoted"] == 1 and stats["superseded"] == 1
    rows = {r["text"]: r for r in mem.find({"tenant_id": "t1"})}
    new, old = rows["偏好：默认货币单位=万元"], rows["偏好：默认货币单位=人民币"]
    assert new["metadata"]["stage"] == "formal"
    assert new["metadata"]["status"] == "active"
    assert new["metadata"]["promoted_from"] == new["id"]
    assert new["metadata"]["promoted_at"] > 0
    assert old["metadata"]["status"] == "archived"
    assert old["metadata"]["status_reason"] == "superseded"
    assert old["metadata"]["superseded_by"] == new["id"]


def test_promote_below_threshold_stays_candidate(mem, legacy_promote):
    mem.add("偏好：回答语言=中文",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "candidate", "confidence": 0.5,
             "subject_dim": "回答语言", "subject_value": "中文"})
    stats = mem.promote_candidates()
    assert stats["promoted"] == 0 and stats["below_threshold"] == 1
    m = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert m["stage"] == "candidate" and m["status"] == "active"


def test_promote_skips_candidate_without_subject_dim(mem):
    mem.add("偏好：用表格展示",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "candidate", "confidence": 0.95})
    stats = mem.promote_candidates()
    assert stats["skipped_no_dim"] == 1 and stats["promoted"] == 0
    assert mem._col.get(include=["metadatas"])["metadatas"][0]["stage"] == "candidate"


def test_promote_skips_candidate_without_subject_value(mem):
    mem.add("偏好：默认货币单位",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "candidate", "confidence": 0.95,
             "subject_dim": "默认货币单位"})
    stats = mem.promote_candidates()
    assert stats["skipped_no_value"] == 1 and stats["promoted"] == 0


def test_promote_archives_duplicate_candidates(mem, legacy_promote):
    for text, conf in [("偏好：单位=万元", 0.9), ("偏好：单位=万元。", 0.7)]:
        mem.add(text, {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
                       "stage": "candidate", "confidence": conf,
                       "subject_dim": "单位", "subject_value": "万元"})
    stats = mem.promote_candidates()
    assert stats["promoted"] == 1 and stats["duplicate"] == 1
    active = [m for m in mem._col.get(include=["metadatas"])["metadatas"]
              if m["status"] == "active"]
    assert len(active) == 1 and active[0]["stage"] == "formal"



_OLD = 1_600_000_000.0    

_NEAR = ["偏好：默认货币单位用万元计价时不再换算",
         "偏好：默认货币单位按万元计价时不再换算",
         "偏好：默认货币单位以万元计价时不再换算"]


def _cand(s: MemoryStore, text, conf, dim="默认货币单位", value="万元",
          created_at=_OLD):
    s.add(text, {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
                 "stage": "candidate", "confidence": conf,
                 "subject_dim": dim, "subject_value": value,
                 "created_at": created_at})


def test_promote_requires_min_facts(mem):
    _cand(mem, _NEAR[0], 0.9)
    _cand(mem, _NEAR[1], 0.8)
    stats = mem.promote_candidates()
    assert stats["promoted"] == 0 and stats["below_count"] == 2
    assert all(m["stage"] == "candidate" for m
               in mem._col.get(include=["metadatas"])["metadatas"])


def test_promote_requires_min_age(mem):
    for text, conf in zip(_NEAR, (0.9, 0.8, 0.75)):
        _cand(mem, text, conf, created_at=None)      
    stats = mem.promote_candidates()
    assert stats["promoted"] == 0 and stats["too_young"] == 3


def test_promote_clusters_similar_summaries(mem):
    for text, conf, dim in zip(_NEAR, (0.9, 0.8, 0.75),
                               ("默认货币单位", "货币单位", "币种")):
        _cand(mem, text, conf, dim=dim)
    stats = mem.promote_candidates()
    assert stats["clustering"] == "embedding"
    assert stats["groups"] == 1 and stats["promoted"] == 1
    assert stats["duplicate"] == 2


def test_promote_writes_valid_until_and_rollback(mem):
    mem.add("偏好：默认货币单位=人民币",
            {"user_id": "u1", "tenant_id": "t1", "memory_type": "general",
             "stage": "formal", "confidence": 0.9, "subject_dim": "默认货币单位",
             "subject_value": "人民币", "created_at": _OLD})
    for text, conf in zip(_NEAR, (0.9, 0.8, 0.75)):
        _cand(mem, text, conf)
    stats = mem.promote_candidates()
    assert stats["promoted"] == 1 and stats["superseded"] == 1

    rows = {r["text"]: r for r in mem.find({"tenant_id": "t1"})}
    new = rows[_NEAR[0]]["metadata"]
    old = rows["偏好：默认货币单位=人民币"]["metadata"]
    assert new["stage"] == "formal" and new["valid_until"] == ""
    assert new["valid_from"] == _OLD
    assert old["status"] == "archived" and old["valid_until"] == new["valid_from"]
    batch = new["promotion_batch"]
    assert batch and old["promotion_batch"] == batch
    assert old["prev_status"] == "active" and old["prev_valid_until"] == ""

    done = mem.rollback_promotion(batch)
    assert done["ok"] is True and done["restored"] == 4
    rows = {r["text"]: r for r in mem.find({"tenant_id": "t1"})}
    assert rows[_NEAR[0]]["metadata"]["stage"] == "candidate"
    assert rows[_NEAR[0]]["metadata"]["status"] == "active"
    assert rows[_NEAR[0]]["metadata"]["valid_until"] == ""
    restored = rows["偏好：默认货币单位=人民币"]["metadata"]
    assert restored["status"] == "active" and restored["valid_until"] == ""
    assert restored["superseded_by"] == "" and restored["stage"] == "formal"
    assert rows[_NEAR[0]]["metadata"]["promotion_batch"] == ""



_FORMAL = {"user_id": "u1", "tenant_id": "t1", "memory_type": "sql",
           "stage": "formal", "subject_dim": "成交额口径"}


def test_add_formal_supersedes_same_dim_conflict(mem):
    old_id = mem.add("成交额口径=含退款",
                     {**_FORMAL, "subject_value": "含退款", "created_at": 100.0})
    new_id = mem.add("成交额口径=不含退款",
                     {**_FORMAL, "subject_value": "不含退款", "created_at": 200.0})
    rows = {r["id"]: r["metadata"] for r in mem.find({"tenant_id": "t1"})}
    assert rows[old_id]["status"] == "archived"
    assert rows[old_id]["status_reason"] == "superseded"
    assert rows[old_id]["superseded_by"] == new_id
    assert rows[old_id]["valid_until"] == 200.0
    assert rows[new_id]["status"] == "active"
    assert rows[new_id]["valid_from"] == 200.0 and rows[new_id]["valid_until"] == ""


def test_add_formal_same_value_is_duplicate(mem):
    old_id = mem.add("成交额口径=含退款（1）",
                     {**_FORMAL, "subject_value": "含退款", "created_at": 100.0})
    new_id = mem.add("成交额口径=含退款（2）",
                     {**_FORMAL, "subject_value": "含退款", "created_at": 200.0})
    rows = {r["id"]: r["metadata"] for r in mem.find({"tenant_id": "t1"})}
    assert rows[old_id]["status"] == "archived"
    assert rows[old_id]["status_reason"] == "duplicate"
    assert rows[new_id]["status"] == "active"


def test_add_candidate_does_not_archive_formal(mem):
    formal_id = mem.add("成交额口径=含退款", {**_FORMAL, "subject_value": "含退款"})
    mem.add("成交额口径=不含退款",
            {**_FORMAL, "stage": "candidate", "confidence": 0.9,
             "subject_value": "不含退款"})
    rows = {r["id"]: r["metadata"] for r in mem.find({"tenant_id": "t1"})}
    assert rows[formal_id]["status"] == "active"
    assert rows[formal_id]["valid_until"] == ""


def test_add_formal_without_subject_skips_archive(mem):
    a = mem.add("事实 A", {**_FORMAL, "memory_type": "rag", "subject_dim": "",
                           "subject_value": ""})
    b = mem.add("事实 B", {**_FORMAL, "memory_type": "rag", "subject_dim": "",
                           "subject_value": ""})
    rows = {r["id"]: r["metadata"] for r in mem.find({"tenant_id": "t1"})}
    assert rows[a]["status"] == "active" and rows[b]["status"] == "active"



def test_migrate_legacy_maps_metadata(mem, tmp_path):
    import chromadb

    from memory_service.migrate_legacy import migrate
    old = chromadb.PersistentClient(path=str(tmp_path)).get_or_create_collection("qa_memory")
    old.upsert(ids=["x1"], documents=["qa 旧记忆：口径=含退款"],
               metadatas=[{"user_id": "u1", "session_id": "s9", "type": "preference",
                           "access_count": 7, "created_at": 100.0}])
    n = migrate(mem, str(tmp_path), "qa_memory")
    assert n == 1 and n == migrate(mem, str(tmp_path), "qa_memory")   
    got = mem._col.get(include=["metadatas", "documents"])
    assert "qa 旧记忆" in got["documents"][0]
    m = got["metadatas"][0]
    assert m["memory_type"] == "general"        
    assert m["source"] == "session:s9" and m["access_count"] == 7
