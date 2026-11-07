"""Selectable behavioral stories and explicit controller execution limits.

Execution, terminals, receipts and cleanup stay with the existing test-kit.
These limits count submitted sessions/turns and public controller tool calls;
they do not measure tokens or a native client's internal tool activity.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Story:
    id: str
    version: int
    modes: tuple[str, ...]
    description: str


_PROTOCOL = ("fixture", "protocol-fixture")
STORIES = (
    Story("coordination", 1, _PROTOCOL, "Route, acknowledge and retain a message across restart."),
    Story("effects-recovery", 1, _PROTOCOL, "Fence denied, repeated and uncertain external effects across restart."),
    Story("session-lifecycle", 1, _PROTOCOL, "Start, communicate with and stop a bridge session."),
    Story("work-acceptance", 1, _PROTOCOL, "Measure an artifact, accept it and invalidate acceptance after revision."),
    Story("native-callback", 1, (*_PROTOCOL, "native"), "Observe an actor-bound harness callback and retained result."),
)


def select_stories(stories=(), mode="protocol-fixture"):
    if mode not in (*_PROTOCOL, "native"):
        raise ValueError("Unknown story mode")
    if not isinstance(stories, (tuple, list)) or any(not isinstance(s, str) or not s for s in stories):
        raise ValueError("Stories must contain nonempty IDs")
    if len(stories) != len(set(stories)):
        raise ValueError("Duplicate story selection")
    catalog = {story.id: story for story in STORIES}
    unknown = set(stories) - catalog.keys()
    if unknown:
        raise ValueError("Unknown stories: " + ", ".join(sorted(unknown)))
    selected = tuple(catalog[s] for s in stories) if stories else tuple(s for s in STORIES if mode in s.modes)
    if any(mode not in s.modes for s in selected):
        raise ValueError("Story mode unsupported: " + ", ".join(s.id for s in selected if mode not in s.modes))
    return selected


def validate_limits(*, max_sessions=2, max_turns=4, max_tool_calls=5000):
    limits = dict(max_sessions=max_sessions, max_turns=max_turns, max_tool_calls=max_tool_calls)
    for name, value in limits.items():
        if type(value) is not int or value <= 0:
            raise ValueError(name + " must be a positive integer")
    return limits
