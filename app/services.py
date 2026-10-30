"""Business logic: lifecycle, carry-forward, reassignment, attention, knowledge base."""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from . import ai_service, config
from .logging_setup import log_event
from .models import ActionItem, Decision, Document, ItemDocLink, ItemHistory, Meeting, Participant, now
from .schemas import Recap

log = logging.getLogger("meetinghub.service")

OPEN = ("open", "in_progress")
CLOSED = ("done", "dropped")
STATUSES = OPEN + CLOSED
ATTENDANCE = ("attended", "missed", "no_response", "on_leave")
KINDS = ("meeting", "handover")
MEETING_STATUSES = ("planned", "held", "closed")
PRIORITIES = ("urgent", "high", "normal", "low")
STOP = set("the and for with from that this will have into about over after before their they them were been "
           "being than then also not are our you your has had can should would could".split())


# ---------------------------------------------------------------- helpers
def parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError("Due date must be in YYYY-MM-DD format.")


def parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError("Scheduled time is not a valid date and time.")


def parse_names(raw: str) -> list[str]:
    seen, out = set(), []
    for name in re.split(r"[,\n]", raw or ""):
        name = name.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name[:80])
    return out


def _hist(s: Session, item: ActionItem, event: str, detail: str = "") -> None:
    s.add(ItemHistory(item_id=item.id, event=event, detail=detail))


def get_or_404(s: Session, model, pk: int):
    obj = s.get(model, pk)
    if not obj:
        raise LookupError(f"{model.__name__} {pk} not found")
    return obj


# ---------------------------------------------------------------- meetings
def create_meeting(s: Session, title: str, kind: str, agenda: str, scheduled_raw: str,
                   participants_raw: str, previous_id: Optional[int] = None) -> Meeting:
    title = (title or "").strip()
    if not title:
        raise ValueError("Title is required.")
    if len(title) > 150:
        raise ValueError("Title must be 150 characters or fewer.")
    if kind not in KINDS:
        raise ValueError("Type must be meeting or handover.")
    m = Meeting(title=title, kind=kind, agenda=agenda.strip(), scheduled_at=parse_dt(scheduled_raw),
                previous_meeting_id=previous_id)
    s.add(m)
    s.flush()
    for name in parse_names(participants_raw):
        s.add(Participant(meeting_id=m.id, name=name))
    s.commit()
    log_event(log, logging.INFO, "meeting.created", meeting_id=m.id, kind=kind)
    return m


def update_meeting(s: Session, meeting_id: int, agenda: str = None, notes: str = None, status: str = None) -> Meeting:
    m = get_or_404(s, Meeting, meeting_id)
    if status is not None:
        if status not in MEETING_STATUSES:
            raise ValueError("Invalid meeting status.")
        if status != m.status:
            log_event(log, logging.INFO, "meeting.status_changed", meeting_id=m.id, old=m.status, new=status)
        m.status = status
    if agenda is not None:
        m.agenda = agenda.strip()
    if notes is not None:
        if len(notes) > 50000:
            raise ValueError("Notes are too long (max 50,000 characters).")
        m.notes = notes
    s.add(m)
    s.commit()
    return m


def add_participants(s: Session, meeting_id: int, raw: str) -> int:
    get_or_404(s, Meeting, meeting_id)
    existing = {p.name.lower() for p in s.exec(select(Participant).where(Participant.meeting_id == meeting_id))}
    added = 0
    for name in parse_names(raw):
        if name.lower() not in existing:
            s.add(Participant(meeting_id=meeting_id, name=name))
            added += 1
    s.commit()
    return added


def set_attendance(s: Session, participant_id: int, attendance: str) -> None:
    if attendance not in ATTENDANCE:
        raise ValueError("Invalid attendance value.")
    p = get_or_404(s, Participant, participant_id)
    p.attendance = attendance
    s.add(p)
    s.commit()
    log_event(log, logging.INFO, "participant.attendance_set", meeting_id=p.meeting_id, participant=p.name, attendance=attendance)


