#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام برای ارسال خودکار ویدیوهای جدید از کانال‌های یوتیوب
- ارسال عنوان + لینک + تامبنیل
- ذخیره‌سازی ماندگار لیست ویدیوها با jsonbin.io (دیگر تکراری نمی‌فرستد)
- حلقه ریستارت خودکار در صورت کرش
- وب‌سرور Flask برای Render + UptimeRobot
"""

import time
import json
import os
import threading
import traceback
import feedparser
import telebot
import requests
from datetime import datetime, timezone
from dateutil import parser as date_parser
from flask import Flask

# ==================== تنظیمات ====================
BOT_TOKEN = "8849761551:AAFeQb24l5btt5ruzfMVzG4eupYGccN5l9c"
TELEGRAM_CHANNEL = "@FilmVidReaction"

YOUTUBE_CHANNELS = {
    "FilmVidReaction": "UC5n9MslnBrfE47nk3HZvz4Q",
    "RAMRCTV": "UC6I2PhiFlgeg9YkctOo8-SQ",
    "AnimeVidReaction": "UC34UC6T2pd7BX7J7UfW2jyA",
    "ALIRAMDISH": "UCtepKE5h2LbYQLngBdCEZeQ",
}

CHECK_INTERVAL = 300  # هر ۵ دقیقه

# --- تنظیمات jsonbin.io (ذخیره‌سازی ماندگار) ---
JSONBIN_ID = "6ab4fa99ac6210605af05e47"
JSONBIN_KEY = "$2a$10$hwRQ7ooZi5kcfK7iY.cE4.qlT8.jN1mihqGXoeP.I8e0mQyrr4svy"
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

# حداکثر تعداد ID که نگه می‌داریم (برای جلوگیری از بزرگ شدن بیش از حد)
MAX_SEEN_IDS = 300

# =================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

bot_thread_alive = True


@app.route("/")
def health_check():
    status = "Bot is running ✅" if bot_thread_alive else "Bot thread is DOWN ❌"
    return status, 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def load_seen():
    """خواندن لیست ویدیوهای دیده‌شده از jsonbin"""
    try:
        headers = {
            "X-Master-Key": JSONBIN_KEY
        }
        r = requests.get(f"{JSONBIN_URL}/latest", headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            record = data.get("record", {})
            # پشتیبانی از هر دو فرمت ممکن
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
    except Exception as e:
        print(f"⚠️ خطا در اتصال به jsonbin (خواندن): {e}")
    return set()


def save_seen(seen):
    """ذخیره لیست ویدیوهای دیده‌شده در jsonbin"""
    try:
        # فقط آخرین MAX_SEEN_IDS تا را نگه دار
        seen_list = list(seen)[-MAX_SEEN_IDS:]
        payload = {"seen": seen_list}

        headers = {
            "X-Master-Key": JSONBIN_KEY,
            "Content-Type": "application/json"
        }
        r = requests.put(JSONBIN_URL, json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            print(f"💾 لیست seen ذخیره شد ({len(seen_list)} مورد)")
        else:
            print(f"⚠️ خطا در ذخیره jsonbin: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ خطا در اتصال به jsonbin (ذخیره): {e}")


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


def check_new_videos(seen):
    new_videos = []
    for name, channel_id in YOUTUBE_CHANNELS.items():
        try:
            feed = feedparser.parse(get_rss_url(channel_id))
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
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                TELEGRAM_CHANNEL,
                caption,
                parse_mode="HTML",
                disable_web_page_preview=False
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
                disable_web_page_preview=False
            )
            print(f"[{datetime.now()}] ✅ به صورت متن ارسال شد.")
            return True
        except Exception as e2:
            print(f"[{datetime.now()}] ❌ خطای کامل ارسال: {e2}")
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

            try:
                me = bot.get_me()
                print(f"✅ ربات متصل شد: @{me.username}")
            except Exception as e:
                print(f"❌ خطا در اتصال به تلگرام: {e}")
                time.sleep(30)
                continue

            # لود لیست ماندگار از jsonbin
            seen = load_seen()
            print(f"تعداد ویدیوهای ذخیره‌شده: {len(seen)}")

            # چک اولیه
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

            # حلقه اصلی
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
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    run_flask()


if __name__ == "__main__":
    main()
