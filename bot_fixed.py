#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام برای ارسال خودکار ویدیوهای جدید از کانال‌های یوتیوب
- ارسال عنوان + لینک + تامبنیل
- شامل یک وب‌سرور کوچک (Flask) تا روی رندر به عنوان Web Service سالم بماند
  و UptimeRobot بتواند بهش پینگ بزند
"""

import time
import json
import os
import threading
import feedparser
import telebot
from datetime import datetime
from flask import Flask

# ==================== تنظیمات ====================
BOT_TOKEN = "8849761551:AAFeQb24l5btt5ruzfMVzG4eupYGccN5l9c"
TELEGRAM_CHANNEL = "@FilmVidReaction"   # کانال مقصد

# کانال‌های یوتیوب (نام + channel_id)
YOUTUBE_CHANNELS = {
    "FilmVidReaction": "UC5n9MslnBrfE47nk3HZvz4Q",
    "RAMRCTV": "UC6I2PhiFlgeg9YkctOo8-SQ",
    "AnimeVidReaction": "UC34UC6T2pd7BX7J7UfW2jyA",
    "ALIRAMDISH": "UCtepKE5h2LbYQLngBdCEZeQ",
}

# هر چند ثانیه یکبار چک کند (پیشنهاد: ۳۰۰ = ۵ دقیقه)
CHECK_INTERVAL = 300

# فایل ذخیره ویدیوهای قبلی (تا دوباره ارسال نشوند)
SEEN_FILE = "seen_videos.json"

# =================================================

bot = telebot.TeleBot(BOT_TOKEN)

# ---------------- وب‌سرور سلامت (برای رندر و UptimeRobot) ----------------
app = Flask(__name__)


@app.route("/")
def health_check():
    return "Bot is running", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================================================


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def get_rss_url(channel_id):
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def check_new_videos(seen):
    new_videos = []
    for name, channel_id in YOUTUBE_CHANNELS.items():
        try:
            feed = feedparser.parse(get_rss_url(channel_id))
            for entry in feed.entries[:5]:  # فقط ۵ تای آخر را چک کن
                video_id = entry.yt_videoid if hasattr(entry, "yt_videoid") else entry.id.split(":")[-1]
                if video_id not in seen:
                    title = entry.title
                    link = entry.link
                    published = entry.get("published", "")

                    # گرفتن تامبنیل
                    thumbnail = None
                    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                        thumbnail = entry.media_thumbnail[0]["url"]
                    # نسخه با کیفیت بالاتر (maxres اگر موجود باشد)
                    if thumbnail:
                        thumbnail = thumbnail.replace("hqdefault.jpg", "maxresdefault.jpg")

                    new_videos.append({
                        "id": video_id,
                        "title": title,
                        "link": link,
                        "channel": name,
                        "published": published,
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
        print(f"[{datetime.now()}] ارسال شد: {video['title'][:50]}...")
        return True
    except Exception as e:
        print(f"[{datetime.now()}] خطا در ارسال به تلگرام (عکس): {e}")
        try:
            bot.send_message(
                TELEGRAM_CHANNEL,
                caption,
                parse_mode="HTML",
                disable_web_page_preview=False
            )
            print(f"[{datetime.now()}] به صورت متن ارسال شد.")
            return True
        except Exception as e2:
            print(f"[{datetime.now()}] خطای کامل: {e2}")
            return False


def bot_loop():
    print("=" * 50)
    print("ربات یوتیوب → تلگرام شروع به کار کرد")
    print(f"کانال مقصد: {TELEGRAM_CHANNEL}")
    print(f"تعداد کانال‌های یوتیوب: {len(YOUTUBE_CHANNELS)}")
    print(f"فاصله چک: هر {CHECK_INTERVAL} ثانیه")
    print("=" * 50)

    try:
        me = bot.get_me()
        print(f"ربات متصل شد: @{me.username}")
    except Exception as e:
        print(f"خطا در اتصال به تلگرام: {e}")
        print("توکن را چک کن و مطمئن شو ربات ادمین کانال است.")
        return

    seen = load_seen()
    print(f"تعداد ویدیوهای قبلی ذخیره شده: {len(seen)}")

    print("در حال بارگذاری ویدیوهای فعلی (بدون ارسال)...")
    first_run = check_new_videos(seen)
    for v in first_run:
        seen.add(v["id"])
    save_seen(seen)
    print("ویدیوهای فعلی به عنوان دیده شده ذخیره شدند. از این به بعد فقط ویدیوی جدید ارسال می‌شود.")

    while True:
        try:
            new_videos = check_new_videos(seen)
            for video in reversed(new_videos):
                if send_to_telegram(video):
                    seen.add(video["id"])
                    save_seen(seen)
                time.sleep(2)

            if new_videos:
                print(f"[{datetime.now()}] {len(new_videos)} ویدیوی جدید پیدا و ارسال شد.")
            else:
                print(f"[{datetime.now()}] ویدیوی جدیدی نیست. در حال انتظار...")

        except Exception as e:
            print(f"[{datetime.now()}] خطای کلی: {e}")

        time.sleep(CHECK_INTERVAL)


def main():
    # حلقه‌ی اصلی ربات را در یک ترد جدا اجرا کن
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

    # وب‌سرور را در ترد اصلی اجرا کن تا رندر سرویس را "سالم" ببیند
    run_flask()


if __name__ == "__main__":
    main()
