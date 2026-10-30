"""Pydantic schemas that validate everything the LLM returns."""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RecapItem(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    owner: str = "Unassigned"
    due_date: Optional[date] = None

    @field_validator("due_date", mode="before")
    @classmethod
    def _lenient_date(cls, v):
        if v in (None, "", "null", "none", "None"):
            return None
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None  # a bad date should not fail the whole recap

    @field_validator("owner", mode="before")
    @classmethod
    def _owner(cls, v):
        return (str(v).strip() if v else "") or "Unassigned"


class Recap(BaseModel):
    summary: str = Field(min_length=1)
    decisions: list[str] = []
    action_items: list[RecapItem] = []


class Brief(BaseModel):
    what_happened: str
    your_items: list[str] = []
    risks: list[str] = []
    next_steps: list[str] = []


class LinkPick(BaseModel):
    item_id: int
    doc_id: int
    reason: str = ""


class LinkPicks(BaseModel):
    links: list[LinkPick] = []
