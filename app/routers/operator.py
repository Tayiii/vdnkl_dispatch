from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models import Appointment, AppointmentHistory, Service, User
from app.services import add_audit, add_history, create_appointment_atomic, get_settings, slot_end
from app.utils import day_slots

router = APIRouter(prefix="/operator", tags=["operator"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/schedule")
async def schedule(
    request: Request,
    date_str: str | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("operator", "admin")),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    settings = await get_settings(db)
    slots = []
    for slot in day_slots(day, settings.slot_minutes):
        used = await db.scalar(
            select(func.count(Appointment.id)).where(and_(Appointment.slot_start == slot, Appointment.status != "cancelled"))
        )
        slots.append({"slot": slot, "used": used or 0})
    return templates.TemplateResponse("operator/schedule.html", {"request": request, "slots": slots, "day": day, "settings": settings})


@router.get("/appointment/new")
async def new_appointment_form(
    request: Request,
    slot: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("operator", "admin")),
):
    services = (await db.scalars(select(Service).order_by(Service.name))).all()
    return templates.TemplateResponse("operator/new_appointment.html", {"request": request, "slot": slot, "services": services})


@router.post("/appointment/new")
async def create_appointment(
    request: Request,
    slot: str = Form(...),
    service_id: int = Form(...),
    full_name: str = Form(...),
    account_number: str = Form(...),
    phone: str = Form(...),
    street: str = Form(...),
    house: str = Form(...),
    apartment: str = Form(...),
    address_extra: str = Form(""),
    operator_comment: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("operator", "admin")),
):
    settings = await get_settings(db)
    slot_start = datetime.fromisoformat(slot)
    payload = {
        "service_id": service_id,
        "status": "new",
        "full_name": full_name,
        "account_number": account_number,
        "phone": phone,
        "street": street,
        "house": house,
        "apartment": apartment,
        "address_extra": address_extra or None,
        "slot_start": slot_start,
        "slot_end": slot_end(slot_start, settings.slot_minutes),
        "operator_comment": operator_comment or None,
        "created_by": user.id,
    }
    try:
        appointment = await create_appointment_atomic(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(f"/operator/appointment/{appointment.id}", status_code=303)


@router.get("/appointment/{appointment_id}")
async def appointment_card(
    request: Request,
    appointment_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("operator", "admin")),
):
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404)
    history = (await db.scalars(select(AppointmentHistory).where(AppointmentHistory.appointment_id == appointment_id).order_by(AppointmentHistory.created_at.desc()))).all()
    return templates.TemplateResponse("operator/appointment_card.html", {"request": request, "appointment": appointment, "history": history})


@router.post("/appointment/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: int,
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("operator", "admin")),
):
    appt = await db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404)
    await db.execute(update(Appointment).where(Appointment.id == appointment_id).values(status="cancelled", cancelled_reason=reason))
    await add_history(db, appointment_id, user.id, "cancel", f"Отменено: {reason}")
    await add_audit(db, user.id, "cancel", "appointment", str(appointment_id), reason)
    await db.commit()
    return RedirectResponse(f"/operator/appointment/{appointment_id}", status_code=303)
