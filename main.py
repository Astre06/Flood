import asyncio
import os
import re
import tempfile
from config import TELEGRAM_BOT_TOKENS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.helpers import escape_markdown
from config import MAX_WORKERS, DEFAULT_API_URL
from auth_processor import generate_uuids, prepare_headers, check_card_across_sites
import proxy  # proxy.py for proxy management
from bininfo import round_robin_bin_lookup
from manual_check import chk  # Manual single card check handler
from mass_check import handle_file  # Batch check handler (has status keyboard)
from telegram.error import RetryAfter  # Flood control exception

SITE_STORAGE_FILE = "current_site.txt"
PROXY_ADD_STATE = "proxy_add_state"
PROXY_MSG_IDS_KEY = "proxymsgids"  # Fixed key to match your usage

def save_current_site(urls):
    with open(SITE_STORAGE_FILE, "w", encoding="utf-8") as f:
        for url in urls:
            f.write(url.strip() + "\n")

def load_current_site():
    try:
        with open(SITE_STORAGE_FILE, "r", encoding="utf-8") as f:
            sites = [line.strip() for line in f if line.strip()]
            return sites if sites else [DEFAULT_API_URL]
    except FileNotFoundError:
        return [DEFAULT_API_URL]

def append_proxy_message(context, message):
    msg_ids = context.user_data.setdefault(PROXY_MSG_IDS_KEY, [])
    msg_ids.append(message.message_id)

async def safe_send_message(bot, chat_id, text, reply_markup=None):
    try:
        message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        await asyncio.sleep(1)  # Delay to avoid hitting rate limits
        return message
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await safe_send_message(bot, chat_id, text, reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Send me a .txt file with one card per line in the format:\n"
        "`card|month|year|cvc`\n"
        "Example:\n"
        "`4242424242424242|12|2025|123`"
    )
    await safe_send_message(context.bot, update.effective_chat.id, msg)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "replace_site":
        context.user_data["awaiting_site"] = True
        context.user_data["site_buffer"] = []
        try:
            edited_msg = await query.edit_message_text(
                "Please send site URLs (one or more). You can send multiple messages. When done, click Done.",
            )
            context.user_data["site_prompt_msg_id"] = edited_msg.message_id
            context.user_data["site_prompt_chat_id"] = edited_msg.chat_id
        except Exception:
            sent_msg = await safe_send_message(context.bot, update.effective_chat.id,
                "Please send site URLs (one or more). You can send multiple messages. When done, click Done.")
            context.user_data["site_prompt_msg_id"] = sent_msg.message_id
            context.user_data["site_prompt_chat_id"] = sent_msg.chat_id
        return
    elif data == "done_sites":
        sites_to_save = context.user_data.get("site_buffer", [])
        if sites_to_save:
            save_current_site(sites_to_save)
            saved_msg = await safe_send_message(context.bot, update.effective_chat.id, "Site(s) saved successfully.")
        else:
            saved_msg = await safe_send_message(context.bot, update.effective_chat.id, "No sites to save.")
        # Cleanup site prompt messages
        try:
            chat_id = context.user_data.get("site_prompt_chat_id")
            msg_id = context.user_data.get("site_prompt_msg_id")
            if chat_id and msg_id:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            context.user_data.pop("site_prompt_chat_id", None)
            context.user_data.pop("site_prompt_msg_id", None)
        except Exception:
            pass
        await asyncio.sleep(1)
        try:
            await saved_msg.delete()
        except Exception:
            pass
        context.user_data["awaiting_site"] = False
        context.user_data["site_buffer"] = []
        return await cleanup_tracked_messages(context, update.effective_chat.id)
    elif data == "finish_site":
        await query.answer("Site management finished.")
        try:
            await query.message.delete()
        except Exception:
            pass
        return await cleanup_tracked_messages(context, update.effective_chat.id)
    # Proxy buttons handling as before...
    elif data == "proxy_add":
        context.user_data[PROXY_ADD_STATE] = True
        try:
            await query.edit_message_text(
                "Please send a `.txt` file containing proxies in the format: IP:PORT:USERNAME:PASSWORD",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back", callback_data="proxy_back")],
                    [InlineKeyboardButton("Done", callback_data="proxy_done")],
                ]),
            )
        except Exception:
            await safe_send_message(context.bot, update.effective_chat.id,
                "Please send a `.txt` file containing proxies in the format: IP:PORT:USERNAME:PASSWORD")
    elif data == "proxy_back":
        context.user_data[PROXY_ADD_STATE] = False
        try:
            await query.edit_message_text(
                "Choose Proxy option:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Add", callback_data="proxy_add")],
                    [InlineKeyboardButton("Del", callback_data="proxy_del")],
                ]),
            )
        except Exception:
            await safe_send_message(context.bot, update.effective_chat.id, "Choose Proxy option:")
    elif data == "proxy_del":
        proxy.delete_proxies()
        del_msg = await safe_send_message(context.bot, update.effective_chat.id, "All proxies have been deleted.")
        append_proxy_message(context, del_msg)
        await asyncio.sleep(1)
        await cleanup_tracked_messages(context, update.effective_chat.id)
        context.user_data[PROXY_ADD_STATE] = False
        return
    elif data == "proxy_done":
        done_msg = await safe_send_message(context.bot, update.effective_chat.id, "Proxy add successfully.")
        append_proxy_message(context, done_msg)
        await asyncio.sleep(1)
        await cleanup_tracked_messages(context, update.effective_chat.id)
        context.user_data[PROXY_ADD_STATE] = False
        return
    else:
        pass  # no-op or other buttons

