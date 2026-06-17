import os
import re
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from bot_state import get_state, set_state, clear_state
from extractors.spayee_api import SpayeeClient

@Client.on_message(filters.text & filters.private, group=3)
async def handle_spayee_messages(client: Client, message: Message):
    user_id = message.from_user.id
    state = get_state(user_id)
    
    if state == "WAITING_FOR_SPAYEE_CREDS":
        text = message.text.strip()
        status_msg = await message.reply_text("⏳ **Connecting to Spayee/Graphy... Please wait (~10s)**")
        
        try:
            url_part, creds_part = text.split(" ", 1)
            email, password = creds_part.split("*", 1)
        except Exception:
            await status_msg.edit_text("❌ Invalid format. Use: `[URL] [EMAIL]*[PASSWORD]` OR `[URL] token*[YOUR_JWT_TOKEN]`")
            return
            
        if not url_part.startswith("http"):
            url_part = "https://" + url_part
            
        clear_state(user_id)
        
        spayee_client = SpayeeClient(url_part, email, password)
        
        courses_resp = await spayee_client.fetch_courses()
        
        if not courses_resp.get("success"):
            await status_msg.edit_text(f"❌ **Login or Fetch Failed:** {courses_resp.get('error')}")
            return
            
        courses = courses_resp.get("courses", [])
        if not courses:
            await status_msg.edit_text("❌ **Logged in, but no courses found on the dashboard.**")
            return
            
        courses_text = ""
        # Create a simple numeric ID for each course since URLs are long
        for i, c in enumerate(courses):
            c["numeric_id"] = str(i + 1)
            courses_text += f"{i + 1}. {c['title']}\n"
            
        platform_name = "SPAYEE"
        if "ganitank" in url_part.lower():
            platform_name = "GANITANK"
            
        success_msg = (
            f"{platform_name} [Graphy] Login Successfull !\n\n"
            f"🔗 API URL: {spayee_client.domain_url}\n\n"
            f"👤 Login Credentials: {email}*{password}\n\n"
            f"{platform_name} - Enrolled Courses\n\n"
            f"{courses_text}\n"
            f"**Reply to this message with the BATCH ID (e.g., 1, 2, 3) you want to extract.**"
        )
        
        if not hasattr(client, "user_sessions"):
            client.user_sessions = {}
            
        client.user_sessions[user_id] = {
            "spayee": spayee_client,
            "courses": courses,
            "app_name": "SPAYEE",
            "base_url": url_part
        }
        
        set_state(user_id, "WAITING_FOR_SPAYEE_COURSE_SELECTION")
        await status_msg.edit_text(success_msg)
        
    elif state == "WAITING_FOR_SPAYEE_COURSE_SELECTION":
        if not hasattr(client, "user_sessions") or user_id not in client.user_sessions:
            await message.reply_text("❌ Session expired. Please login again.")
            clear_state(user_id)
            return
            
        session = client.user_sessions[user_id]
        if session.get("app_name") != "SPAYEE":
            return
            
        spayee_client = session["spayee"]
        courses = session["courses"]
        course_id = message.text.strip()
        
        selected_course = None
        for c in courses:
            if c["numeric_id"] == course_id:
                selected_course = c
                break
                
        if not selected_course:
            await message.reply_text("❌ Invalid Batch ID. Please try again.")
            return
            
        clear_state(user_id)
        status_msg = await message.reply_text(f"⏳ **Extracting links for {selected_course['title']}... Please wait (~20s)**")
        
        import time
        from datetime import datetime
        start_time = time.time()
        
        # This is native async
        links = await spayee_client.extract_links(selected_course["id"])
        end_time = time.time()
        
        if not links:
            await status_msg.edit_text("❌ **No links found in this course.**")
            return
            
        c_title = selected_course["title"]
        c_title_clean = re.sub(r'[\\/*?:"<>| ]+', '_', c_title.strip())
        file_name = f"{c_title_clean}.txt"
        
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(f"Course: {c_title}\n")
            f.write(f"URL: {selected_course['id']}\n\n")
            f.write("\n".join(links))
            
        total_time = end_time - start_time
        mins = int(total_time // 60)
        secs = int(total_time % 60)
        time_str = f"{mins}m {secs}s"
        
        dt_str = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        total_content = spayee_client.total_videos + spayee_client.total_pdfs
        
        caption = (
            f"✅ SPAYEE Extraction Successful!\n\n"
            f"📚 Course Name:  {c_title}\n"
            f"• 🏛️ App Name: SPAYEE\n"
            f"• 🌐 Base URL: {spayee_client.domain_url}\n"
            f"• 📦 Total Content: {total_content} | 🎥 Videos: {spayee_client.total_videos}\n"
            f"• 📄 PDFs: {spayee_client.total_pdfs}\n"
            f"• ⏱️ Total Time Taken: {time_str}\n"
            f"• 📅 Date-Time: {dt_str}"
        )
        
        await client.send_document(
            chat_id=message.chat.id,
            document=file_name,
            caption=caption
        )
        
        await status_msg.delete()
        os.remove(file_name)
