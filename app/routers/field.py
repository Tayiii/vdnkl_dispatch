from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models import (
    Appointment,
    AppointmentHistory,
    AppointmentPhoto,
    DaySetting,
    Meter,
    RescheduleRequest,
    Seal,
    Service,
    Settings,
    User,
)
from app.security import verify_password
from app.services import add_audit, add_history, create_appointment_atomic, get_settings, slot_end, accept_self_assign_atomic
from app.utils import save_upload

router = APIRouter(prefix="/field", tags=["field"])
templates = Jinja2Templates(directory="app/templates")
BASE_DIR = Path(__file__).resolve().parent.parent.parent


@router.get("/days")
async def field_days(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("field", "admin"))):
    today = date.today()
    dates = [today, today + timedelta(days=1)]
    self_days = (await db.scalars(select(DaySetting).where(DaySetting.self_assign_enabled.is_(True), DaySetting.date >= today))).all()
    dates.extend([d.date for d in self_days])
    unique_dates = sorted(set(dates))
    return templates.TemplateResponse("field/days.html", {"request": request, "dates": unique_dates})


@router.get("/list")
async def field_list(
    request: Request,
    date_str: str | None = Query(None, alias="date"),
    filter_type: str = Query("new", alias="filter"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("field", "admin")),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    query = select(Appointment).where(Appointment.slot_start >= datetime.combine(day, datetime.min.time()), Appointment.slot_start < datetime.combine(day + timedelta(days=1), datetime.min.time()))
    if filter_type == "new":
        query = query.where(Appointment.status == "new")
    elif filter_type == "mine":
        query = query.where(Appointment.assigned_to == user.id)
    else:
        query = query.where(Appointment.status == filter_type)
    appointments = (await db.scalars(query.order_by(Appointment.slot_start))).all()
    return templates.TemplateResponse("field/list.html", {"request": request, "appointments": appointments, "day": day, "filter": filter_type})


@router.post("/appointment/{appointment_id}/accept")
async def accept_appointment(appointment_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("field", "admin"))):
    ok = await accept_self_assign_atomic(db, appointment_id, user.id)
    if not ok:
        raise HTTPException(status_code=400, detail="Нельзя принять заявку")
    return RedirectResponse(f"/field/appointment/{appointment_id}", status_code=303)


@router.get("/appointment/{appointment_id}")
async def field_appointment_card(
    request: Request,
    appointment_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("field", "admin")),
):
    appt = await db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404)
    photos = (await db.scalars(select(AppointmentPhoto).where(AppointmentPhoto.appointment_id == appointment_id, AppointmentPhoto.kind == "result"))).all()
    history = (await db.scalars(select(AppointmentHistory).where(AppointmentHistory.appointment_id == appointment_id).order_by(AppointmentHistory.created_at.desc()))).all()
    return templates.TemplateResponse("field/appointment_card.html", {"request": request, "appointment": appt, "photos": photos, "history": history})


@router.post("/appointment/{appointment_id}/status")
async def change_status(
    appointment_id: int,
    status_value: str = Form(...),
    field_notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("field", "admin")),
):
    appt = await db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404)
    await db.execute(update(Appointment).where(Appointment.id == appointment_id).values(status=status_value, field_notes=field_notes or appt.field_notes))
    await add_history(db, appointment_id, user.id, "status", f"Статус: {status_value}")
    await add_audit(db, user.id, "status", "appointment", str(appointment_id), status_value)
    await db.commit()
    return RedirectResponse(f"/field/appointment/{appointment_id}", status_code=303)


