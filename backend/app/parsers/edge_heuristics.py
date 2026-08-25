from __future__ import annotations

PUBLISH_NAMES = frozenset(
    {
        "publish",
        "emit",
        "produce",
        "dispatch",
        "send_event",
        "fire",
        "fireevent",
        "sendevent",
        "produce_event",
    }
)
CONSUME_NAMES = frozenset(
    {
        "subscribe",
        "consume",
        "listen",
        "on",
        "addeventlistener",
        "add_listener",
        "addlistener",
        "handle",
        "receive",
        "onmessage",
        "kafkalistener",
        "eventlistener",
        "rabbitlistener",
    }
)
INJECT_ANNOTATIONS = frozenset(
    {
        "autowired",
        "inject",
        "resource",
        "injected",
        "injectable",
    }
)


def call_leaf(name: str) -> str:
    leaf = name.split("(")[0].strip()
    if "." in leaf:
        leaf = leaf.split(".")[-1]
    return leaf.strip()


def event_edge_type(call_name: str) -> str | None:
    leaf = call_leaf(call_name).lower()
    if leaf in PUBLISH_NAMES:
        return "PUBLISHES"
    if leaf in CONSUME_NAMES:
        return "CONSUMES"
    return None


def is_inject_annotation(name: str) -> bool:
    return call_leaf(name).lower().lstrip("@") in INJECT_ANNOTATIONS
