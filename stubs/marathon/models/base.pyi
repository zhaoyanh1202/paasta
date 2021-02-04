# Stubs for marathon.models.base (Python 3.7)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any

class MarathonObject:
    def __eq__(self, other): ...
    def __hash__(self): ...
    def json_repr(self, minimal: bool = ...): ...
    @classmethod
    def from_json(cls, attributes): ...
    def to_json(self, minimal: bool = ...): ...

class MarathonResource(MarathonObject):
    def __eq__(self, other): ...
    def __hash__(self): ...

ID_PATTERN = ...  # type: Any

def assert_valid_path(path): ...
def assert_valid_id(id): ...