@router.post("/appointment/{appointment_id}/result")
async def add_result(
    appointment_id: int,
    meter_number: list[str] = Form([]),
    meter_model: list[str] = Form([]),
    passport_verification_date: list[str] = Form([]),
    verification_interval: list[str] = Form([]),
    seal_number: list[str] = Form([]),
    photo_comment: list[str] = Form([]),
    photos: list[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("field", "admin")),
):
    if len(photos) > 10:
        raise HTTPException(status_code=400, detail="Максимум 10 фото")
    for i, file in enumerate(photos):
        content = await file.read()
        rel = save_upload(BASE_DIR, appointment_id, file.filename or "photo.jpg", content)
        db.add(AppointmentPhoto(appointment_id=appointment_id, kind="result", path=rel, comment=photo_comment[i] if i < len(photo_comment) else None))
    for i, num in enumerate(meter_number):
        if num.strip():
            db.add(Meter(appointment_id=appointment_id, meter_number=num, meter_model=meter_model[i] if i < len(meter_model) else None, passport_verification_date=passport_verification_date[i] if i < len(passport_verification_date) else None, verification_interval=verification_interval[i] if i < len(verification_interval) else None))
    for s in seal_number:
        if s.strip():
            db.add(Seal(appointment_id=appointment_id, seal_number=s))
    await add_history(db, appointment_id, user.id, "result", "Добавлены результаты выезда")
    await add_audit(db, user.id, "result", "appointment", str(appointment_id), "photos/meters/seals added")
    await db.commit()
    return RedirectResponse(f"/field/appointment/{appointment_id}", status_code=303)


@router.get("/reschedule/{appointment_id}")
async def reschedule_form(request: Request, appointment_id: int, user: User = Depends(require_role("field", "admin"))):
    return templates.TemplateResponse("field/reschedule.html", {"request": request, "appointment_id": appointment_id})


@router.post("/reschedule/{appointment_id}")
async def create_reschedule_request(
    appointment_id: int,
    reason: str = Form(...),
    photos: list[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("field", "admin")),
):
    if len(photos) > 10:
        raise HTTPException(status_code=400, detail="Максимум 10 фото")
    req = RescheduleRequest(appointment_id=appointment_id, requested_by=user.id, reason=reason, status="pending")
    db.add(req)
    for f in photos:
        rel = save_upload(BASE_DIR, appointment_id, f.filename or "reschedule.jpg", await f.read())
        db.add(AppointmentPhoto(appointment_id=appointment_id, kind="reschedule", path=rel))
    await db.execute(update(Appointment).where(Appointment.id == appointment_id).values(status="reschedule_pending"))
    await add_history(db, appointment_id, user.id, "reschedule_request", reason)
    await add_audit(db, user.id, "reschedule_request", "appointment", str(appointment_id), reason)
    await db.commit()
    return RedirectResponse(f"/field/appointment/{appointment_id}", status_code=303)


@router.get("/create-extra")
async def extra_form(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("field", "admin"))):
    token_exp = request.session.get("extra_pin_exp")
    allowed = token_exp and datetime.fromisoformat(token_exp) > datetime.utcnow()
    services = (await db.scalars(select(Service).where(Service.is_extra_allowed.is_(True)).order_by(Service.name))).all()
    return templates.TemplateResponse("field/create_extra.html", {"request": request, "allowed": allowed, "services": services})


@router.post("/create-extra/pin")
async def verify_pin(request: Request, pin: str = Form(...), db: AsyncSession = Depends(get_db), user: User = Depends(require_role("field", "admin"))):
    settings = await db.get(Settings, 1)
    if not settings or not settings.field_extra_pin_hash or not verify_password(pin, settings.field_extra_pin_hash):
        raise HTTPException(status_code=400, detail="Неверный PIN")
    request.session["extra_pin_exp"] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    return RedirectResponse("/field/create-extra", status_code=303)


@router.post("/create-extra")
async def create_extra(
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
    user: User = Depends(require_role("field", "admin")),
):
    token_exp = request.session.get("extra_pin_exp")
    if not token_exp or datetime.fromisoformat(token_exp) <= datetime.utcnow():
        raise HTTPException(status_code=403, detail="Требуется PIN")
    settings = await get_settings(db)
    slot_start_dt = datetime.fromisoformat(slot)
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
        "slot_start": slot_start_dt,
        "slot_end": slot_end(slot_start_dt, settings.slot_minutes),
        "operator_comment": operator_comment or None,
        "created_by": user.id,
        "is_extra": True,
    }
    appt = await create_appointment_atomic(db, payload)
    await add_history(db, appt.id, user.id, "extra_create", "Создана внеплановая заявка")
    await add_audit(db, user.id, "extra_create", "appointment", str(appt.id), "field extra")
    await db.commit()
    return RedirectResponse(f"/field/appointment/{appt.id}", status_code=303)
