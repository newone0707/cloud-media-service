import asyncio
import os
import sys
import subprocess
from playwright.async_api import async_playwright
import yt_dlp

# Credentials
EMAIL = "pawarkapil629@gmail.com"
PASSWORD = "Mh181101"

# Target platform
LOGIN_URL = "https://www.ganitank.com/t/public/login"
COURSES_URL = "https://www.ganitank.com/s/mycourses"

# Download Directory
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

async def login(page):
    print("Navigating to login page...")
    await page.goto(LOGIN_URL)
    print("Please monitor the browser window. If an OTP is required, please enter it manually.")
    
    try:
        if await page.locator("input[type='email']").is_visible(timeout=5000):
            await page.fill("input[type='email']", EMAIL)
            if await page.locator("input[type='password']").is_visible():
                await page.fill("input[type='password']", PASSWORD)
            await page.click("button[type='submit']")
    except Exception as e:
        print("Could not auto-fill email/password. Please login manually.", e)

    try:
        await page.wait_for_url("**/s/mycourses**", timeout=60000)
        print("Successfully logged in!")
    except Exception as e:
        print("Did not detect redirect to mycourses within 60s. Checking current URL...")
        print(f"Current URL: {page.url}")

async def extract_courses(page):
    await page.goto(COURSES_URL)
    await page.wait_for_load_state("networkidle")
    print("Extracting courses...")
    course_links = await page.locator("a[href*='/s/store/courses/']").evaluate_all(
        "elements => elements.map(e => e.href)"
    )
    course_links = list(set(course_links))
    print(f"Found {len(course_links)} courses: {course_links}")
    return course_links

async def extract_videos_from_course(page, course_url):
    print(f"Opening course: {course_url}")
    await page.goto(course_url)
    await page.wait_for_load_state("networkidle")
    
    video_links = []
    
    async def handle_response(response):
        if ".m3u8" in response.url:
            print(f"Found m3u8 stream: {response.url}")
            video_links.append(response.url)

    page.on("response", handle_response)
    print("Listening for video streams. Please click through lessons manually to detect streams...")
    await page.wait_for_timeout(30000)
    page.remove_listener("response", handle_response)
    return list(set(video_links))

def download_video(m3u8_url, output_filename):
    filepath = os.path.join(DOWNLOAD_DIR, output_filename)
    print(f"Downloading {m3u8_url} to {filepath}...")
    ydl_opts = {
        'outtmpl': filepath,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4'
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([m3u8_url])
        print("Download complete.")
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False

async def main():
    print("Starting Ganitank Extractor Bot...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        await login(page)
        courses = await extract_courses(page)
        
        if courses:
            first_course = courses[0]
            m3u8_urls = await extract_videos_from_course(page, first_course)
            
            for idx, url in enumerate(m3u8_urls):
                filename = f"downloaded_video_{idx}.mp4"
                download_video(url, filename)
        
        print("Closing browser...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
