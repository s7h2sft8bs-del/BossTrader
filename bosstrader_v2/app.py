import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

app = FastAPI()

# ---------- DB ----------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./boss.db")

# Render Postgres sometimes gives "postgres://", SQLAlchemy wants "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    paid_until = Column(DateTime, nullable=True)
    plan = Column(String, default="basic")
    tg_chat_id = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


# ---------- Schemas ----------
class CreateUserBody(BaseModel):
    email: EmailStr
    tg_chat_id: Optional[str] = None


# ---------- Routes ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/admin/create-user")
def admin_create_user(body: CreateUserBody, db: Session = Depends(get_db)):
    email = str(body.email).lower().strip()
    tg_chat_id = body.tg_chat_id

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return {"ok": True, "user_id": existing.id, "api_key": existing.api_key}

    api_key = new_api_key()
    user = User(
        email=email,
        api_key=api_key,
        is_active=True,
        paid_until=None,
        plan="basic",
        tg_chat_id=str(tg_chat_id) if tg_chat_id else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"ok": True, "user_id": user.id, "api_key": api_key}
