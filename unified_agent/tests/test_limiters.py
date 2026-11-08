from unified_agent.core.limiters import TokenBucket, backoff_after_429, cost_ledger_entry


def test_token_bucket_depletes_and_refuses():
    b = TokenBucket(capacity=2, refill_per_sec=0)
    assert b.acquire() and b.acquire()
    assert not b.acquire()   


def test_backoff_grows():
    assert backoff_after_429(0) < backoff_after_429(4)


def test_cost_entry_has_lane_and_node():
    e = cost_ledger_entry("rag", "retrieve", 10, 20, ts=0)
    assert e["lane"] == "rag" and e["node"] == "retrieve"