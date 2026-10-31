"""LangGraph executes journal-backed transitions; receipts remain business facts."""

import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


class GraphState(TypedDict, total=False):
    run_id: str
    stage: str


def build_graph(controller, checkpointer):
    builder = StateGraph(GraphState)

    def dispatch(state):
        return {"stage": controller.store.load(state["run_id"]).stage}

    def transition(state):
        controller._advance(state["run_id"])
        return {"stage": controller.store.load(state["run_id"]).stage}

    builder.add_node("dispatch", dispatch)
    builder.add_edge(START, "dispatch")
    stages = ["decide", "submit", "wait", "analyze", "reconcile", "finished"]
    for stage in stages:
        builder.add_node(stage, transition)
        builder.add_edge(stage, END)
    builder.add_conditional_edges("dispatch", lambda state: state["stage"], {s: s for s in stages})
    return builder.compile(checkpointer=checkpointer)


def drive(controller, run_id: str, max_steps: int | None = None, poll_interval: float = 0.05, on_event=None):
    """A fresh graph invocation dispatches from durable business state each tick.

    This deliberately reconciles external effects instead of blindly replaying a
    failed submission node. The same thread retains graph checkpoints for audit.
    """
    if max_steps is not None and max_steps < 1:
        raise ValueError("max_steps must be positive")
    if poll_interval < 0:
        raise ValueError("poll_interval must be nonnegative")
    with controller.store.run_lock(run_id):
        with SqliteSaver.from_conn_string(str(controller.store.root / "checkpoints.sqlite")) as saver:
            graph = build_graph(controller, saver)
            config = {"configurable": {"thread_id": run_id}}
            cursor = 0
            steps = 0
            while controller.store.load(run_id).stage != "finished":
                graph.invoke({"run_id": run_id}, config=config, durability="sync")
                steps += 1
                events = controller.store.events(run_id, cursor)
                if events:
                    cursor = events[-1]["seq"]
                if on_event:
                    for event in events:
                        on_event(event)
                state = controller.store.load(run_id)
                if state.stage == "reconcile" and events and events[-1]["event"] == "reconciliation_required":
                    break
                if max_steps is not None and steps >= max_steps:
                    break
                if state.stage == "wait":
                    time.sleep(poll_interval)
        return controller.report(run_id)
