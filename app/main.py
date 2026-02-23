from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select

from app.database import AsyncSessionLocal, Base, engine
from app.models import Service, Settings, User
from app.routers import admin, auth, field, operator
from app.security import hash_password

app = FastAPI(title="VDNKL Dispatch")
app.add_middleware(SessionMiddleware, secret_key="change-me-in-prod", same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(auth.router)
app.include_router(operator.router)
app.include_router(field.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"ok": True, "login": "/login"}


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        if not await db.get(Settings, 1):
            db.add(Settings(id=1, slot_minutes=30, default_capacity=6))

        for username, role, password in [
            ("admin", "admin", "admin123"),
            ("operator", "operator", "operator123"),
            ("field", "field", "field123"),
        ]:
            exists = await db.scalar(select(User).where(User.username == username))
            if not exists:
                db.add(User(username=username, role=role, password_hash=hash_password(password)))

        for name, extra_allowed in [
            ("опломбировка", True),
            ("распломбировка", True),
            ("проверка пломб", True),
        ]:
            exists = await db.scalar(select(Service).where(Service.name == name))
            if not exists:
                db.add(Service(name=name, is_extra_allowed=extra_allowed))

        await db.commit()
