import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

def tg_send(chat_id: str, text: str, reply_markup=None):
    if not BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload, timeout=10)

def tg_answer_callback(callback_query_id: str, text: str):
    if not BOT_TOKEN or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    requests.post(
        url,
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=10,
    )
