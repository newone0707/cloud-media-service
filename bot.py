import os
import re
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
from playwright.async_api import async_playwright

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
UPLOADER_API_URL = os.environ.get("UPLOADER_API_URL", "http://localhost:5000/download")
EMAIL = os.environ.get("SPAYEE_EMAIL", "pawarkapil629@gmail.com")
PASSWORD = os.environ.get("SPAYEE_PASSWORD", "Mh181101")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

user_state = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    btn1 = InlineKeyboardButton("Auto Extract (Playwright)", callback_data="auto_extract")
    btn2 = InlineKeyboardButton("Graphy / Spayee URLs", callback_data="manual_extract")
    markup.add(btn1)
    markup.add(btn2)
    bot.send_message(message.chat.id, "Welcome to Cloud Media Extractor!\nChoose an extraction method:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "auto_extract":
        bot.send_message(call.message.chat.id, "Auto Extract is currently set up for local headless runs. Please ensure OTP can be bypassed.")
        # Trigger Playwright logic here (running asynchronously)
        # Note: In a stateless Heroku worker without an interactive terminal, OTP is hard to input.
        bot.send_message(call.message.chat.id, "Starting auto extraction in background (Check logs)...")
    elif call.data == "manual_extract":
        bot.send_message(call.message.chat.id, "Please send me the message containing the Spayee/Graphy URLs.\nExample:\n(Chp 1) Title : https://...")
        user_state[call.from_user.id] = "waiting_for_urls"

@bot.message_handler(func=lambda message: user_state.get(message.from_user.id) == "waiting_for_urls")
def handle_urls(message):
    text = message.text
    # Regex to capture "Title : URL"
    # Example format: (Chp 1. Solid State) Solid state 2025 pyqs questions : https://d2a5xnk4s7n8a6.cloudfront.net/...
    pattern = r"(.*?)\s*:\s*(https?://[^\s]+)"
    matches = re.findall(pattern, text)
    
    if not matches:
        bot.send_message(message.chat.id, "Could not find any valid URLs in the format 'Title : https://...'. Please try again.")
        return
    
    bot.send_message(message.chat.id, f"Found {len(matches)} links. Sending them to Media Sync Service...")
    
    for title, url in matches:
        title = title.strip()
        payload = {
            "title": title,
            "url": url,
            "chat_id": message.chat.id
        }
        try:
            resp = requests.post(UPLOADER_API_URL, json=payload, timeout=5)
            if resp.status_code == 200:
                bot.send_message(message.chat.id, f"✅ Sent: {title}")
            else:
                bot.send_message(message.chat.id, f"❌ Failed to send: {title} (Status: {resp.status_code})")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error connecting to Uploader Service: {e}")
    
    user_state[message.from_user.id] = None
    bot.send_message(message.chat.id, "All links processed!")

if __name__ == "__main__":
    print("Starting Extractor Bot...")
    bot.polling(none_stop=True)
