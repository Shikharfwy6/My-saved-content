"""
Public Channel Saver + Thumbnail Bot (single file)

Kaam:
  1. Aap bot ko ek photo bhejte hain  -> wo thumbnail (cover) save ho jati hai
  2. Aap public channel ka link bhejte hain (single ya range)
  3. Bot har post nikalta hai aur TARGET_CHANNEL me bhejta hai:
       - video          -> aapke saved thumbnail ke saath (bina download/upload)
       - image / file / audio / text -> jaisa ka taisa (thumbnail nahi lagta)

Install:  pip install pyrogram tgcrypto aiohttp
Run:      python public_saver_thumb_bot.py

Zaroori:
  - Bot ko TARGET_CHANNEL me ADMIN banayein (post karne ki permission ke saath)
  - OWNER_IDS me apna Telegram user ID likhein (warna koi bhi bot use karke
    aapke channel me post bhej sakta hai)
"""
import os
import re
import json
import asyncio

# Python 3.12+ par pyrogram import se pehle ek event loop chahiye
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import Message

# ================= YAHAN APNI VALUE LIKHEIN =================
# GitHub par ye value Secrets me rakhein. Local chalane ke liye "..." ki jagah seedha likh sakte hain.
API_ID = int(os.environ.get("API_ID", "1234567"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "123456:ABC-DEF...")

TARGET_CHANNEL = int(os.environ.get("TARGET_CHANNEL", "-1003914383416"))
# apna user ID (@userinfobot se mile ga); kai ho to comma se alag karein: 111,222
OWNER_IDS = [int(x) for x in os.environ.get("OWNER_IDS", "123456789").replace(" ", "").split(",") if x]
# ===========================================================

MAX_RANGE = 10000      # ek link range me maximum posts
DELAY = 1.5          # har post ke beech wait (seconds)
THUMB_FILE = "thumb.json"   # thumbnail ka file_id yahan save hota hai (restart ke baad bhi rahe)

app = Client("public_saver_thumb_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?P<user>[A-Za-z][A-Za-z0-9_]{3,})/(?P<a>\d+)(?:-(?P<b>\d+))?"
)
PRIVATE_RE = re.compile(r"(?:https?://)?t\.me/c/")

RUNNING = set()   # jin users ka task chal raha hai
CANCEL = set()    # jinhone /cancel kiya


# ---------------- thumbnail save / load ----------------
def load_thumb():
    try:
        with open(THUMB_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("file_id")
    except Exception:
        return None


def save_thumb(file_id):
    with open(THUMB_FILE, "w", encoding="utf-8") as f:
        json.dump({"file_id": file_id}, f)


THUMB_ID = load_thumb()
owner_filter = filters.user(OWNER_IDS)


# ---------------- caption / text safai ----------------
CLEAN_CAPTIONS = True   # False karne par caption jaisa ka taisa jayega

_A_TAG = re.compile(r"<a\b[^>]*>.*?</a>", re.I | re.S)                 # "Click here" jaise text-links
_URL = re.compile(r"(?:https?://|www\.|(?:t|telegram)\.me/)[^\s<]+", re.I)  # seedhe links
_MENTION = re.compile(r"(?<![\w&/.])@[A-Za-z0-9_]{3,}")                # @username
_HASHTAG = re.compile(r"(?<![\w&])#[^\s#<>]+")                         # #hashtags (Hindi bhi)
_EMPTY_TAG = re.compile(
    r"<(b|i|u|s|em|strong|code|pre|blockquote|tg-spoiler)\b[^>]*>\s*</\1>", re.I
)


def clean_html(html: str) -> str:
    """@username, #hashtag, links aur text-links (jaise 'Click here') hata deta hai."""
    if not html or not CLEAN_CAPTIONS:
        return html or ""
    text = _A_TAG.sub("", html)
    text = _URL.sub("", text)
    text = _MENTION.sub("", text)
    text = _HASHTAG.sub("", text)
    prev = None
    while prev != text:                       # khali bache tags hatao
        prev = text
        text = _EMPTY_TAG.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------- Bot API se cover ke saath video bhejna ----------------
async def send_video_with_cover(msg: Message, cover_id: str) -> bool:
    """file_id se video wapas bhejta hai naye cover ke saath. Success par True."""
    v = msg.video
    payload = {
        "chat_id": TARGET_CHANNEL,
        "video": v.file_id,
        "cover": cover_id,
        "supports_streaming": True,
    }
    if v.duration:
        payload["duration"] = v.duration
    if v.width:
        payload["width"] = v.width
    if v.height:
        payload["height"] = v.height
    caption = clean_html(msg.caption.html) if msg.caption else ""
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("ok"):
                return True
            if data.get("error_code") == 429:
                wait = data.get("parameters", {}).get("retry_after", 5)
                await asyncio.sleep(wait + 1)
                async with session.post(url, json=payload) as resp2:
                    return (await resp2.json()).get("ok", False)
            print("sendVideo error:", data)
            return False


async def _retry(func):
    try:
        return await func()
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await func()


async def plain_copy(client: Client, msg: Message, username: str) -> bool:
    """post channel me bhejta hai (safai ke baad). Kuch bhejne layak na bache to False."""
    if msg.text:  # sirf text wali post
        text = clean_html(msg.text.html)
        if not text:
            return False
        await _retry(lambda: client.send_message(
            TARGET_CHANNEL, text,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        ))
        return True

    kwargs = {}
    if msg.caption:
        kwargs["caption"] = clean_html(msg.caption.html)   # khali "" ho to caption hat jata hai
        kwargs["parse_mode"] = enums.ParseMode.HTML
    await _retry(lambda: client.copy_message(
        TARGET_CHANNEL, username, msg.id, reply_markup=None, **kwargs
    ))
    return True


# ---------------- commands ----------------
@app.on_message(filters.command("start") & filters.private & owner_filter)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "<b>👋 नमस्ते!</b>\n\n"
        "1️⃣ पहले थंबनेल वाली <b>फ़ोटो</b> मुझे भेजिए (सेव हो जाएगी)\n"
        "2️⃣ फिर पब्लिक चैनल का लिंक भेजिए:\n"
        "<code>https://t.me/channelname/123</code>\n"
        "<code>https://t.me/channelname/123-150</code>\n"
        "या दो लिंक (पहला = शुरू, दूसरा = अंत) अलग-अलग लाइन में\n\n"
        "🎞 वीडियो पर नया थंबनेल लगेगा\n"
        "🖼 फ़ोटो/फ़ाइल/ऑडियो/टेक्स्ट जैसे हैं वैसे ही चैनल में जाएँगे\n\n"
        "/thumb - सेव थंबनेल देखें\n"
        "/delthumb - थंबनेल हटाएँ\n"
        "/cancel - चल रहा टास्क रोकें",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.command("thumb") & filters.private & owner_filter)
async def view_thumb(client: Client, message: Message):
    if not THUMB_ID:
        return await message.reply_text("❌ कोई थंबनेल सेव नहीं है। फ़ोटो भेजिए।")
    await message.reply_photo(THUMB_ID, caption="✅ यह आपका सेव थंबनेल है।")


@app.on_message(filters.command("delthumb") & filters.private & owner_filter)
async def del_thumb(client: Client, message: Message):
    global THUMB_ID
    THUMB_ID = None
    if os.path.exists(THUMB_FILE):
        os.remove(THUMB_FILE)
    await message.reply_text("🗑 थंबनेल हटा दिया गया।")


@app.on_message(filters.command("cancel") & filters.private & owner_filter)
async def cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if uid in RUNNING:
        CANCEL.add(uid)
        await message.reply_text("❌ टास्क रोका जा रहा है...")
    else:
        await message.reply_text("कोई टास्क नहीं चल रहा।")


# आप सीधे बॉट को फ़ोटो भेजेंगे तो वही नया थंबनेल बनेगा
@app.on_message(filters.photo & filters.private & owner_filter)
async def set_thumb(client: Client, message: Message):
    global THUMB_ID
    THUMB_ID = message.photo.file_id
    save_thumb(THUMB_ID)
    await message.reply_text("✅ थंबनेल सेव हो गया। अब चैनल का लिंक भेजिए।")


# ---------------- link handler ----------------
@app.on_message(
    filters.text & filters.private & owner_filter & ~filters.command(["start", "thumb", "delthumb", "cancel"])
)
async def link_handler(client: Client, message: Message):
    uid = message.from_user.id

    if PRIVATE_RE.search(message.text):
        return await message.reply_text("🔒 प्राइवेट लिंक सपोर्टेड नहीं है। सिर्फ़ पब्लिक चैनल का लिंक भेजिए।")

    links = list(LINK_RE.finditer(message.text))
    m = links[0] if links else None
    if not m:
        return await message.reply_text(
            "⚠️ सही लिंक भेजिए, जैसे:\n<code>https://t.me/channelname/123-150</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    if uid in RUNNING:
        return await message.reply_text("⚠️ पहले वाला टास्क चल रहा है। रोकने के लिए /cancel")

    if not THUMB_ID:
        await message.reply_text("ℹ️ थंबनेल सेव नहीं है, इसलिए वीडियो बिना नए थंबनेल के जाएँगे।")

    username = m.group("user")
    start_id = int(m.group("a"))
    end_id = int(m.group("b") or start_id)
    # दो अलग लिंक भेजे हों (पहला = शुरू, दूसरा = अंत) तो भी रेंज मानो
    if len(links) >= 2 and links[1].group("user").lower() == username.lower():
        end_id = int(links[1].group("a"))
    if end_id < start_id:
        start_id, end_id = end_id, start_id
    if end_id - start_id + 1 > MAX_RANGE:
        end_id = start_id + MAX_RANGE - 1
        await message.reply_text(f"ℹ️ एक बार में अधिकतम {MAX_RANGE} पोस्ट। ID {start_id} से {end_id} तक ले रहा हूँ।")

    # सिर्फ़ start_id से end_id तक (दोनों शामिल). डिलीट हुई पोस्ट छोड़ दी जाती हैं, end_id के आगे नहीं बढ़ता।
    RUNNING.add(uid)
    CANCEL.discard(uid)
    videos = others = fallback = failed = skipped = deleted = 0
    found = 0
    msg_id = start_id
    status = await message.reply_text("⏳ शुरू कर रहा हूँ...")

    try:
        for msg_id in range(start_id, end_id + 1):
            if uid in CANCEL:
                break

            try:
                msg = await _retry(lambda: client.get_messages(username, msg_id))
            except Exception as e:
                print("fetch error:", msg_id, e)
                failed += 1
                await asyncio.sleep(DELAY)
                continue

            # डिलीट हुई / खाली पोस्ट: छोड़ो, अगली पर जाओ
            if not msg or msg.empty or msg.service:
                deleted += 1
                await asyncio.sleep(0.3)
                continue

            found += 1
            try:
                if msg.video and THUMB_ID:
                    ok = await send_video_with_cover(msg, THUMB_ID)
                    if ok:
                        videos += 1
                    else:
                        # cover फेल हुआ तो वीडियो पुराने थंबनेल के साथ भेज दो
                        await plain_copy(client, msg, username)
                        fallback += 1
                else:
                    if await plain_copy(client, msg, username):
                        others += 1
                    else:
                        skipped += 1   # टेक्स्ट सफ़ाई के बाद खाली बचा
            except Exception as e:
                print("error:", msg_id, e)
                failed += 1

            if found % 5 == 0:
                try:
                    await status.edit_text(
                        f"⏳ पोस्ट ID {msg_id} / {end_id}\n"
                        f"🎞 नए थंबनेल के साथ: {videos}\n"
                        f"🖼 बाकी (फ़ोटो/फ़ाइल आदि): {others}\n"
                        f"↩️ पुराने थंबनेल के साथ: {fallback}\n"
                        f"⏭ सफ़ाई के बाद खाली (छोड़े): {skipped}\n"
                        f"🗑 डिलीट पोस्ट (छोड़ी): {deleted}\n❌ फेल: {failed}"
                    )
                except Exception:
                    pass
            await asyncio.sleep(DELAY)
    finally:
        RUNNING.discard(uid)
        cancelled = uid in CANCEL
        CANCEL.discard(uid)

    result = (
        f"{'❌ रोका गया' if cancelled else '✅ पूरा हुआ'} (ID {start_id} से {end_id} तक)\n"
        f"📦 मौजूद पोस्ट मिलीं: {found}\n"
        f"🎞 नए थंबनेल के साथ वीडियो: {videos}\n"
        f"🖼 बाकी: {others}\n"
        f"↩️ पुराने थंबनेल के साथ गए: {fallback}\n"
        f"⏭ सफ़ाई के बाद खाली (छोड़े): {skipped}\n"
        f"🗑 डिलीट पोस्ट (छोड़ी): {deleted}\n"
        f"❌ फेल: {failed}"
    )
    await message.reply_text(result)


if __name__ == "__main__":
    app.run()
