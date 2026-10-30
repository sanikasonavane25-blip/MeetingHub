"""HTTP routes. Thin: validate input, call services, render or redirect."""
import logging
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import ai_service, config, services
from .db import get_session
from .models import ActionItem, Decision, Document, ItemDocLink, ItemHistory, Meeting, Participant

log = logging.getLogger("meetinghub.web")
router = APIRouter()
templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
templates.env.globals.update(STATUSES=services.STATUSES, ATTENDANCE=services.ATTENDANCE, OPEN=services.OPEN, today=date.today)

PROGRESS = {"open": 15, "in_progress": 60, "done": 100, "dropped": 100}


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str, msg: str = None, err: str = None):
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    return RedirectResponse(url, status_code=303)


def safe_next(url: str, default: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else default


def task_visual(s: Session, item: ActionItem) -> dict:
    meeting = s.get(Meeting, item.current_meeting_id)
    attendees = s.exec(select(Participant).where(Participant.meeting_id == item.current_meeting_id)).all()
    people = [item.owner] if item.owner and item.owner != "Unassigned" else []
    for participant in attendees:
        if participant.attendance == "attended" and participant.name not in people:
            people.append(participant.name)
    return {
        "item": item,
        "meeting": meeting,
        "progress": PROGRESS.get(item.status, 0),
        "people": people[:4],
    }


# ---------------------------------------------------------------- dashboard
@router.get("/")
def dashboard(request: Request, s: Session = Depends(get_session)):
    meetings = s.exec(select(Meeting).order_by(Meeting.created_at.desc())).all()
    open_items = s.exec(select(ActionItem).where(ActionItem.status.in_(services.OPEN))).all()
    all_items = s.exec(select(ActionItem).order_by(ActionItem.updated_at.desc()).limit(8)).all()
    open_count = {}
    for i in open_items:
        open_count[i.current_meeting_id] = open_count.get(i.current_meeting_id, 0) + 1
    attention = services.attention_items(s)
    return render(request, "dashboard.html", meetings=meetings, open_count=open_count, open_items=open_items,
                  workboard=[task_visual(s, item) for item in all_items],
                  attention=attention, ai_on=ai_service.ai_enabled())


# ---------------------------------------------------------------- meetings
@router.get("/meetings/new")
def new_meeting_form(request: Request):
    return render(request, "new_meeting.html")


@router.post("/meetings")
def create_meeting(title: str = Form(""), kind: str = Form("meeting"), agenda: str = Form(""),
                   scheduled_at: str = Form(""), participants: str = Form(""), s: Session = Depends(get_session)):
    try:
        m = services.create_meeting(s, title, kind, agenda, scheduled_at, participants)
    except ValueError as e:
        return redirect("/meetings/new", err=str(e))
    return redirect(f"/meetings/{m.id}", msg="Meeting created.")


@router.get("/meetings/{mid}")
def meeting_detail(mid: int, request: Request, s: Session = Depends(get_session)):
    m = services.get_or_404(s, Meeting, mid)
    items = s.exec(select(ActionItem).where(ActionItem.current_meeting_id == mid).order_by(ActionItem.id)).all()
    rows = []
    for it in items:
        rows.append({
            "item": it,
            "progress": PROGRESS.get(it.status, 0),
            "people": task_visual(s, it)["people"],
            "overdue": bool(it.due_date and it.due_date < date.today() and it.status in services.OPEN),
            "history": s.exec(select(ItemHistory).where(ItemHistory.item_id == it.id).order_by(ItemHistory.id)).all(),
            "links": s.exec(select(ItemDocLink, Document).where(ItemDocLink.item_id == it.id,
                                                                 ItemDocLink.doc_id == Document.id)).all(),
            "origin": s.get(Meeting, it.origin_meeting_id),
        })
    return render(request, "meeting.html", m=m, rows=rows, prev=s.get(Meeting, m.previous_meeting_id) if m.previous_meeting_id else None,
                  participants=s.exec(select(Participant).where(Participant.meeting_id == mid)).all(),
                  decisions=s.exec(select(Decision).where(Decision.meeting_id == mid)).all())


@router.post("/meetings/{mid}/notes")
def save_notes(mid: int, agenda: str = Form(""), notes: str = Form(""), status: str = Form("planned"),
               s: Session = Depends(get_session)):
    try:
        services.update_meeting(s, mid, agenda=agenda, notes=notes, status=status)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    return redirect(f"/meetings/{mid}", msg="Saved.")


@router.post("/meetings/{mid}/recap")
def generate_recap(mid: int, agenda: str = Form(""), notes: str = Form(""), s: Session = Depends(get_session)):
    m = services.get_or_404(s, Meeting, mid)
    if len(notes.strip()) < 20:
        return redirect(f"/meetings/{mid}", err="Paste at least a few lines of notes, chat or transcript first.")
    try:
        services.update_meeting(s, mid, agenda=agenda, notes=notes)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    recap, used_ai = ai_service.extract_recap(notes, date.today())
    decisions, items = services.apply_recap(s, m, recap, used_ai)
    how = "AI recap" if used_ai else "AI was unavailable, so a rule-based recap was used (look for 'Decision:' and 'Action:' lines)"
    return redirect(f"/meetings/{mid}", msg=f"{how}: {decisions} decision(s) and {items} action item(s) added. Review and edit below.")


@router.post("/meetings/{mid}/participants")
def add_participants(mid: int, names: str = Form(""), s: Session = Depends(get_session)):
    added = services.add_participants(s, mid, names)
    return redirect(f"/meetings/{mid}", msg=f"{added} participant(s) added.") if added else redirect(f"/meetings/{mid}", err="No new names to add.")


@router.post("/participants/{pid}/attendance")
def set_attendance(pid: int, attendance: str = Form(...), s: Session = Depends(get_session)):
    p = services.get_or_404(s, Participant, pid)
    try:
        services.set_attendance(s, pid, attendance)
    except ValueError as e:
        return redirect(f"/meetings/{p.meeting_id}", err=str(e))
    return redirect(f"/meetings/{p.meeting_id}")


@router.post("/meetings/{mid}/decisions")
def add_decision(mid: int, text: str = Form(""), s: Session = Depends(get_session)):
    try:
        services.add_decision(s, mid, text)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    return redirect(f"/meetings/{mid}")


@router.post("/meetings/{mid}/carry-forward")
def carry_forward(mid: int, s: Session = Depends(get_session)):
    try:
        new = services.carry_forward(s, mid)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    return redirect(f"/meetings/{new.id}", msg="Follow-up created. Open items were carried forward with their history.")


@router.post("/meetings/{mid}/reassign")
def bulk_reassign(mid: int, from_owner: str = Form(""), to_owner: str = Form(""), s: Session = Depends(get_session)):
    try:
        n = services.bulk_reassign(s, from_owner, to_owner)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    return redirect(f"/meetings/{mid}", msg=f"{n} open item(s) reassigned from {from_owner} to {to_owner}.")


@router.post("/meetings/{mid}/link-kb")
def link_kb(mid: int, s: Session = Depends(get_session)):
    m = services.get_or_404(s, Meeting, mid)
    try:
        n, used_ai = services.link_meeting_items(s, m)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    how = "AI linked" if used_ai else "AI was unavailable, linked by keyword match"
    return redirect(f"/meetings/{mid}", msg=f"{how}: {n} new link(s) to the knowledge base.")


@router.get("/meetings/{mid}/catchup")
def catchup(mid: int, request: Request, person: str = "", s: Session = Depends(get_session)):
    m = services.get_or_404(s, Meeting, mid)
    if not person.strip():
        return render(request, "_brief.html", error="Choose who the brief is for.")
    brief, used_ai = ai_service.build_brief(services.catchup_context(s, m, person.strip()))
    return render(request, "_brief.html", brief=brief, used_ai=used_ai, person=person)


# ---------------------------------------------------------------- items
@router.post("/meetings/{mid}/items")
def add_item(mid: int, title: str = Form(""), owner: str = Form(""), due_date: str = Form(""), s: Session = Depends(get_session)):
    try:
        services.create_item(s, mid, title, owner, due_date)
    except ValueError as e:
        return redirect(f"/meetings/{mid}", err=str(e))
    return redirect(f"/meetings/{mid}", msg="Action item added.")


@router.post("/items/{iid}")
def update_item(iid: int, title: str = Form(""), owner: str = Form(""), due_date: str = Form(""),
                status: str = Form(""), outcome: str = Form(""), next: str = Form("/"), s: Session = Depends(get_session)):
    target = safe_next(next, "/")
    try:
        services.update_item(s, iid, title=title, owner=owner, due_raw=due_date, status=status, outcome=outcome)
    except ValueError as e:
        return redirect(target, err=str(e))
    return redirect(target, msg="Item updated.")


@router.post("/items/{iid}/nudge")
def nudge(iid: int, request: Request, s: Session = Depends(get_session)):
    text, used_ai = ai_service.draft_nudge(services.nudge_context(s, iid))
    return render(request, "_nudge.html", text=text, used_ai=used_ai)


# ---------------------------------------------------------------- attention + knowledge base
@router.get("/attention")
def attention(request: Request, s: Session = Depends(get_session)):
    return render(request, "attention.html", attention=services.attention_items(s), stall_days=config.STALL_DAYS)


@router.get("/knowledge")
def knowledge(request: Request, s: Session = Depends(get_session)):
    docs = s.exec(select(Document).order_by(Document.doc_type, Document.title)).all()
    linked = {}
    for link, item in s.exec(select(ItemDocLink, ActionItem).where(ItemDocLink.item_id == ActionItem.id)).all():
        linked.setdefault(link.doc_id, []).append(item)
    return render(request, "knowledge.html", docs=docs, linked=linked)


@router.post("/knowledge")
def add_document(title: str = Form(""), content: str = Form(""), s: Session = Depends(get_session)):
    try:
        services.add_document(s, title, content)
    except ValueError as e:
        return redirect("/knowledge", err=str(e))
    return redirect("/knowledge", msg="Document added.")


# ---------------------------------------------------------------- JSON API
@router.get("/api/meetings")
def api_meetings(s: Session = Depends(get_session)):
    return s.exec(select(Meeting)).all()


@router.get("/api/items")
def api_items(status: str = None, owner: str = None, s: Session = Depends(get_session)):
    q = select(ActionItem)
    if status:
        q = q.where(ActionItem.status == status)
    items = s.exec(q).all()
    return [i for i in items if not owner or i.owner.lower() == owner.lower()]


@router.get("/health")
def health():
    return {"status": "ok", "ai_configured": ai_service.ai_enabled()}
