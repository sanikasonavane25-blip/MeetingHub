"""Realistic demo data so the app tells a story on first launch."""
import logging
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from .db import engine
from .logging_setup import log_event
from .models import ActionItem, Decision, Document, ItemHistory, Meeting, Participant, now
from .services import sync_meeting_kb

log = logging.getLogger("meetinghub.seed")

SPRINT_NOTES = """SS26 collection planning. The team reviewed the first fitting and the launch story for the new DigiPlus collection.
Decision: lock the hero look and campaign narrative before the next studio shoot.
Decision: move the limited-edition drop to a two-stage release so the community preview lands first.
Action: Rahul to resolve the final sample fitting notes.
Action: Meera to draft the collection story and product copy.
Action: Asha to share the production cost comparison with finance.
Action: Dev to update the launch checklist and press-kit timeline.
Meera could not attend, Dev never replied to the invite."""

HANDOVER_NOTES = """Meera goes on leave from next Monday for three weeks.
She owns the SS26 collection story, product copy and studio shoot notes. Dev will cover her work.
Lookbook selects are in the campaign folder; the collection brief has the latest tone-of-voice guidance."""


def seed_demo_data() -> None:
    with Session(engine) as s:
        if s.exec(select(Meeting)).first():
            return
        today = date.today()
        m1 = Meeting(title="SS26 collection planning", kind="meeting", status="held", notes=SPRINT_NOTES,
                     scheduled_at=datetime.combine(today - timedelta(days=8), datetime.min.time()).replace(hour=10),
                     agenda="1. Collection story\n2. Hero look and release rhythm\n3. Production risks",
                     summary="The team reviewed the SS26 collection story, locked the hero look direction and agreed a staged limited-edition release.")
        m2 = Meeting(title="Handover: Meera to Dev", kind="handover", status="planned", notes=HANDOVER_NOTES,
                     scheduled_at=datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=15),
                     agenda="Walk through Meera's open work and where the materials live.")
        s.add(m1)
        s.add(m2)
        s.flush()
        for name, att in (("Asha", "attended"), ("Rahul", "attended"), ("Meera", "missed"), ("Dev", "no_response")):
            s.add(Participant(meeting_id=m1.id, name=name, attendance=att))
        for name, att in (("Meera", "on_leave"), ("Dev", "attended"), ("Asha", "attended")):
            s.add(Participant(meeting_id=m2.id, name=name, attendance=att))
        for text in ("Ship the v2 onboarding flow behind a feature flag first.", "Postpone SSO integration to Sprint 44."):
            s.add(Decision(meeting_id=m1.id, text=text))

        def item(title, owner, due, status, outcome="", idle_days=0):
            it = ActionItem(title=title, owner=owner, due_date=due, status=status, outcome=outcome,
                            origin_meeting_id=m1.id, current_meeting_id=m1.id, ai_generated=True,
                            created_at=now() - timedelta(days=8), updated_at=now() - timedelta(days=idle_days))
            s.add(it)
            s.flush()
            s.add(ItemHistory(item_id=it.id, at=it.created_at, event="created", detail=f"Owner {owner}, suggested by AI"))
            if status == "done":
                s.add(ItemHistory(item_id=it.id, event="status_changed", detail=f"open -> done. Outcome: {outcome}"))

        item("Resolve the final sample fitting notes", "Rahul", today - timedelta(days=3), "in_progress", idle_days=6)
        item("Draft the SS26 collection story", "Meera", today + timedelta(days=2), "open", idle_days=7)
        item("Share the production cost comparison with finance", "Asha", today - timedelta(days=1), "open", idle_days=2)
        item("Update the launch and press-kit checklist", "Dev", today - timedelta(days=4), "done",
             outcome="Checklist updated and approved by the studio lead.", idle_days=3)

        for title, content in (
            ("SS26 collection brief", "The creative direction, material story and tone of voice for the SS26 limited-edition collection."),
            ("Lookbook and campaign guide", "Hero look selects, studio shoot sequence, campaign narrative and the handoff from lookbook to launch."),
            ("Launch and press-kit checklist", "Steps before launch: final samples, image selects, product copy, press notes, release rhythm and studio sign-off."),
            ("Production cost framework", "Criteria for comparing production costs: materials, sampling, quantities, lead times and margin."),
            ("Leave and handover policy", "When a teammate goes on leave, write a handover note, reassign open work and share where materials live."),
        ):
            s.add(Document(title=title, content=content))
        s.commit()
        sync_meeting_kb(s, m1)
        log_event(log, logging.INFO, "seed.completed", meetings=2, documents=6)
