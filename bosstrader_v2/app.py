import os
import re
import secrets
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker


# -----------------------------
# Config
# -----------------------------
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./boss.db")
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# Render can provide postgres:// — SQLAlchemy wants postgresql://
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# -----------------------------
# DB Model
# -----------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    api_key = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    plan = Column(String(50), default="basic", nullable=False)
    paid_until = Column(DateTime, nullable=True)
    tg_chat_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


Base.metadata.create_all(bind=engine)


# -----------------------------
# Schemas
# -----------------------------
class CreateUserBody(BaseModel):
    email: str = Field(..., examples=["stephenmartinez@gmail.com"])
    tg_chat_id: Optional[str] = None

    def model_post_init(self, __context):
        e = (self.email or "").strip()
        if not EMAIL_RE.match(e):
            raise ValueError("Invalid email")
        self.email = e


class TVWebhookBody(BaseModel):
    secret: Optional[str] = None
    symbol: Optional[str] = None
    action: Optional[str] = None
    payload: Optional[dict] = None


# -----------------------------
# App
# -----------------------------
app = FastAPI(title="BossTrader API", version="1.0.0")


@app.get("/health")
def health():
    return {"ok": True}


def new_api_key() -> str:
    return secrets.token_urlsafe(32)


def require_admin(x_admin_secret: Optional[str]):
    # If ADMIN_SECRET is set, header must match.
    if ADMIN_SECRET and (x_admin_secret != ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="Invalid admin secret")


@app.post("/admin/create-user")
def admin_create_user(
    body: CreateUserBody,
    x_admin_secret: Optional[str] = Header(default=None, alias="x-admin-secret"),
):
    require_admin(x_admin_secret)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == body.email).first()
        if existing:
            return {
                "ok": True,
                "user_id": existing.id,
                "api_key": existing.api_key,
                "existing": True,
            }

        api_key = new_api_key()
        user = User(
            email=body.email,
            api_key=api_key,
            is_active=True,
            plan="basic",
            paid_until=None,
            tg_chat_id=body.tg_chat_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"ok": True, "user_id": user.id, "api_key": user.api_key, "existing": False}

    except IntegrityError:
        db.rollback()
        existing = db.query(User).filter(User.email == body.email).first()
        if existing:
            return {
                "ok": True,
                "user_id": existing.id,
                "api_key": existing.api_key,
                "existing": True,
            }
        raise
    finally:
        db.close()


@app.post("/tv-webhook")
async def tv_webhook(
    request: Request,
    body: TVWebhookBody,
    x_tv_secret: Optional[str] = Header(default=None, alias="x-tv-secret"),
):
    provided = (x_tv_secret or body.secret or "").strip()
    if not TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Server missing TV_WEBHOOK_SECRET")
    if provided != TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Bad secret")
print("✅ tv_webhook reached + secret passed")
# ✅ secret OK, continue
# --- TELEGRAM NOTIFY (BossTrader) ---
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            msg = (
                "BossTrader ALERT\n"
                f"symbol: {getattr(body, 'symbol', '')}\n"
                f"side: {getattr(body, 'side', '')}\n"
                f"qty: {getattr(body, 'qty', '')}\n"
                f"timeframe: {getattr(body, 'timeframe', '')}\n"
                f"strategy: {getattr(body, 'strategy', '')}\n"
                f"comment: {getattr(body, 'comment', '')}"
            )
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram send failed:", e)
    # --- END TELEGRAM NOTIFY ---
    raw = await request.body()
    return {"ok": True, "received_bytes": len(raw), "symbol": body.symbol, "action": body.action}
