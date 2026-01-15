from fastapi import FastAPI, Request, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uuid

from db import Base, ENGINE, get_db
from models import User, Proposal
from security import new_api_key, is_paid_active
from telegram import tg_send, tg_answer_callback
from risk import risk_gate
from adapters.manual import ManualAdapter

app = FastAPI()
Base.metadata.create_all(bind=ENGINE)

broker = ManualAdapter()
“

def get_user_by_api_key(db: Session, api_key: str):
    return db.query(User).filter(User.api_key == api_key).first()

def get_user_by_chat_id(db: Session, chat_id: str):
    return db.query(User).filter(User.tg_chat_id == str(chat_id)).first()

@app.get("/health")
def health(db: Session = Depends(get_db)):
    users = db.query(User).count()
    props = db.query(Proposal).count()
    return {"ok": True, "users": users, "proposals": props}
class CreateUserBody(BaseModel):
    email: EmailStr
    plan: str = "member"
    days: int = 30
    tg_chat_id: Optional[str] = None

class EmailBody(BaseModel):
    email: EmailStr

class SetPaidUntilBody(BaseModel):
    email: EmailStr
    days: int = 30

# ---------------- ADMIN (starter endpoints) ----------------
# NOTE: These are open right now. We'll add admin auth next.
@app.post("/admin/create-user")
async def admin_create_user(body: CreateUserBody, db: Session = Depends(get_db)):

    email = body.emailtg_chat_id = body.tg_chat_id# optional for now

    if not email:
        raise HTTPException(400, "email required")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return {"ok": True, "user_id": existing.id, "api_key": existing.api_key}

    api_key = new_api_key()
    user = User(
        email=email,
        api_key=api_key,
        is_active=True,
        paid_until=None,   # set after payment
        plan="basic",
        tg_chat_id=str(tg_chat_id) if tg_chat_id else None
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "user_id": user.id, "api_key": user.api_key}

@app.post("/admin/disable-user")
async def admin_disable_user(body: EmailBody, db: Session = Depends(get_db)):

    email = body.emailuser = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, "user not found")

    user.is_active = False
    db.commit()
    return {"ok": True, "disabled": email}

@app.post("/admin/set-paid-until")
async def admin_set_paid_until(body: SetPaidUntilBody, db: Session = Depends(get_db)):

    email = body.emaildays = body.daysuser = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, "user not found")

    user.paid_until = datetime.utcnow() + timedelta(days=days)
    user.is_active = True
    db.commit()
    return {"ok": True, "paid_until": user.paid_until.isoformat()}

# ---------------- TradingView Webhook ----------------
@app.post("/tv-webhook")
async def tv_webhook(request: Request, db: Session = Depends(get_db)):
    """
    TradingView should send JSON like:
    {
      "api_key": "USER_API_KEY",
      "symbol": "MNQ",
      "side": "LONG",
      "timeframe": "1m",
      "reason": "VWAP reclaim + volume spike"
    }
    """

    api_key = body.get("api_key")
    if not api_key:
        raise HTTPException(401, "missing api_key")

    user = get_user_by_api_key(db, api_key)
    if not user:
        raise HTTPException(401, "invalid api_key")

    # membership enforcement
    if not is_paid_active(user):
        return {"ok": False, "blocked": "membership_inactive"}

    symbol = body.get("symbol")
    side = body.get("side")
    timeframe = body.get("timeframe", "")
    reason = body.get("reason", "")

    if not symbol or not side:
        raise HTTPException(400, "symbol and side required")

    pid = str(uuid.uuid4())

    prop = Proposal(
        id=pid,
        user_id=user.id,
        symbol=str(symbol),
        side=str(side),
        timeframe=str(timeframe),
        reason=str(reason),
        status="PENDING"
    )
    db.add(prop)
    db.commit()

    # send to telegram
    if user.tg_chat_id:
        text = (
            f"📌 <b>Trade Proposal</b>\n"
            f"<b>{prop.symbol}</b> — <b>{prop.side}</b>\n"
            f"TF: {prop.timeframe}\n"
            f"Reason: {prop.reason}\n\n"
            f"Proposal ID: <code>{pid}</code>"
        )
        kb = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve:{pid}"},
                {"text": "❌ Reject", "callback_data": f"reject:{pid}"}
            ]]
        }
        tg_send(user.tg_chat_id, text, kb)

    return {"ok": True, "proposal_id": pid}

# ---------------- Telegram Webhook ----------------
@app.post("/tg-webhook")
async def tg_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Set your Telegram bot webhook to:
      https://YOUR-RENDER-URL/tg-webhook

    Telegram will POST updates here.
    """

    cq = body.get("callback_query")
    if not cq:
        return {"ok": True}

    cq_id = cq.get("id")
    data = cq.get("data", "")
    msg = cq.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))

    user = get_user_by_chat_id(db, chat_id)
    if not user:
        tg_answer_callback(cq_id, "User not linked.")
        return {"ok": True}

    # membership enforcement on button clicks
    if not is_paid_active(user):
        tg_answer_callback(cq_id, "Membership inactive — renew to resume.")
        return {"ok": True}

    if data.startswith("approve:"):
        pid = data.split(":", 1)[1]
        prop = db.query(Proposal).filter(Proposal.id == pid, Proposal.user_id == user.id).first()
        if not prop:
            tg_answer_callback(cq_id, "Proposal not found.")
            return {"ok": True}

        # Risk Gate (TopstepX rules go here)
        allowed, reason = risk_gate(user, prop)
        if not allowed:
            prop.status = "BLOCKED"
            db.commit()
            tg_answer_callback(cq_id, f"Blocked ❌ ({reason})")
            tg_send(chat_id, f"⛔ Blocked: <code>{pid}</code>\nReason: {reason}")
            return {"ok": True}

        ok, msg2 = broker.place_trade(user, prop)
        prop.status = "APPROVED" if ok else "BLOCKED"
        db.commit()

        tg_answer_callback(cq_id, "Approved ✅")
        tg_send(chat_id, f"✅ Approved: <code>{pid}</code>\n{msg2}")
        return {"ok": True}

    if data.startswith("reject:"):
        pid = data.split(":", 1)[1]
        prop = db.query(Proposal).filter(Proposal.id == pid, Proposal.user_id == user.id).first()
        if not prop:
            tg_answer_callback(cq_id, "Proposal not found.")
            return {"ok": True}
        prop.status = "REJECTED"
        db.commit()
        tg_answer_callback(cq_id, "Rejected ❌")
        tg_send(chat_id, f"❌ Rejected: <code>{pid}</code>")
        return {"ok": True}

    tg_answer_callback(cq_id, "Unhandled")
    return {"ok": True}
