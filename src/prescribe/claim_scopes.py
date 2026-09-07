"""Shared overlap rules for durable claims and temporary reservations."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ClaimScope(Protocol):
    @property
    def target_type(self) -> str: ...

    @property
    def subject(self) -> str: ...

    @property
    def address(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ClaimAddress:
    target_type: str
    subject: str
    address: str


def claims_overlap(left: ClaimScope, right: ClaimScope) -> bool:
    if left.target_type == "env" or right.target_type == "env":
        normalize = str.casefold if os.name == "nt" else str
        return left.target_type == right.target_type and normalize(left.subject) == normalize(right.subject)
    left_path = Path(os.path.normcase(os.path.abspath(left.subject)))
    right_path = Path(os.path.normcase(os.path.abspath(right.subject)))
    if left.target_type == "asset" and left.address == "tree" and right_path.is_relative_to(left_path):
        return True
    if right.target_type == "asset" and right.address == "tree" and left_path.is_relative_to(right_path):
        return True
    if left_path != right_path:
        return False
    if "asset" in {left.target_type, right.target_type}:
        return True
    if left.target_type == right.target_type == "file":
        return (
            left.address == right.address
            or left.address.startswith(right.address + ".")
            or right.address.startswith(left.address + ".")
        )
    if {left.target_type, right.target_type} <= {"line", "shell"}:
        return left.address == right.address
    # Different document formats cannot safely manage the same file.
    return True
