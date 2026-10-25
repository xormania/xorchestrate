"""Built-in role behavior adapters; configured role identities live in the catalog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleBehavior:
    """Installed behavior contract, independent of configured role identities."""

    id: str
    editor: bool = False
    interface: bool = False


def co_behavior():
    return RoleBehavior("co", interface=True)


def xo_behavior():
    return RoleBehavior("xo")


def nco_behavior():
    return RoleBehavior("nco")


def planner_behavior():
    return RoleBehavior("planner")


def coder_behavior():
    return RoleBehavior("coder", editor=True)


def audit_behavior():
    return RoleBehavior("audit")


def docs_behavior():
    return RoleBehavior("docs", editor=True)


def research_behavior():
    return RoleBehavior("research")


def arbitrator_behavior():
    return RoleBehavior("arbitrator")


def test_writer_behavior():
    return RoleBehavior("test_writer", editor=True)


def architect_behavior():
    return RoleBehavior("architect", editor=True)
