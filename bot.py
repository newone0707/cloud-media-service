import os
import re
import asyncio
import threading
import telebot
from playwright.async_api import async_playwright

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

user_sessions = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    user_sessions[user_id] = {"state": "WAITING_URL"}
    bot.send_message(message.chat.id, "Welcome to the Spayee/Graphy Auto-Extractor!\n\nPlease send me the **Base URL** of the website (e.g., https://www.ganitank.com)", parse_mode="Markdown")

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_URL")
def handle_url(message):
    user_id = message.from_user.id
    url = message.text.strip()
    if not url.startswith("http"):
        url = "https://" + url
    
    user_sessions[user_id]["url"] = url
    user_sessions[user_id]["state"] = "WAITING_CREDS"
    bot.send_message(message.chat.id, f"URL saved: {url}\n\nNow, please send your login credentials in this format:\n`email*password`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_CREDS")
def handle_creds(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if "*" not in text:
        bot.send_message(message.chat.id, "Invalid format. Please use `email*password`.", parse_mode="Markdown")
        return
        
    email, password = text.split("*", 1)
    user_sessions[user_id]["email"] = email
    user_sessions[user_id]["password"] = password
    user_sessions[user_id]["state"] = "EXTRACTING"
    
    # We use a simple boolean and loop instead of asyncio.Event across threads
    user_sessions[user_id]["otp_provided"] = False
    user_sessions[user_id]["otp_code"] = ""
    user_sessions[user_id]["links"] = []
    
    bot.send_message(message.chat.id, "Credentials saved! Starting the browser in the background. Please wait...")
    
    # Start the async Playwright task in a new thread
    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_extraction(message.chat.id, user_id))
        loop.close()

    threading.Thread(target=run_in_thread).start()

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_OTP")
def handle_otp(message):
    user_id = message.from_user.id
    otp = message.text.strip()
    user_sessions[user_id]["otp_code"] = otp
    user_sessions[user_id]["otp_provided"] = True
    user_sessions[user_id]["state"] = "EXTRACTING"
    bot.send_message(message.chat.id, "OTP received! Submitting...")

async def run_extraction(chat_id, user_id):
    session = user_sessions[user_id]
    base_url = session["url"]
    email = session["email"]
    password = session["password"]
    
    bot.send_message(chat_id, "Launching headless browser...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context()
            page = await context.new_page()
            
            extracted_links = []
            
            async def handle_request(request):
                url = request.url
                if ".m3u8" in url or ".pdf" in url:
                    if url not in extracted_links:
                        extracted_links.append(url)
            
            page.on("request", handle_request)
            
            login_url = f"{base_url}/s/authenticate" if not base_url.endswith("/") else f"{base_url}s/authenticate"
            bot.send_message(chat_id, f"Navigating to {login_url}...")
            await page.goto(login_url, wait_until="networkidle")
            
            bot.send_message(chat_id, "Filling credentials...")
            try:
                await page.fill('input[type="email"], input[name="email"], #email', email, timeout=5000)
                await page.fill('input[type="password"], input[name="password"], #password', password, timeout=5000)
                await page.click('button[type="submit"], #login-btn, .login-btn', timeout=5000)
            except Exception as e:
                bot.send_message(chat_id, f"Could not find standard login fields. Proceeding anyway...")
            
            await asyncio.sleep(3)
            page_text = await page.content()
            if "OTP" in page_text or "Verification Code" in page_text or await page.locator('input[name="otp"]').count() > 0:
                bot.send_message(chat_id, "⚠️ **OTP REQUIRED!** ⚠️\n\nPlease check your email/phone and send the OTP here.", parse_mode="Markdown")
                session["state"] = "WAITING_OTP"
                session["otp_provided"] = False
                
                # Wait for user to provide OTP via Telegram (max 5 minutes)
                wait_time = 0
                while not session["otp_provided"] and wait_time < 300:
                    await asyncio.sleep(2)
                    wait_time += 2
                    
                if not session["otp_provided"]:
                    bot.send_message(chat_id, "OTP Timeout. Extraction aborted.")
                    await browser.close()
                    return
                
                otp_code = session["otp_code"]
                try:
                    inputs = await page.locator('input[type="text"]').all()
                    if len(inputs) == 4 or len(inputs) == 6:
                        for i, char in enumerate(otp_code):
                            await inputs[i].fill(char)
                    else:
                        await page.fill('input[name="otp"], input[placeholder*="OTP"]', otp_code)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                except Exception as e:
                    bot.send_message(chat_id, "Failed to enter OTP automatically.")
            
            await page.goto(f"{base_url}/s/mycourses", wait_until="networkidle")
            if "mycourses" not in page.url and "dashboard" not in page.url.lower():
                bot.send_message(chat_id, "❌ Login failed! We didn't reach the My Courses page.")
                await browser.close()
                return
                
            bot.send_message(chat_id, "✅ Login successful! Navigating to courses to extract links... This might take a while.")
            
            await asyncio.sleep(10)
            
            if not extracted_links:
                bot.send_message(chat_id, "Extraction finished but no .m3u8 or .pdf links were caught.")
            else:
                filename = f"extracted_links_{user_id}.txt"
                with open(filename, "w") as f:
                    for link in extracted_links:
                        f.write(link + "\n")
                        
                with open(filename, "rb") as f:
                    bot.send_document(chat_id, f, caption="🎉 Extraction Complete! Here are your links.")
                os.remove(filename)

            await browser.close()
            session["state"] = "WAITING_URL"

    except Exception as e:
        bot.send_message(chat_id, f"❌ A fatal error occurred during extraction:\n{e}")
        session["state"] = "WAITING_URL"

if __name__ == "__main__":
    print("Starting Synchronous Extractor Bot to avoid Heroku timeouts...")
    bot.polling(none_stop=True)
