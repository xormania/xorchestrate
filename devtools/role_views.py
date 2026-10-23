"""Live projections of the configured role registry; no role list is duplicated."""

from collections.abc import Mapping, Set

from .role_packages import catalog


class RoleSet(Set):
    def __init__(self, *field):
        self.field = field

    def __iter__(self):
        for name, package in catalog()["roles"].items():
            value = package
            for key in self.field:
                value = value[key]
            if value:
                yield name

    def __contains__(self, value):
        return value in set(iter(self))

    def __len__(self):
        return sum(1 for _ in self)


class RolePaths(Mapping):
    def __getitem__(self, name):
        package = catalog()["roles"][name]
        if not package["workspace"]["editor"]:
            raise KeyError(name)
        return package["workspace"]["paths"]

    def __iter__(self):
        return iter(RoleSet("workspace", "editor"))

    def __len__(self):
        return len(RoleSet("workspace", "editor"))
