from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    tg_chat_id = Column(String, unique=True, index=True, nullable=True)

    # Membership control:
    is_active = Column(Boolean, default=True)
    paid_until = Column(DateTime, nullable=True)  # None = expired / not paid
    plan = Column(String, default="basic")

    # Per-user secret for TradingView webhooks:
    api_key = Column(String, unique=True, index=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(String, primary_key=True)  # uuid string
    user_id = Column(Integer, index=True, nullable=False)

    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    timeframe = Column(String, nullable=True)
    reason = Column(String, nullable=True)

    status = Column(String, default="PENDING")  # PENDING/APPROVED/REJECTED/BLOCKED
    created_at = Column(DateTime, default=datetime.utcnow)
