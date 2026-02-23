from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)  # operator, field, admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    is_extra_allowed: Mapped[bool] = mapped_column(Boolean, default=False)


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)
    default_capacity: Mapped[int] = mapped_column(Integer, default=6)
    field_extra_pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DaySetting(Base):
    __tablename__ = "day_settings"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    self_assign_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    day_capacity_override: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SlotCapacity(Base):
    __tablename__ = "slot_capacities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    capacity: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("date", "slot_start", name="uq_slot_capacity"),)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    account_number: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(64))
    street: Mapped[str] = mapped_column(String(255))
    house: Mapped[str] = mapped_column(String(64))
    apartment: Mapped[str] = mapped_column(String(64))
    address_extra: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slot_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    slot_end: Mapped[datetime] = mapped_column(DateTime)
    operator_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_extra: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    service = relationship("Service")


class AppointmentPhoto(Base):
    __tablename__ = "appointment_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="result")
    path: Mapped[str] = mapped_column(String(512))
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    meter_number: Mapped[str] = mapped_column(String(64))
    meter_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    passport_verification_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_interval: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Seal(Base):
    __tablename__ = "seals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    seal_number: Mapped[str] = mapped_column(String(64))


class RescheduleRequest(Base):
    __tablename__ = "reschedule_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppointmentHistory(Base):
    __tablename__ = "appointment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
