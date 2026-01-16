cd ~/BossTrader/bosstrader_v2
> app.py
import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    text,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session


# ---------------------------
# Settings
# ---------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Optional: protect admin endpoints (recommended).
# If ADMIN_KEY is set, you must send header: X-Admin-Key: <ADMIN_KEY>
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()

# Optional: extra protection for TradingView webhook
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "").strip()

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bosstrader")


# ---------------------------
# Database
# ---------------------------
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    plan = Column(String, default="basic", nullable=False)
    paid_until = Column(DateTime(timezone=True), nullable=True)

    tg_chat_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)


def new_api_key() -> str:
    # 32+ chars, URL-safe
    return secrets.token_urlsafe(32)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def require_admin(request: Request):
    # If ADMIN_KEY isn't set, admin endpoints are open (dev mode).
    if not ADMIN_KEY:
        return
    got = request.headers.get("X-Admin-Key", "")
    if got != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Missing/invalid admin key")


def is_paid(user: User) -> bool:
    if user.paid_until is None:
        return False
    return user.paid_until >= now_utc()


# ---------------------------
# FastAPI
# ---------------------------
app = FastAPI(title="BossTrader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    log.info("DB initialized")


# ---------------------------
# Models
# ---------------------------
class EmailBody(BaseModel):
    email: EmailStr


class CreateUserBody(EmailBody):
    tg_chat_id: Optional[str] = None


class SetPaidUntilBody(EmailBody):
    days: int = 30


class WebhookBody(BaseModel):
    # TradingView can send any JSON; keep flexible
    api_key: Optional[str] = None
    secret: Optional[str] = None
    payload: Optional[dict[str, Any]] = None

    # Common fields people send
    symbol: Optional[str] = None
    side: Optional[str] = None
    timeframe: Optional[str] = None
    message: Optional[str] = None


# ---------------------------
# Health
# ---------------------------
@app.get("/health")
def health():
    return {"ok": True, "ts": now_utc().isoformat()}


# ---------------------------
# Admin
# ---------------------------
@app.post("/admin/create-user")
def admin_create_user(body: CreateUserBody, request: Request, db: Session = Depends(get_db)):
    require_admin(request)

    email = body.email
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


@app.post("/admin/set-paid-until")
def admin_set_paid_until(body: SetPaidUntilBody, request: Request, db: Session = Depends(get_db)):
    require_admin(request)

    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.paid_until = now_utc() + timedelta(days=int(body.days))
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"ok": True, "user_id": user.id, "paid_until": user.paid_until.isoformat()}


@app.get("/admin/user")
def admin_get_user(email: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request)

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "ok": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "api_key": user.api_key,
            "is_active": user.is_active,
            "plan": user.plan,
            "paid_until": user.paid_until.isoformat() if user.paid_until else None,
            "tg_chat_id": user.tg_chat_id,
        },
    }


# ---------------------------
# TradingView Webhook
# ---------------------------
@app.post("/tv-webhook")
async def tv_webhook(body: WebhookBody, request: Request, db: Session = Depends(get_db)):
    # 1) Optional global secret check (recommended)
    # TradingView alert can include {"secret":"..."} in JSON
    if TV_WEBHOOK_SECRET:
        if body.secret != TV_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # 2) Identify user by api_key (header wins, fallback to body)
    api_key = request.headers.get("X-Api-Key") or body.api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing api_key (use header X-Api-Key or JSON field api_key)")

    user = db.query(User).filter(User.api_key == api_key).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or inactive api_key")

    # 3) Enforce paid gate (optional – you can relax this if you want)
    if not is_paid(user):
        raise HTTPException(status_code=402, detail="Payment required")

    # 4) Accept the webhook
    # Here is where we’ll later forward to Telegram / broker.
    log.info(f"Webhook received user={user.email} symbol={body.symbol} side={body.side} tf={body.timeframe}")

    return {
        "ok": True,
        "received": True,
        "user": user.email,
        "ts": now_utc().isoformat(),
        "echo": {
            "symbol": body.symbol,
            "side": body.side,
            "timeframe": body.timeframe,
            "message": body.message,
            "payload": body.payload,
        },
    }