def add_decision(s: Session, meeting_id: int, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise ValueError("Decision text is required.")
    get_or_404(s, Meeting, meeting_id)
    s.add(Decision(meeting_id=meeting_id, text=text[:500]))
    s.commit()


# ---------------------------------------------------------------- action items
def create_item(s: Session, meeting_id: int, title: str, owner: str = "", due_raw: str = "",
                priority: str = "normal", ai: bool = False, commit: bool = True) -> ActionItem:
    title = (title or "").strip()
    if len(title) < 3:
        raise ValueError("Action item needs a title of at least 3 characters.")
    get_or_404(s, Meeting, meeting_id)
    if priority not in PRIORITIES:
        raise ValueError("Invalid priority.")
    item = ActionItem(title=title[:300], owner=(owner or "").strip() or "Unassigned", priority=priority,
                      due_date=parse_date(due_raw),
                      origin_meeting_id=meeting_id, current_meeting_id=meeting_id, ai_generated=ai)
    s.add(item)
    s.flush()
    _hist(s, item, "created", f"Owner {item.owner}" + (", suggested by AI" if ai else ""))
    if commit:
        s.commit()
    log_event(log, logging.INFO, "item.created", item_id=item.id, meeting_id=meeting_id, owner=item.owner, ai_generated=ai)
    return item


def update_item(s: Session, item_id: int, title: str = None, owner: str = None, due_raw: str = None,
                status: str = None, outcome: str = None, priority: str = None) -> ActionItem:
    item = get_or_404(s, ActionItem, item_id)
    new_status = status if status else item.status
    if new_status not in STATUSES:
        raise ValueError("Invalid status.")
    new_outcome = item.outcome if outcome is None else outcome.strip()
    if new_status in CLOSED and not new_outcome:
        raise ValueError("Record the outcome before closing an item (what was the result?).")
    new_due = item.due_date if due_raw is None else parse_date(due_raw)
    new_priority = item.priority if priority is None else priority
    if new_priority not in PRIORITIES:
        raise ValueError("Invalid priority.")
    new_owner = item.owner if not (owner or "").strip() else owner.strip()
    new_title = item.title if not (title or "").strip() else title.strip()[:300]

    changed = False
    if new_owner != item.owner:
        _hist(s, item, "reassigned", f"{item.owner} -> {new_owner}")
        log_event(log, logging.INFO, "item.reassigned", item_id=item.id, old_owner=item.owner, new_owner=new_owner)
        item.owner, changed = new_owner, True
    if new_due != item.due_date:
        _hist(s, item, "due_changed", f"{item.due_date or 'none'} -> {new_due or 'none'}")
        item.due_date, changed = new_due, True
    if new_priority != item.priority:
        _hist(s, item, "priority_changed", f"{item.priority} -> {new_priority}")
        item.priority, changed = new_priority, True
    if new_status != item.status:
        _hist(s, item, "status_changed", f"{item.status} -> {new_status}" + (f". Outcome: {new_outcome}" if new_status in CLOSED else ""))
        event = "item.closed" if new_status in CLOSED else "item.status_changed"
        log_event(log, logging.INFO, event, item_id=item.id, old_status=item.status, new_status=new_status)
        item.status, changed = new_status, True
    if new_outcome != item.outcome:
        item.outcome, changed = new_outcome, True
    if new_title != item.title:
        _hist(s, item, "renamed", f"{item.title} -> {new_title}")
        item.title, changed = new_title, True
    if changed:
        item.updated_at = now()
        s.add(item)
        s.commit()
    return item


def bulk_reassign(s: Session, from_owner: str, to_owner: str) -> int:
    from_owner, to_owner = (from_owner or "").strip(), (to_owner or "").strip()
    if not from_owner or not to_owner:
        raise ValueError("Both 'from' and 'to' names are required.")
    if from_owner.lower() == to_owner.lower():
        raise ValueError("'From' and 'to' must be different people.")
    count = 0
    for item in s.exec(select(ActionItem).where(ActionItem.status.in_(OPEN))).all():
        if item.owner.lower() == from_owner.lower():
            _hist(s, item, "reassigned", f"{item.owner} -> {to_owner} (handover)")
            log_event(log, logging.INFO, "item.reassigned", item_id=item.id, old_owner=item.owner, new_owner=to_owner, bulk=True)
            item.owner, item.updated_at = to_owner, now()
            s.add(item)
            count += 1
    s.commit()
    return count


def apply_recap(s: Session, meeting: Meeting, recap: Recap, used_ai: bool) -> tuple[int, int]:
    meeting.summary = recap.summary.strip()
    if meeting.status == "planned":
        meeting.status = "held"
    s.add(meeting)
    existing_dec = {d.text.lower() for d in s.exec(select(Decision).where(Decision.meeting_id == meeting.id))}
    new_dec = 0
    for text in recap.decisions:
        if text.strip() and text.strip().lower() not in existing_dec:
            s.add(Decision(meeting_id=meeting.id, text=text.strip()[:500]))
            new_dec += 1
    existing_items = {(i.title.lower(), i.owner.lower()) for i in
                      s.exec(select(ActionItem).where(ActionItem.current_meeting_id == meeting.id))}
    new_items = 0
    for it in recap.action_items:
        if (it.title.lower(), it.owner.lower()) in existing_items:
            continue
        create_item(s, meeting.id, it.title, it.owner, it.due_date.isoformat() if it.due_date else "",
                    ai=used_ai, commit=False)
        new_items += 1
    s.commit()
    sync_meeting_kb(s, meeting)
    log_event(log, logging.INFO, "recap.applied", meeting_id=meeting.id, used_ai=used_ai,
              decisions=new_dec, action_items=new_items)
    return new_dec, new_items


def carry_forward(s: Session, meeting_id: int) -> Meeting:
    old = get_or_404(s, Meeting, meeting_id)
    open_items = s.exec(select(ActionItem).where(ActionItem.current_meeting_id == old.id,
                                                 ActionItem.status.in_(OPEN))).all()
    if not open_items:
        raise ValueError("There are no open items to carry forward.")
    new = Meeting(title=f"Follow-up: {old.title.removeprefix('Follow-up: ')}", kind=old.kind,
                  agenda="Review carried-over items:\n" + "\n".join(f"- {i.title} ({i.owner})" for i in open_items),
                  previous_meeting_id=old.id)
    s.add(new)
    s.flush()
    for p in s.exec(select(Participant).where(Participant.meeting_id == old.id)).all():
        s.add(Participant(meeting_id=new.id, name=p.name))
    for item in open_items:
        item.current_meeting_id, item.updated_at = new.id, now()
        _hist(s, item, "carried_forward", f"'{old.title}' -> '{new.title}'")
        s.add(item)
    old.status = "closed"
    s.add(old)
    s.commit()
    log_event(log, logging.INFO, "items.carried_forward", from_meeting=old.id, to_meeting=new.id, count=len(open_items))
    return new


# ---------------------------------------------------------------- follow-up assistant (special feature)
def attention_items(s: Session) -> list[dict]:
    today, cutoff = date.today(), now() - timedelta(days=config.STALL_DAYS)
    out = []
    for item in s.exec(select(ActionItem).where(ActionItem.status.in_(OPEN))).all():
        reasons = []
        overdue = bool(item.due_date and item.due_date < today)
        stalled = item.updated_at < cutoff
        if overdue:
            reasons.append(f"Overdue by {(today - item.due_date).days} day(s)")
        if stalled:
            reasons.append(f"No update for {(now() - item.updated_at).days} days")
        if reasons:
            meeting = s.get(Meeting, item.current_meeting_id)
            out.append({"item": item, "meeting": meeting, "reasons": reasons, "overdue": overdue})
    priority_order = {value: index for index, value in enumerate(PRIORITIES)}
    out.sort(key=lambda r: (not r["overdue"], priority_order.get(r["item"].priority, 2),
                            r["item"].due_date or date.max))
    return out


def nudge_context(s: Session, item_id: int) -> dict:
    item = get_or_404(s, ActionItem, item_id)
    meeting = s.get(Meeting, item.current_meeting_id)
    return {"title": item.title, "owner": item.owner, "due": item.due_date, "status": item.status,
            "overdue": bool(item.due_date and item.due_date < date.today()),
            "days_idle": (now() - item.updated_at).days, "meeting": meeting.title if meeting else ""}


# ---------------------------------------------------------------- catch-up brief
def catchup_context(s: Session, meeting: Meeting, person: str) -> dict:
    today = date.today()
    part = s.exec(select(Participant).where(Participant.meeting_id == meeting.id)).all()
    attendance = next((p.attendance for p in part if p.name.lower() == person.lower()), "unknown")
    decisions = [d.text for d in s.exec(select(Decision).where(Decision.meeting_id == meeting.id))]

    def row(i: ActionItem) -> dict:
        return {"title": i.title, "owner": i.owner, "due": i.due_date, "status": i.status,
                "overdue": bool(i.due_date and i.due_date < today)}

    open_items = s.exec(select(ActionItem).where(ActionItem.status.in_(OPEN))).all()
    mine = [row(i) for i in open_items if i.owner.lower() == person.lower()]
    others = [row(i) for i in open_items if i.current_meeting_id == meeting.id
              and i.owner.lower() != person.lower() and i.due_date and i.due_date < today]
    return {"meeting": meeting.title, "type": meeting.kind, "person": person, "person_attendance": attendance,
            "summary": meeting.summary or meeting.notes[:800], "decisions": decisions,
            "your_open_items": mine, "other_overdue_items": others, "today": today.isoformat()}


# ---------------------------------------------------------------- knowledge base
def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOP}


