from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AppointmentHistory,
    AuditEvent,
    DaySetting,
    Settings,
    SlotCapacity,
)


async def add_history(db: AsyncSession, appointment_id: int, user_id: int | None, event_type: str, description: str):
    db.add(AppointmentHistory(appointment_id=appointment_id, user_id=user_id, event_type=event_type, description=description))


async def add_audit(db: AsyncSession, user_id: int | None, event_type: str, entity_type: str, entity_id: str, details: str = ""):
    db.add(AuditEvent(user_id=user_id, event_type=event_type, entity_type=entity_type, entity_id=entity_id, details=details))


async def get_settings(db: AsyncSession) -> Settings:
    settings = await db.get(Settings, 1)
    if not settings:
        settings = Settings(id=1, slot_minutes=30, default_capacity=6)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def capacity_for_slot(db: AsyncSession, slot_start: datetime) -> int:
    settings = await get_settings(db)
    day = slot_start.date()
    slot_specific = await db.scalar(select(SlotCapacity.capacity).where(and_(SlotCapacity.date == day, SlotCapacity.slot_start == slot_start)))
    if slot_specific is not None:
        return slot_specific
    day_cap = await db.scalar(select(DaySetting.day_capacity_override).where(DaySetting.date == day))
    if day_cap:
        return day_cap
    return settings.default_capacity


async def create_appointment_atomic(db: AsyncSession, payload: dict):
    slot_start = payload["slot_start"]
    slot_end = payload["slot_end"]
    await db.execute(text("BEGIN IMMEDIATE"))
    cap = await capacity_for_slot(db, slot_start)
    current = await db.scalar(select(func.count(Appointment.id)).where(and_(Appointment.slot_start == slot_start, Appointment.status != "cancelled")))
    if current >= cap:
        await db.rollback()
        raise ValueError("Слот заполнен")
    appointment = Appointment(**payload)
    db.add(appointment)
    await db.flush()
    await add_history(db, appointment.id, payload["created_by"], "create", f"Создана заявка на слот {slot_start}")
    await add_audit(db, payload["created_by"], "create", "appointment", str(appointment.id), f"slot={slot_start},end={slot_end}")
    await db.commit()
    await db.refresh(appointment)
    return appointment


async def accept_self_assign_atomic(db: AsyncSession, appointment_id: int, field_user_id: int) -> bool:
    appt = await db.get(Appointment, appointment_id)
    if not appt:
        return False
    ds = await db.get(DaySetting, appt.slot_start.date())
    if not ds or not ds.self_assign_enabled:
        return False

    await db.execute(text("BEGIN IMMEDIATE"))
    result = await db.execute(
        update(Appointment)
        .where(and_(Appointment.id == appointment_id, Appointment.status == "new", Appointment.assigned_to.is_(None)))
        .values(status="accepted", assigned_to=field_user_id)
    )
    if result.rowcount != 1:
        await db.rollback()
        return False
    await add_history(db, appointment_id, field_user_id, "accept", "Заявка принята выездным")
    await add_audit(db, field_user_id, "accept", "appointment", str(appointment_id), "self-assign accepted")
    await db.commit()
    return True


def slot_end(slot_start: datetime, slot_minutes: int) -> datetime:
    return slot_start + timedelta(minutes=slot_minutes)


def is_today_or_tomorrow(day: date) -> bool:
    today = date.today()
    return day in {today, today + timedelta(days=1)}
