from __future__ import annotations

import argparse
import json

from memory_service.store import promote_candidates, rollback_promotion


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把长期记忆候选晋升为正式记忆")
    ap.add_argument("--min-conf", type=float, default=None,
                    help="晋升阈值（默认读 MEMORY_SERVICE_PROMOTE_MIN_CONF，再回 0.7）")
    ap.add_argument("--rollback", metavar="BATCH_ID", default=None,
                    help="按晋升批次回滚（恢复 stage/status/valid_until 等原值）")
    args = ap.parse_args(argv)
    if args.rollback:
        stats = rollback_promotion(args.rollback)
        print(json.dumps(stats, ensure_ascii=False))
        return 0 if stats.get("ok") else 1
    stats = promote_candidates(min_conf=args.min_conf)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
