#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام برای ارسال خودکار ویدیوهای جدید از کانال‌های یوتیوب
- ارسال عنوان + لینک + تامبنیل
- شامل وب‌سرور Flask برای Render + UptimeRobot
- حلقه ربات در صورت کرش، خودش دوباره راه‌اندازی می‌شود
"""

import time
import json
import os
import threading
import traceback
import feedparser
import telebot
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
SEEN_FILE = "seen_videos.json"
MAX_AGE_HOURS_ON_FIRST_RUN = 6

# =================================================

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# برای مانیتور کردن سلامت حلقه ربات
bot_thread_alive = True
last_bot_activity = time.time()


@app.route("/")
def health_check():
    status = "Bot is running ✅"
    if not bot_thread_alive:
        status = "Bot thread is DOWN ❌"
    return status, 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"خطا در خواندن seen: {e}")
    return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"خطا در ذخیره seen: {e}")


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


def check_new_videos(seen, is_first_run=False):
    new_videos = []
    now = datetime.now(timezone.utc)

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

                if is_first_run and published_dt:
                    age_hours = (now - published_dt).total_seconds() / 3600
                    if age_hours > MAX_AGE_HOURS_ON_FIRST_RUN:
                        seen.add(video_id)
                        continue

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
    """حلقه اصلی ربات - در صورت کرش دوباره از اینجا شروع می‌شود"""
    global bot_thread_alive, last_bot_activity

    while True:  # حلقه بیرونی برای ریستارت خودکار
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

            seen = load_seen()
            is_first_run = len(seen) == 0
            print(f"تعداد ویدیوهای ذخیره‌شده قبلی: {len(seen)}")

            if is_first_run:
                print(f"⚠️ اولین اجرا. فقط ویدیوهای کمتر از {MAX_AGE_HOURS_ON_FIRST_RUN} ساعت اخیر ارسال می‌شوند.")

            # چک اولیه
            new_videos = check_new_videos(seen, is_first_run=is_first_run)
            save_seen(seen)

            if new_videos:
                print(f"در چک اولیه {len(new_videos)} ویدیوی جدید پیدا شد.")
                for video in reversed(new_videos):
                    if send_to_telegram(video):
                        seen.add(video["id"])
                        save_seen(seen)
                    time.sleep(3)
            else:
                print("ویدیوی جدیدی برای ارسال وجود ندارد.")

            # حلقه اصلی چک کردن
            while True:
                last_bot_activity = time.time()
                time.sleep(CHECK_INTERVAL)

                print(f"\n[{datetime.now()}] در حال چک کردن ویدیوهای جدید...")
                new_videos = check_new_videos(seen, is_first_run=False)

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
    # ربات را در ترد جداگانه اجرا کن
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

    # وب‌سرور را در ترد اصلی نگه دار
    run_flask()


if __name__ == "__main__":
    main()
