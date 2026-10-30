"""Database tables."""
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Meeting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    kind: str = "meeting"            # meeting | handover
    status: str = "planned"          # planned | held | closed
    scheduled_at: Optional[NaiveDatetime] = None
    agenda: str = ""
    notes: str = ""                  # raw, unstructured notes / transcript / chat
    summary: str = ""
    previous_meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id")
    created_at: NaiveDatetime = Field(default_factory=now)


class Participant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    name: str
    attendance: str = "no_response"  # attended | missed | no_response | on_leave


class Decision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    text: str


class ActionItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    owner: str = "Unassigned"
    due_date: Optional[date] = None
    status: str = "open"             # open | in_progress | done | dropped
    outcome: str = ""                # required when done / dropped
    origin_meeting_id: int = Field(foreign_key="meeting.id", index=True)
    current_meeting_id: int = Field(foreign_key="meeting.id", index=True)
    ai_generated: bool = False
    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class ItemHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="actionitem.id", index=True)
    at: NaiveDatetime = Field(default_factory=now)
    event: str
    detail: str = ""


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    doc_type: str = "doc"            # doc | meeting
    content: str = ""
    source_meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id")
    created_at: NaiveDatetime = Field(default_factory=now)


class ItemDocLink(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="actionitem.id", index=True)
    doc_id: int = Field(foreign_key="document.id", index=True)
    reason: str = ""