def sync_meeting_kb(s: Session, meeting: Meeting) -> None:
    """Every recapped meeting becomes a searchable knowledge-base entry."""
    decisions = [d.text for d in s.exec(select(Decision).where(Decision.meeting_id == meeting.id))]
    content = f"{meeting.summary}\nDecisions: {'; '.join(decisions)}\n{meeting.notes[:1500]}"
    doc = s.exec(select(Document).where(Document.source_meeting_id == meeting.id)).first()
    if not doc:
        doc = Document(title=f"Meeting: {meeting.title}", doc_type="meeting", source_meeting_id=meeting.id)
    doc.content = content
    s.add(doc)
    s.commit()


def add_document(s: Session, title: str, content: str) -> Document:
    title, content = (title or "").strip(), (content or "").strip()
    if not title or not content:
        raise ValueError("Title and content are both required.")
    doc = Document(title=title[:150], content=content[:20000])
    s.add(doc)
    s.commit()
    log_event(log, logging.INFO, "document.created", doc_id=doc.id)
    return doc


def link_meeting_items(s: Session, meeting: Meeting) -> tuple[int, bool]:
    items = s.exec(select(ActionItem).where(ActionItem.current_meeting_id == meeting.id)).all()
    docs = [d for d in s.exec(select(Document)).all() if d.source_meeting_id != meeting.id]
    if not items or not docs:
        raise ValueError("Needs at least one action item and one knowledge-base document.")
    # 1) cheap keyword shortlist per item, 2) the LLM decides which are genuinely relevant
    shortlist: dict[int, list[tuple[int, Document, set[str]]]] = {}
    for it in items:
        it_tokens = _tokens(it.title)
        scored = []
        for d in docs:
            hits = it_tokens & _tokens(d.title + " " + d.content)
            score = len(hits) + len(it_tokens & _tokens(d.title))
            if score:
                scored.append((score, d, hits))
        shortlist[it.id] = sorted(scored, key=lambda t: -t[0])[:3]
    cand_docs = {d.id: d for sl in shortlist.values() for _, d, _ in sl}
    if not cand_docs:
        return 0, False
    picks, used_ai = ai_service.pick_links(
        [{"id": i.id, "title": i.title} for i in items if shortlist[i.id]],
        [{"id": d.id, "title": d.title, "snippet": d.content[:300]} for d in cand_docs.values()])
    if picks is None:
        pairs = [(i.id, sl[0][1].id, "Keyword match: " + ", ".join(sorted(sl[0][2])[:4]))
                 for i in items if (sl := shortlist[i.id])]
    else:
        valid_items = {i.id for i in items}
        pairs = [(p.item_id, p.doc_id, p.reason) for p in picks.links if p.item_id in valid_items and p.doc_id in cand_docs]
    existing = {(l.item_id, l.doc_id) for l in s.exec(select(ItemDocLink)).all()}
    created = 0
    for item_id, doc_id, reason in pairs:
        if (item_id, doc_id) not in existing:
            s.add(ItemDocLink(item_id=item_id, doc_id=doc_id, reason=reason[:300]))
            existing.add((item_id, doc_id))
            created += 1
    s.commit()
    log_event(log, logging.INFO, "kb.linked", meeting_id=meeting.id, links=created, used_ai=used_ai)
    return created, used_ai
