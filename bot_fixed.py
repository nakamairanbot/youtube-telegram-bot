#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام برای ارسال خودکار ویدیوهای جدید از کانال‌های یوتیوب
نسخه ۲ اصلاح‌شده:
- تمام درخواست‌های شبکه (تلگرام + jsonbin) timeout صریح دارن، پس هیچ‌جا گیر نمی‌کنه
- چک اتصال/ادمین بودن با درخواست خام requests (نه از طریق telebot) تا قابل کنترل‌تر باشه
- مسیر تشخیصی /debug برای چک سریع سلامت اتصال‌ها بدون نیاز به لاگ
- لاگ بدون بافر (flush=True)
- retry برای ذخیره‌سازی seen
- پرچم SEND_ON_FIRST_RUN
- self-ping برای جلوگیری از خواب رفتن سرویس رایگان Render
"""

import builtins
import functools
import os
import threading
import time
import traceback
from datetime import datetime

import feedparser
import requests
import telebot
from dateutil import parser as date_parser
from flask import Flask, jsonify

# ==================== لاگ بدون بافر ====================
builtins.print = functools.partial(print, flush=True)

# ==================== تنظیمات (از Environment Variables) ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@FilmVidReaction").strip()

JSONBIN_ID = os.environ.get("JSONBIN_ID", "").strip()
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "").strip()
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}" if JSONBIN_ID else ""

SEND_ON_FIRST_RUN = os.environ.get("SEND_ON_FIRST_RUN", "false").lower() == "true"
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
NET_TIMEOUT = 10  # ثانیه - هیچ درخواستی بیشتر از این منتظر نمی‌مونه

YOUTUBE_CHANNELS = {
    "FilmVidReaction": "UC5n9MslnBrfE47nk3HZvz4Q",
    "RAMRCTV": "UC6I2PhiFlgeg9YkctOo8-SQ",
    "AnimeVidReaction": "UC34UC6T2pd7BX7J7UfW2jyA",
    "ALIRAMDISH": "UCtepKE5h2LbYQLngBdCEZeQ",
}

CHECK_INTERVAL = 300
MAX_SEEN_IDS = 300


def validate_config():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not JSONBIN_ID:
        missing.append("JSONBIN_ID")
    if not JSONBIN_KEY:
        missing.append("JSONBIN_KEY")
    if missing:
        print(f"❌ متغیرهای محیطی زیر تنظیم نشدن: {', '.join(missing)}")
        raise SystemExit(1)


# telebot فقط برای ارسال واقعی پیام استفاده می‌شه؛ برای چک‌های اولیه از requests خام استفاده می‌کنیم
bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None
try:
    telebot.apihelper.CONNECT_TIMEOUT = NET_TIMEOUT
    telebot.apihelper.READ_TIMEOUT = NET_TIMEOUT + 10
except Exception:
    pass

app = Flask(__name__)
bot_thread_alive = True
last_error = None


@app.route("/")
def health_check():
    status = "Bot is running ✅" if bot_thread_alive else "Bot thread is DOWN ❌"
    return status, 200


@app.route("/health")
def health():
    return "OK", 200


@app.route("/debug")
def debug():
    """چک سریع سلامت اتصال‌ها - برای دیدن مستقیم از مرورگر، بدون نیاز به لاگ."""
    result = {"telegram": None, "jsonbin": None, "bot_thread_alive": bot_thread_alive, "last_error": last_error}

    try:
        r = requests.get(f"{TELEGRAM_API}/getMe", timeout=NET_TIMEOUT)
        data = r.json()
        if data.get("ok"):
            username = data["result"].get("username")
            bot_id = data["result"]["id"]
            try:
                member = requests.get(
                    f"{TELEGRAM_API}/getChatMember",
                    params={"chat_id": TELEGRAM_CHANNEL, "user_id": bot_id},
                    timeout=NET_TIMEOUT,
                ).json()
                if member.get("ok"):
                    status = member["result"]["status"]
                    result["telegram"] = {
                        "ok": True,
                        "username": username,
                        "bot_status_in_channel": status,
                        "can_send": status in ("administrator", "creator"),
                        "channel": TELEGRAM_CHANNEL,
                    }
                else:
                    result["telegram"] = {"ok": True, "username": username, "member_check_error": member}
            except Exception as e:
                result["telegram"] = {"ok": True, "username": username, "member_check_error": str(e)}
        else:
            result["telegram"] = {"ok": False, "response": data}
    except requests.exceptions.Timeout:
        result["telegram"] = {"ok": False, "error": "timeout - اتصال به api.telegram.org برقرار نشد"}
    except Exception as e:
        result["telegram"] = {"ok": False, "error": str(e)}

    try:
        r = requests.get(
            f"{JSONBIN_URL}/latest",
            headers={"X-Master-Key": JSONBIN_KEY},
            timeout=NET_TIMEOUT,
        )
        if r.status_code == 200:
            record = r.json().get("record", {})
            seen = record.get("seen", record.get("seen_videos", [])) if isinstance(record, dict) else record
            result["jsonbin"] = {"ok": True, "seen_count": len(seen)}
        else:
            result["jsonbin"] = {"ok": False, "status": r.status_code, "body": r.text[:200]}
    except requests.exceptions.Timeout:
        result["jsonbin"] = {"ok": False, "error": "timeout - اتصال به jsonbin.io برقرار نشد"}
    except Exception as e:
        result["jsonbin"] = {"ok": False, "error": str(e)}

    return jsonify(result)


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def self_ping_loop():
    if not RENDER_EXTERNAL_URL:
        return
    url = RENDER_EXTERNAL_URL.rstrip("/") + "/health"
    while True:
        time.sleep(600)
        try:
            requests.get(url, timeout=NET_TIMEOUT)
            print(f"[{datetime.now()}] 🔁 self-ping انجام شد.")
        except Exception as e:
            print(f"[{datetime.now()}] ⚠️ self-ping ناموفق: {e}")


def check_bot_channel_access():
    """چک اتصال و ادمین بودن با درخواست خام و timeout صریح - هیچ‌وقت گیر نمی‌کنه."""
    global last_error
    try:
        r = requests.get(f"{TELEGRAM_API}/getMe", timeout=NET_TIMEOUT)
        data = r.json()
        if not data.get("ok"):
            last_error = f"getMe ناموفق: {data}"
            print(f"❌ توکن نامعتبره یا خطای API: {data}")
            return False
        me = data["result"]
        print(f"✅ ربات متصل شد: @{me.get('username')}")
    except requests.exceptions.Timeout:
        last_error = "timeout در getMe"
        print("❌ تایم‌اوت در اتصال به api.telegram.org - ممکنه شبکه Render به تلگرام محدود باشه.")
        return False
    except Exception as e:
        last_error = str(e)
        print(f"❌ توکن ربات نامعتبره یا اتصال به تلگرام برقرار نشد: {e}")
        return False

    try:
        r = requests.get(
            f"{TELEGRAM_API}/getChatMember",
            params={"chat_id": TELEGRAM_CHANNEL, "user_id": me["id"]},
            timeout=NET_TIMEOUT,
        )
        data = r.json()
        if not data.get("ok"):
            # اگه این متد هم به هر دلیلی جواب نداد (مثلاً کانال خیلی بزرگه)،
            # به‌جای گیر کردن تو حلقه، اجازه می‌دیم ادامه بده و تلاش برای ارسال واقعی رو امتحان کنه.
            last_error = f"getChatMember ناموفق: {data}"
            print(f"⚠️ نتونستم وضعیت ربات تو {TELEGRAM_CHANNEL} رو چک کنم: {data} - با این‌حال ادامه می‌دم.")
            return True
        status = data["result"]["status"]
        if status not in ("administrator", "creator"):
            last_error = f"ربات ادمین کانال نیست (status={status})"
            print(f"❌ ربات @{me.get('username')} توی کانال {TELEGRAM_CHANNEL} ادمین نیست (status={status}).")
            return False
        print(f"✅ ربات در کانال {TELEGRAM_CHANNEL} ادمینه (status={status}).")
        last_error = None
        return True
    except requests.exceptions.Timeout:
        last_error = "timeout در getChatMember"
        print("⚠️ تایم‌اوت در چک وضعیت ربات - با این‌حال ادامه می‌دم.")
        return True
    except Exception as e:
        last_error = str(e)
        print(f"⚠️ خطا در چک وضعیت ربات: {e} - با این‌حال ادامه می‌دم.")
        return True


def load_seen():
    try:
        headers = {"X-Master-Key": JSONBIN_KEY}
        r = requests.get(f"{JSONBIN_URL}/latest", headers=headers, timeout=NET_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            record = data.get("record", {})
            if isinstance(record, list):
                seen = set(record)
            elif isinstance(record, dict):
                seen = set(record.get("seen", record.get("seen_videos", [])))
            else:
                seen = set()
            print(f"✅ لیست seen از jsonbin لود شد ({len(seen)} مورد)")
            return seen
        else:
            print(f"⚠️ خطا در خواندن jsonbin: {r.status_code} - {r.text[:200]}")
    except requests.exceptions.Timeout:
        print("⚠️ تایم‌اوت در خواندن jsonbin.")
    except Exception as e:
        print(f"⚠️ خطا در اتصال به jsonbin (خواندن): {e}")
    return set()


def save_seen(seen, retries=3):
    seen_list = list(seen)[-MAX_SEEN_IDS:]
    payload = {"seen": seen_list}
    headers = {"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"}
    for attempt in range(1, retries + 1):
        try:
            r = requests.put(JSONBIN_URL, json=payload, headers=headers, timeout=NET_TIMEOUT)
            if r.status_code in (200, 201):
                print(f"💾 لیست seen ذخیره شد ({len(seen_list)} مورد)")
                return True
            print(f"⚠️ تلاش {attempt}/{retries} برای ذخیره jsonbin ناموفق: {r.status_code} - {r.text[:200]}")
        except requests.exceptions.Timeout:
            print(f"⚠️ تلاش {attempt}/{retries} - تایم‌اوت در ذخیره jsonbin.")
        except Exception as e:
            print(f"⚠️ تلاش {attempt}/{retries} - خطا در اتصال به jsonbin (ذخیره): {e}")
        time.sleep(2)
    print("❌ ذخیره‌سازی seen بعد از چند تلاش شکست خورد.")
    return False


def get_rss_url(channel_id):
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def parse_published(entry):
    try:
        published = entry.get("published") or entry.get("updated")
        if published:
            return date_parser.parse(published)
    except Exception:
        pass
    return None


def get_current_video_ids():
    ids = set()
    for name, channel_id in YOUTUBE_CHANNELS.items():
        try:
            feed = feedparser.parse(get_rss_url(channel_id))
            for entry in feed.entries[:10]:
                video_id = getattr(entry, "yt_videoid", None) or entry.id.split(":")[-1]
                ids.add(video_id)
        except Exception as e:
            print(f"خطا در گرفتن IDهای {name}: {e}")
    return ids


def check_new_videos(seen):
    new_videos = []
    for name, channel_id in YOUTUBE_CHANNELS.items():
        try:
            feed = feedparser.parse(get_rss_url(channel_id))
            if not feed.entries:
                print(f"⚠️ فید {name} خالی برگشت یا پارس نشد.")
            for entry in feed.entries[:8]:
                video_id = getattr(entry, "yt_videoid", None) or entry.id.split(":")[-1]
                if video_id in seen:
                    continue
                title = entry.title
                link = entry.link
                published_dt = parse_published(entry)
                thumbnail = None
                if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                    thumbnail = entry.media_thumbnail[0]["url"]
                    thumbnail = thumbnail.replace("hqdefault.jpg", "maxresdefault.jpg")
                new_videos.append({
                    "id": video_id,
                    "title": title,
                    "link": link,
                    "channel": name,
                    "published": published_dt.isoformat() if published_dt else "",
                    "thumbnail": thumbnail,
                })
        except Exception as e:
            print(f"[{datetime.now()}] خطا در چک کردن {name}: {e}")
    return new_videos


def send_to_telegram(video):
    caption = (
        f"🎬 <b>{video['title']}</b>\n\n"
        f"📺 کانال: <b>{video['channel']}</b>\n"
        f"🔗 {video['link']}"
    )
    try:
        if video.get("thumbnail"):
            bot.send_photo(
                TELEGRAM_CHANNEL,
                photo=video["thumbnail"],
                caption=caption,
                parse_mode="HTML",
                timeout=NET_TIMEOUT + 10,
            )
        else:
            bot.send_message(
                TELEGRAM_CHANNEL,
                caption,
                parse_mode="HTML",
                disable_web_page_preview=False,
                timeout=NET_TIMEOUT + 10,
            )
        print(f"[{datetime.now()}] ✅ ارسال شد: {video['title'][:60]}...")
        return True
    except Exception as e:
        print(f"[{datetime.now()}] ❌ خطا در ارسال عکس: {e}")
        try:
            bot.send_message(
                TELEGRAM_CHANNEL,
                caption,
                parse_mode="HTML",
                disable_web_page_preview=False,
                timeout=NET_TIMEOUT + 10,
            )
            print(f"[{datetime.now()}] ✅ به صورت متن ارسال شد.")
            return True
        except Exception as e2:
            print(f"[{datetime.now()}] ❌ خطای کامل ارسال (احتمالاً ربات ادمین کانال نیست): {e2}")
            return False


def bot_loop():
    global bot_thread_alive

    while True:
        try:
            bot_thread_alive = True
            print("=" * 60)
            print(f"[{datetime.now()}] 🚀 حلقه ربات شروع شد")
            print(f"کانال مقصد: {TELEGRAM_CHANNEL}")
            print("=" * 60)

            if not check_bot_channel_access():
                print("⏳ به دلیل خطای دسترسی، ۳۰ ثانیه صبر می‌کنم و دوباره چک می‌کنم...")
                time.sleep(30)
                continue

            seen = load_seen()
            print(f"تعداد ویدیوهای ذخیره‌شده: {len(seen)}")

            if len(seen) == 0:
                if SEND_ON_FIRST_RUN:
                    print("⚠️ لیست seen خالیه و SEND_ON_FIRST_RUN فعاله؛ ویدیوهای فعلی هم ارسال می‌شن.")
                    new_videos = check_new_videos(seen)
                    for video in reversed(new_videos):
                        if send_to_telegram(video):
                            seen.add(video["id"])
                            save_seen(seen)
                        time.sleep(3)
                else:
                    print("⚠️ لیست seen خالیه. ویدیوهای فعلی را فقط علامت می‌زنم (ارسال نمی‌کنم)...")
                    current_ids = get_current_video_ids()
                    seen.update(current_ids)
                    save_seen(seen)
                    print(f"✅ {len(current_ids)} ویدیوی فعلی ذخیره شد.")
            else:
                new_videos = check_new_videos(seen)
                if new_videos:
                    print(f"در چک اولیه {len(new_videos)} ویدیوی جدید پیدا شد.")
                    for video in reversed(new_videos):
                        if send_to_telegram(video):
                            seen.add(video["id"])
                            save_seen(seen)
                        time.sleep(3)
                else:
                    print("ویدیوی جدیدی برای ارسال وجود ندارد.")

            while True:
                time.sleep(CHECK_INTERVAL)
                print(f"\n[{datetime.now()}] در حال چک کردن ویدیوهای جدید...")
                new_videos = check_new_videos(seen)
                if new_videos:
                    print(f"🎯 {len(new_videos)} ویدیوی جدید پیدا شد!")
                    for video in reversed(new_videos):
                        if send_to_telegram(video):
                            seen.add(video["id"])
                            save_seen(seen)
                        time.sleep(3)
                else:
                    print("ویدیوی جدیدی نیست.")

        except Exception as e:
            bot_thread_alive = False
            print(f"\n[{datetime.now()}] ❌❌ حلقه ربات کرش کرد:")
            print(traceback.format_exc())
            print("⏳ ۱۰ ثانیه صبر می‌کنم و دوباره راه‌اندازی می‌کنم...")
            time.sleep(10)


def main():
    validate_config()
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    ping_t = threading.Thread(target=self_ping_loop, daemon=True)
    ping_t.start()
    run_flask()


if __name__ == "__main__":
    main()