async def capture_site_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_site"):
        text = update.message.text
        urls = re.findall(r'https?://[^\s]+', text)
        if urls:
            context.user_data.setdefault("site_buffer", []).extend(urls)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Add more", callback_data="replace_site"),
                 InlineKeyboardButton("Done", callback_data="done_sites")]
            ])
            msg = f"Received {len(context.user_data['site_buffer'])} site(s). Send more or click Done when finished."
            sent = await safe_send_message(context.bot, update.effective_chat.id, msg, reply_markup=keyboard)
            append_proxy_message(context, sent)
        else:
            await safe_send_message(context.bot, update.effective_chat.id, "No valid URLs detected. Please try again or click Done if finished.")

async def sitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sites = load_current_site()
    if not sites:
        sent_msg = await safe_send_message(context.bot, update.effective_chat.id, "No sites are currently set.")
    else:
        sites_text = "\n".join([f"{idx + 1}. {site}" for idx, site in enumerate(sites)])
        sent_msg = await safe_send_message(context.bot, update.effective_chat.id, f"Current sites:\n{sites_text}")
    append_proxy_message(context, sent_msg)
    await asyncio.sleep(5)
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=sent_msg.message_id)
    except Exception:
        pass

async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add", callback_data="proxy_add"),
         InlineKeyboardButton("Del", callback_data="proxy_del")]
    ])
    sent = await safe_send_message(context.bot, update.effective_chat.id, "Choose Proxy option:", reply_markup=keyboard)
    append_proxy_message(context, sent)

async def proxy_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # all proxy_callbacks are handled by button_handler

async def handle_other_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_site"):
        await capture_site_message(update, context)

async def handle_proxy_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        warn_msg = await safe_send_message(context.bot, update.effective_chat.id,
                                            "Please upload a .txt file with proxies in the format: IP:PORT:USERNAME:PASSWORD")
        append_proxy_message(context, warn_msg)
        return

    local_path = None
    try:
        file = await doc.get_file()
        local_path = os.path.join(tempfile.gettempdir(), doc.file_name)
        await file.download_to_drive(local_path)
        with open(local_path, "r") as f:
            proxy_lines = [line.strip() for line in f if line.strip()]
        if not proxy_lines:
            empty_msg = await safe_send_message(context.bot, update.effective_chat.id, "The uploaded file is empty.")
            append_proxy_message(context, empty_msg)
            return
        proxy.add_proxies(proxy_lines)
        succ_msg = await safe_send_message(context.bot, update.effective_chat.id, f"Successfully added {len(proxy_lines)} proxies.")
        append_proxy_message(context, succ_msg)
    except Exception as e:
        fail_msg = await safe_send_message(context.bot, update.effective_chat.id,
                                           f"Failed to process the uploaded file: {e}")
        append_proxy_message(context, fail_msg)
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception as e:
                print(f"Failed to delete file {local_path}: {e}")

async def handle_file_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get(PROXY_ADD_STATE, False):
        await handle_proxy_file_upload(update, context)
    else:
        await handle_file(update, context)  # This is now only in mass_check.py

async def site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Replace", callback_data="replace_site"),
         InlineKeyboardButton("Done", callback_data="done_sites")]
    ])
    sent = await safe_send_message(context.bot, update.effective_chat.id, "Choose an option:", reply_markup=keyboard)
    append_proxy_message(context, sent)

async def cleanup_tracked_messages(context, chat_id):
    msg_ids = context.user_data.get(PROXY_MSG_IDS_KEY, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    context.user_data[PROXY_MSG_IDS_KEY] = []

class BotTokenManager:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    def get_current_token(self):
        return self.tokens[self.index]

    def rotate_token(self):
        self.index = (self.index + 1) % len(self.tokens)
        return self.get_current_token()

bot_token_manager = BotTokenManager(TELEGRAM_BOT_TOKENS)

def create_bot_application_with_rotation():
    for _ in range(len(TELEGRAM_BOT_TOKENS)):
        token = bot_token_manager.get_current_token()
        try:
            app = Application.builder().token(token).build()
            print(f"Bot started with token index: {bot_token_manager.index}")
            return app
        except RetryAfter as e:
            print(f"Flood control hit on token index {bot_token_manager.index}, rotating token...")
            bot_token_manager.rotate_token()
            continue
    raise Exception("All bot tokens are rate limited or blocked. Please wait before retrying.")

def main():
    app = create_bot_application_with_rotation()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("site", site))
    app.add_handler(CommandHandler("sitelist", sitelist))
    app.add_handler(CommandHandler("proxy", proxy_command))
    app.add_handler(CommandHandler("chk", chk))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_other_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_wrapper))
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
