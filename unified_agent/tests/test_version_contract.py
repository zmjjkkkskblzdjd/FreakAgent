import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from unified_agent.core.contracts import PendingInterrupt


class S(TypedDict, total=False):
    out: Annotated[list, operator.add]   


def _a(state):
    return {"out": ["a"]}

def _b(state):
    return {"out": ["b"]}


def test_fanout_reducer_merges_both_lanes():
    g = StateGraph(S)
    g.add_node("a", _a)
    g.add_node("b", _b)
    g.add_edge(START, "a")
    g.add_edge(START, "b")               
    app = g.compile()
    result = app.invoke({})
    assert sorted(result["out"]) == ["a", "b"]


def test_conditional_edges_router_returns_list_fans_out():
    def _pick_node(state):
        return {}                      

    def _pick_router(state) -> list[str]:
        return ["fan_a", "fan_b"]      

    g = StateGraph(S)
    g.add_node("pick", _pick_node)
    g.add_node("a", _a)
    g.add_node("b", _b)
    g.add_edge(START, "pick")
    g.add_conditional_edges("pick", _pick_router, {"fan_a": "a", "fan_b": "b"})
    app = g.compile()
    result = app.invoke({})
    assert sorted(result["out"]) == ["a", "b"]


def test_parent_interrupt_resume_roundtrip():
    class S2(TypedDict, total=False):
        v: str

    def _ask(state):
        payload = PendingInterrupt(interrupt_id="i1", kind="route", lane=None,
                                   schema={"options": ["choose_rag", "give_up"]}).model_dump()
        answer = interrupt(payload)             
        return {"v": answer}

    g = StateGraph(S2)
    g.add_node("ask", _ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-interrupt"}}
    r1 = app.invoke({}, cfg)
    assert "__interrupt__" in r1
    value = r1["__interrupt__"][0].value   
    assert value == PendingInterrupt(interrupt_id="i1", kind="route", lane=None,
                                     schema={"options": ["choose_rag", "give_up"]}).model_dump()
    assert set(value) == {"interrupt_id", "kind", "lane", "schema"}
    r2 = app.invoke(Command(resume="choose_rag"), cfg)
    assert r2["v"] == "choose_rag"