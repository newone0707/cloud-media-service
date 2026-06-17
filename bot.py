import os
import re
import asyncio
from telebot.async_telebot import AsyncTeleBot
from playwright.async_api import async_playwright

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
bot = AsyncTeleBot(TELEGRAM_BOT_TOKEN)

# Dictionary to hold state for each user
# user_id: { "state": "WAITING_URL", "url": "", "email": "", "password": "", "otp_event": asyncio.Event(), "otp_code": "", "links": [] }
user_sessions = {}

@bot.message_handler(commands=['start'])
async def send_welcome(message):
    user_id = message.from_user.id
    user_sessions[user_id] = {"state": "WAITING_URL"}
    await bot.send_message(message.chat.id, "Welcome to the Spayee/Graphy Auto-Extractor!\n\nPlease send me the **Base URL** of the website (e.g., https://www.ganitank.com)")

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_URL")
async def handle_url(message):
    user_id = message.from_user.id
    url = message.text.strip()
    if not url.startswith("http"):
        url = "https://" + url
    
    user_sessions[user_id]["url"] = url
    user_sessions[user_id]["state"] = "WAITING_CREDS"
    await bot.send_message(message.chat.id, f"URL saved: {url}\n\nNow, please send your login credentials in this format:\n`email*password`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_CREDS")
async def handle_creds(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if "*" not in text:
        await bot.send_message(message.chat.id, "Invalid format. Please use `email*password`.")
        return
        
    email, password = text.split("*", 1)
    user_sessions[user_id]["email"] = email
    user_sessions[user_id]["password"] = password
    user_sessions[user_id]["state"] = "EXTRACTING"
    user_sessions[user_id]["otp_event"] = asyncio.Event()
    user_sessions[user_id]["links"] = []
    
    await bot.send_message(message.chat.id, "Credentials saved! Starting the browser in the background. Please wait...")
    
    # Start the async Playwright task
    asyncio.create_task(run_extraction(message.chat.id, user_id))

@bot.message_handler(func=lambda m: user_sessions.get(m.from_user.id, {}).get("state") == "WAITING_OTP")
async def handle_otp(message):
    user_id = message.from_user.id
    otp = message.text.strip()
    user_sessions[user_id]["otp_code"] = otp
    user_sessions[user_id]["state"] = "EXTRACTING"
    user_sessions[user_id]["otp_event"].set()
    await bot.send_message(message.chat.id, "OTP received! Submitting...")

async def run_extraction(chat_id, user_id):
    session = user_sessions[user_id]
    base_url = session["url"]
    email = session["email"]
    password = session["password"]
    
    await bot.send_message(chat_id, "Launching headless browser...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            # Setup network interception to catch .m3u8 and .pdf
            extracted_links = []
            
            async def handle_request(request):
                url = request.url
                if ".m3u8" in url or ".pdf" in url:
                    if url not in extracted_links:
                        extracted_links.append(url)
            
            page.on("request", handle_request)
            
            # 1. Navigate to login
            login_url = f"{base_url}/s/authenticate" if not base_url.endswith("/") else f"{base_url}s/authenticate"
            await bot.send_message(chat_id, f"Navigating to {login_url}...")
            await page.goto(login_url, wait_until="networkidle")
            
            # 2. Try to login
            await bot.send_message(chat_id, "Filling credentials...")
            try:
                await page.fill('input[type="email"], input[name="email"], #email', email, timeout=5000)
                await page.fill('input[type="password"], input[name="password"], #password', password, timeout=5000)
                await page.click('button[type="submit"], #login-btn, .login-btn', timeout=5000)
            except Exception as e:
                await bot.send_message(chat_id, f"Could not find standard login fields. Error: {str(e)[:100]}")
            
            # 3. Check for OTP
            await asyncio.sleep(3)
            page_text = await page.content()
            if "OTP" in page_text or "Verification Code" in page_text or await page.locator('input[name="otp"]').count() > 0:
                await bot.send_message(chat_id, "⚠️ **OTP REQUIRED!** ⚠️\n\nPlease check your email/phone and send the OTP here.")
                session["state"] = "WAITING_OTP"
                session["otp_event"].clear()
                
                # Wait for user to provide OTP via Telegram
                try:
                    await asyncio.wait_for(session["otp_event"].wait(), timeout=300) # 5 mins timeout
                except asyncio.TimeoutError:
                    await bot.send_message(chat_id, "OTP Timeout. Extraction aborted.")
                    await browser.close()
                    return
                
                # Enter OTP
                otp_code = session["otp_code"]
                try:
                    # Fill OTP fields (Spayee usually has multiple inputs or one)
                    inputs = await page.locator('input[type="text"]').all()
                    if len(inputs) == 4 or len(inputs) == 6:
                        for i, char in enumerate(otp_code):
                            await inputs[i].fill(char)
                    else:
                        await page.fill('input[name="otp"], input[placeholder*="OTP"]', otp_code)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                except Exception as e:
                    await bot.send_message(chat_id, "Failed to enter OTP automatically.")
            
            # 4. Check if login success
            await page.goto(f"{base_url}/s/mycourses", wait_until="networkidle")
            if "mycourses" not in page.url and "dashboard" not in page.url.lower():
                await bot.send_message(chat_id, "❌ Login failed! We didn't reach the My Courses page.")
                await browser.close()
                return
                
            await bot.send_message(chat_id, "✅ Login successful! Navigating to courses to extract links... This might take a while.")
            
            # 5. Extract links (Generic approach: click on all course cards and lessons)
            # Since DOM is dynamic, we inject a generic auto-scroller and clicker or just grab the API responses
            # For this MVP, we will grab the API JSON if it exists
            
            # Simulate waiting and clicking (Simplified for now)
            # Ideally, you'd script `page.click('.course-card')` etc.
            await asyncio.sleep(10) # Let network interceptor catch things
            
            # Save results
            if not extracted_links:
                await bot.send_message(chat_id, "Extraction finished but no .m3u8 or .pdf links were caught. The site structure might require custom clicking logic.")
            else:
                filename = f"extracted_links_{user_id}.txt"
                with open(filename, "w") as f:
                    for link in extracted_links:
                        f.write(link + "\n")
                        
                with open(filename, "rb") as f:
                    await bot.send_document(chat_id, f, caption="🎉 Extraction Complete! Here are your links.")
                os.remove(filename)

            await browser.close()
            session["state"] = "WAITING_URL"

    except Exception as e:
        await bot.send_message(chat_id, f"❌ A fatal error occurred during extraction:\n{e}")
        session["state"] = "WAITING_URL"

import nest_asyncio
nest_asyncio.apply()

if __name__ == "__main__":
    print("Starting Advanced Extractor Bot...")
    asyncio.run(bot.polling(non_stop=True))
