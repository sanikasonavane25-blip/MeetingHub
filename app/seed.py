"""Realistic demo data so the app tells a story on first launch."""
import logging
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from .db import engine
from .logging_setup import log_event
from .models import ActionItem, Decision, Document, ItemHistory, Meeting, Participant, now
from .services import sync_meeting_kb

log = logging.getLogger("meetinghub.seed")

SPRINT_NOTES = """Sprint 42 planning. Asha opened by reviewing last sprint: velocity was down because of the flaky payment webhook tests.
Rahul said the webhook retries are the root cause and the tests need a rewrite.
Decision: ship the v2 onboarding flow behind a feature flag first.
Decision: postpone SSO integration to Sprint 44.
Action: Rahul to fix the flaky payment webhook tests.
Action: Meera to draft the onboarding v2 copy.
Action: Asha to share the vendor pricing comparison with finance.
Action: Dev to update the release checklist.
Meera could not attend, Dev never replied to the invite."""

HANDOVER_NOTES = """Meera goes on leave from next Monday for three weeks.
She owns the onboarding v2 copy and the user-interview synthesis. Dev will cover her work.
Interview recordings are in the research folder; the onboarding PRD has the latest tone-of-voice guidance."""


def seed_demo_data() -> None:
    with Session(engine) as s:
        if s.exec(select(Meeting)).first():
            return
        today = date.today()
        m1 = Meeting(title="Sprint 42 planning", kind="meeting", status="held", notes=SPRINT_NOTES,
                     scheduled_at=datetime.combine(today - timedelta(days=8), datetime.min.time()).replace(hour=10),
                     agenda="1. Last sprint review\n2. Sprint 42 scope\n3. Risks",
                     summary="The team reviewed Sprint 41, found that flaky payment webhook tests slowed delivery, "
                             "and agreed the Sprint 42 scope. Onboarding v2 ships behind a flag; SSO moves to Sprint 44.")
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

        item("Fix the flaky payment webhook tests", "Rahul", today - timedelta(days=3), "in_progress", idle_days=6)
        item("Draft the onboarding v2 copy", "Meera", today + timedelta(days=2), "open", idle_days=7)
        item("Share the vendor pricing comparison with finance", "Asha", today - timedelta(days=1), "open", idle_days=2)
        item("Update the release checklist", "Dev", today - timedelta(days=4), "done",
             outcome="Checklist updated and approved by QA lead.", idle_days=3)

        for title, content in (
            ("Payments webhook runbook", "How payment webhook retries work, how to replay failed webhook events, and how to debug flaky webhook tests in CI."),
            ("Onboarding v2 PRD", "Product requirements for the onboarding v2 flow, the feature flag rollout plan and tone-of-voice guidance for onboarding copy."),
            ("Release checklist", "Steps before every release: regression suite, feature flag review, rollback plan, QA sign-off and release notes."),
            ("Vendor evaluation criteria", "Criteria finance uses to compare vendor pricing: total cost of ownership, contract length, support SLAs and security review."),
            ("Leave and handover policy", "When a teammate goes on leave, write a handover note, reassign open work and share where materials live."),
        ):
            s.add(Document(title=title, content=content))
        s.commit()
        sync_meeting_kb(s, m1)
        log_event(log, logging.INFO, "seed.completed", meetings=2, documents=6)
