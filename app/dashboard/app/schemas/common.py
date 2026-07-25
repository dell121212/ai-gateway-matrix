from __future__ import annotations

from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: Optional[Any] = None


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int = 1
    page_size: int = 50


class MicroCredits(BaseModel):
    microcredits: int
    credits: float = Field(description="microcredits / 1e6")

    @classmethod
    def from_micro(cls, m: int) -> "MicroCredits":
        return cls(microcredits=int(m), credits=int(m) / 1_000_000.0)
