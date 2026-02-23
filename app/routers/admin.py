from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from openpyxl import Workbook
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models import Appointment, AppointmentPhoto, DaySetting, RescheduleRequest, Settings, SlotCapacity, User
from app.security import hash_password
from app.services import add_audit, add_history, get_settings, slot_end
from app.utils import day_slots

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/day-settings")
async def day_settings_page(
    request: Request,
    date_str: str | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    day = date.fromisoformat(date_str) if date_str else date.today()
    settings = await get_settings(db)
    ds = await db.get(DaySetting, day)
    slots = day_slots(day, settings.slot_minutes)
    slot_caps = (await db.scalars(select(SlotCapacity).where(SlotCapacity.date == day))).all()
    cap_map = {s.slot_start: s.capacity for s in slot_caps}
    return templates.TemplateResponse("admin/day_settings.html", {"request": request, "day": day, "settings": settings, "ds": ds, "slots": slots, "cap_map": cap_map})


@router.post("/day-settings")
async def update_day_settings(
    day: str = Form(...),
    self_assign_enabled: str | None = Form(None),
    day_capacity_override: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    d = date.fromisoformat(day)
    ds = await db.get(DaySetting, d)
    if not ds:
        ds = DaySetting(date=d)
        db.add(ds)
    ds.self_assign_enabled = self_assign_enabled == "on"
    ds.day_capacity_override = day_capacity_override
    await add_audit(db, user.id, "day_settings", "day", str(d), f"self_assign={ds.self_assign_enabled}")
    await db.commit()
    return RedirectResponse(f"/admin/day-settings?date={day}", status_code=303)


@router.post("/slot-capacity")
async def set_slot_capacity(
    day: str = Form(...),
    slot_start: str = Form(...),
    capacity: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    d = date.fromisoformat(day)
    slot_dt = datetime.fromisoformat(slot_start)
    existing = await db.scalar(select(SlotCapacity).where(and_(SlotCapacity.date == d, SlotCapacity.slot_start == slot_dt)))
    if existing:
        existing.capacity = capacity
    else:
        db.add(SlotCapacity(date=d, slot_start=slot_dt, capacity=capacity))
    await add_audit(db, user.id, "slot_capacity", "slot", slot_start, f"capacity={capacity}")
    await db.commit()
    return RedirectResponse(f"/admin/day-settings?date={day}", status_code=303)


@router.get("/assign")
async def assign_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    appointments = (await db.scalars(select(Appointment).where(Appointment.status.in_(["new", "accepted", "reschedule_pending"])).order_by(Appointment.slot_start))).all()
    fields = (await db.scalars(select(User).where(User.role == "field"))).all()
    return templates.TemplateResponse("admin/assign.html", {"request": request, "appointments": appointments, "fields": fields})


@router.post("/assign")
async def mass_assign(
    appointment_ids: list[int] = Form(...),
    field_user_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    for appt_id in appointment_ids:
        await db.execute(update(Appointment).where(Appointment.id == appt_id).values(assigned_to=field_user_id, status="accepted"))
        await add_history(db, appt_id, user.id, "assign", f"Назначен сотрудник {field_user_id}")
    await add_audit(db, user.id, "mass_assign", "appointment", ",".join(map(str, appointment_ids)), f"field={field_user_id}")
    await db.commit()
    return RedirectResponse("/admin/assign", status_code=303)


@router.post("/reschedule/{request_id}/approve")
async def approve_reschedule(
    request_id: int,
    new_slot: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    req = await db.get(RescheduleRequest, request_id)
    settings = await get_settings(db)
    if req:
        req.status = "approved"
        slot_start_dt = datetime.fromisoformat(new_slot)
        await db.execute(
            update(Appointment)
            .where(Appointment.id == req.appointment_id)
            .values(slot_start=slot_start_dt, slot_end=slot_end(slot_start_dt, settings.slot_minutes), assigned_to=None, status="new")
        )
        await add_history(db, req.appointment_id, user.id, "reschedule_approved", f"Новый слот {new_slot}")
        await add_audit(db, user.id, "reschedule_approved", "appointment", str(req.appointment_id), new_slot)
        await db.commit()
    return RedirectResponse("/admin/assign", status_code=303)


@router.get("/export")
async def export_xlsx(
    date_str: str = Query(..., alias="date"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    day = date.fromisoformat(date_str)
    start = datetime.combine(day, datetime.min.time())
    end = datetime.combine(day + timedelta(days=1), datetime.min.time())
    appointments = (await db.scalars(select(Appointment).where(Appointment.slot_start >= start, Appointment.slot_start < end))).all()

    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Услуга", "ФИО", "Л/С", "Телефон", "Адрес", "Слот", "Статус", "Исполнитель", "Изменено", "Фото"])
    for a in appointments:
        photos_count = await db.scalar(select(func.count(AppointmentPhoto.id)).where(AppointmentPhoto.appointment_id == a.id))
        ws.append([a.id, a.service_id, a.full_name, a.account_number, a.phone, f"{a.street} {a.house}-{a.apartment}", a.slot_start.isoformat(sep=" "), a.status, a.assigned_to or "", a.updated_at.isoformat(sep=" "), photos_count])

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    await add_audit(db, user.id, "export", "date", date_str, "xlsx export")
    await db.commit()
    return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=appointments-{date_str}.xlsx"})


@router.get("/settings")
async def settings_page(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("admin"))):
    settings = await get_settings(db)
    return templates.TemplateResponse("admin/settings.html", {"request": request, "settings": settings, "today": date.today().isoformat()})


@router.post("/settings")
async def update_settings(
    slot_minutes: int = Form(...),
    default_capacity: int = Form(...),
    pin: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    settings = await get_settings(db)
    settings.slot_minutes = slot_minutes
    settings.default_capacity = default_capacity
    if pin.strip():
        if len(pin.encode("utf-8")) > 72:
            raise HTTPException(status_code=400, detail="PIN слишком длинный (bcrypt допускает до 72 байт)")
        settings.field_extra_pin_hash = hash_password(pin)
    await add_audit(db, user.id, "settings", "settings", "1", "updated")
    await db.commit()
    return RedirectResponse("/admin/settings", status_code=303)
