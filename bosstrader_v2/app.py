import os
import json
import secrets
from datetime import datetime
from typing import Optional, Any, Dict

import requests
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ----------------------------
# Settings (ENV)
# ----------------------------
ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # you MUST set this on Render
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    # local fallback (Render filesystem is ephemeral; for production use a real DB later)
    DATABASE_URL = "sqlite:///./data.db"

# SQLite needs this flag
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------------------
# DB Models
# ----------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    api_key = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    paid_until = Column(DateTime, nullable=True)
    plan = Column(String(50), default="basic", nullable=False)
    tg_chat_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class AlertLog(Base):
    __tablename__ = "alert_logs"
    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    payload = Column(Text, nullable=False)

Base.metadata.create_all(bind=engine)

# ----------------------------
# App
# ----------------------------
app = FastAPI(title="BossTrader API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def new_api_key() -> str:
    return secrets.token_urlsafe(32)

def require_admin(x_admin_key: Optional[str]):
    if not ADMIN_KEY:
        raise HTTPException(500, "ADMIN_KEY is not set on server (Render env var missing).")
    if not x_admin_key or x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")

def require_user(db: Session, x_api_key: Optional[str]) -> User:
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY header")
    user = db.query(User).filter(User.api_key == x_api_key).first()
    if not user:
        raise HTTPException(401, "Invalid API key")
    if not user.is_active:
        raise HTTPException(403, "User inactive")
    if user.paid_until and user.paid_until < datetime.utcnow():
        raise HTTPException(402, "Subscription expired")
    return user

def telegram_send(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)

# ----------------------------
# Schemas
# ----------------------------
class CreateUserBody(BaseModel):
    email: EmailStr
    tg_chat_id: Optional[str] = None

class TVWebhookBody(BaseModel):
    # accept anything TradingView sends (we store + forward)
    payload: Dict[str, Any]

# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/admin/create-user")
def admin_create_user(
    body: CreateUserBody,
    db: Session = Depends(get_db),
    x_admin_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    require_admin(x_admin_key)

    email = body.email
    tg_chat_id = body.tg_chat_id

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return {"ok": True, "existing": True, "user_id": existing.id, "api_key": existing.api_key}

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

    return {"ok": True, "existing": False, "user_id": user.id, "api_key": api_key}

@app.post("/tv-webhook")
async def tv_webhook(
    body: TVWebhookBody,
    request: Request,
    db: Session = Depends(get_db),
    x_tv_secret: Optional[str] = Header(default=None, convert_underscores=False),
):
    # Security: allow secret either via header or query param
    secret_q = request.query_params.get("secret")
    secret = x_tv_secret or secret_q

    if not TV_WEBHOOK_SECRET:
        raise HTTPException(500, "TV_WEBHOOK_SECRET not set on server")
    if not secret or secret != TV_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid TV webhook secret")

    # Log payload
    payload_str = json.dumps(body.payload, ensure_ascii=False)
    db.add(AlertLog(payload=payload_str))
    db.commit()

    # Send to Telegram (simple)
    telegram_send(f"📈 TradingView Alert:\n{payload_str}")

    return {"ok": True}

@app.get("/me")
def me(
    db: Session = Depends(get_db),
    x_api_key: Optional[str] = Header(default=None, convert_underscores=False),
):
    user = require_user(db, x_api_key)
    return {
        "ok": True,
        "email": user.email,
        "plan": user.plan,
        "paid_until": user.paid_until.isoformat() if user.paid_until else None,
        "is_active": user.is_active,
    }
