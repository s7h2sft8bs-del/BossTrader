import os
import time
import uuid
import threading
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
import time
import uuid
import threading
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
# Load env explicitly (avoids dotenv auto-discovery issues)
load_dotenv(os.path.join(os.getcwd(), ".env"))

TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


app = FastAPI()
@app.get("/test-telegram")
def test_telegram():
    try:
        tg_send("✅ BossTrader Telegram test: OK")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
@app.get("/")
def root():
    return {"status": "ok", "message": "BossTrader is running"}

PROPOSALS: Dict[str, Dict[str, Any]] = {}
KILL_SWITCH = {"armed": False, "armed_at": None}

TG_OFFSET = {"value": 0}  # simple in-memory offset for polling


def tg_api(method: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, json=payload, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def tg_send(text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_api("sendMessage", payload=payload)


def tg_edit(chat_id: str, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    tg_api("editMessageText", payload=payload)


def tg_answer_callback(callback_query_id: str, text: str) -> None:
    tg_api("answerCallbackQuery", payload={"callback_query_id": callback_query_id, "text": text})


def keyboard(pid: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{pid}"},
                {"text": "❌ Reject", "callback_data": f"reject:{pid}"},
            ],
            [
                {"text": "🛑 Kill Switch", "callback_data": "kill:on"},
                {"text": "🟢 Unkill", "callback_data": "kill:off"},
            ],
        ]
    }


def handle_callback_query(cq: dict) -> None:
    cq_id = cq["id"]
    data = cq.get("data", "")
    msg = cq.get("message", {})
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    message_id = msg.get("message_id")
    original_text = msg.get("text", "")

    if data.startswith("approve:"):
        pid = data.split(":", 1)[1]
        if pid in PROPOSALS:
            PROPOSALS[pid]["status"] = "APPROVED"
            tg_answer_callback(cq_id, "Approved ✅")
            tg_edit(chat_id, message_id, original_text + "\n\n<b>Status:</b> ✅ APPROVED", reply_markup={"inline_keyboard": []})
        else:
            tg_answer_callback(cq_id, "Unknown proposal")

    elif data.startswith("reject:"):
        pid = data.split(":", 1)[1]
        if pid in PROPOSALS:
            PROPOSALS[pid]["status"] = "REJECTED"
            tg_answer_callback(cq_id, "Rejected ❌")
            tg_edit(chat_id, message_id, original_text + "\n\n<b>Status:</b> ❌ REJECTED", reply_markup={"inline_keyboard": []})
        else:
            tg_answer_callback(cq_id, "Unknown proposal")

    elif data == "kill:on":
        KILL_SWITCH["armed"] = True
        KILL_SWITCH["armed_at"] = time.time()
        tg_answer_callback(cq_id, "Kill switch ARMED 🛑")
        tg_send("🛑 <b>KILL SWITCH ARMED</b>\nNo new trade proposals will be sent.")

    elif data == "kill:off":
        KILL_SWITCH["armed"] = False
        KILL_SWITCH["armed_at"] = None
        tg_answer_callback(cq_id, "Kill switch OFF 🟢")
        tg_send("🟢 <b>KILL SWITCH OFF</b>\nTrade proposals can resume.")

    else:
        tg_answer_callback(cq_id, "Unhandled")


def telegram_poll_loop() -> None:
    # If CHAT_ID or BOT_TOKEN missing, we can't poll
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured. Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return

    # Optional hello once
    try:
        tg_send("✅ <b>BossTrader is ONLINE</b>\nReady for trade proposals.")
    except Exception as e:
        print("Telegram send failed:", e)

    while True:
        try:
            res = tg_api(
                "getUpdates",
                params={"timeout": 10, "offset": TG_OFFSET["value"]},
            )
            if not res.get("ok"):
                time.sleep(2)
                continue

            updates = res.get("result", [])
            for u in updates:
                TG_OFFSET["value"] = u["update_id"] + 1

                if "callback_query" in u:
                    handle_callback_query(u["callback_query"])

        except Exception as e:
            # keep polling even if Telegram blips
            print("poll error:", e)
            time.sleep(2)


@app.on_event("startup")
def start_polling_thread():
    t = threading.Thread(target=telegram_poll_loop, daemon=True)
    t.start()


@app.get("/health")
def health():
    return {"ok": True, "kill_switch": KILL_SWITCH, "proposals": len(PROPOSALS)}


@app.post("/tv-webhook")
async def tv_webhook(request: Request):
    """
    Expected JSON:
    {
      "secret": "...",
      "symbol": "SPY",
      "side": "CALL" | "PUT",
      "timeframe": "1m",
      "reason": "...",
      "entry": 480.25,
      "stop": 479.75,
      "targets": [481.0, 482.0]
    }
    """
    if KILL_SWITCH["armed"]:
        return JSONResponse({"ok": False, "error": "kill_switch_armed"}, status_code=423)

    data = await request.json()
    if data.get("secret") != TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")

    symbol = str(data.get("symbol", "UNKNOWN")).upper()
    side = str(data.get("side", "CALL")).upper()
    timeframe = str(data.get("timeframe", "1m"))
    reason = str(data.get("reason", ""))
    entry = data.get("entry")
    stop = data.get("stop")
    targets = data.get("targets", [])

    pid = uuid.uuid4().hex[:10]
    PROPOSALS[pid] = {
        "id": pid,
        "created_at": time.time(),
        "status": "PENDING",
        "symbol": symbol,
        "side": side,
        "timeframe": timeframe,
        "reason": reason,
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "raw": data,
    }

    text = (
        f"<b>Trade Proposal</b>\n"
        f"<b>{symbol}</b> — <b>{side}</b> ({timeframe})\n"
        f"Entry: <b>{entry}</b>\n"
        f"Stop: <b>{stop}</b>\n"
        f"Targets: <b>{targets}</b>\n\n"
        f"Reason: {reason}\n"
        f"ID: <code>{pid}</code>"
    )

    tg_send(text, reply_markup=keyboard(pid))
    return {"ok": True, "id": pid}
