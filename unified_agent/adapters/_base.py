from __future__ import annotations

from typing import Protocol

from unified_agent.core.state import UnifiedState


class LaneSubgraph(Protocol):
    def invoke(self, state: UnifiedState) -> UnifiedState: ...