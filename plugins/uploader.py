import os
import re
import time
import asyncio
import concurrent.futures
import yt_dlp
import requests
from curl_cffi import requests as cffi_requests
from pyrogram import Client, filters
from pyrogram.types import Message
from utils import progress_bar, decrypt_file

# Global state
upload_states = {}
user_tokens = {}

# -------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------

def build_referer(base_url: str) -> str:
    """
    Given the raw BaseURL from the txt file (API URL),
    strip the 'api' suffix from the subdomain and return
    the frontend URL with a trailing slash.
    Works for classx.co.in, appx.co.in, akamai.net.in etc.
    """
    base_url = base_url.strip().rstrip('/')
    # Match: protocol://tenantapi.domain OR protocol://tenant.domain
    m = re.match(r'(https?://)([^.]+?)(api)?\.(.+)$', base_url, re.IGNORECASE)
    if m:
        proto   = m.group(1)
        tenant  = m.group(2)
        domain  = m.group(4)
        return f"{proto}{tenant}.{domain}/"
    return base_url + '/'

def is_video_link(url: str) -> bool:
    path = url.split('?')[0].lower()
    return any(path.endswith(ext) for ext in ['.mp4', '.mkv', '.m3u8', '.webm', '.ts'])

def is_pdf_link(url: str) -> bool:
    path = url.split('?')[0].lower()
    return path.endswith('.pdf')

def strip_aes_key(link: str):
    """
    AppX links look like:  url*key:iv  or  url:key
    Returns (clean_url, aes_key_or_None)
    """
    # Pattern: ...Signature=xxxxx*KeyData:IVData
    if '*' in link:
        parts = link.split('*', 1)
        clean = parts[0]
        key = parts[1]
        return clean, key
    # Pattern: ...Signature=xxxxx:ZmVkY...
    if re.search(r'[A-Za-z0-9+/=]{20,}:[A-Za-z0-9+/=]{20,}$', link):
        idx = link.rfind(':')
        maybe_key = link[idx+1:]
        if len(maybe_key) >= 20 and re.match(r'^[A-Za-z0-9+/=]+$', maybe_key):
            return link[:idx], maybe_key
    return link, None


def sync_download_direct(url, output_path, referer):
    """Direct download with curl_cffi Chrome impersonation - works for AppX/ClassX CDN."""
    try:
        h = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer':    referer,
            'Origin':     referer.rstrip('/'),
        }
        r = cffi_requests.get(url, stream=True, headers=h, impersonate='chrome', timeout=120)
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"Direct Download Error: {e}")
        return False


async def download_video(url, output_path, referer, user_id=None):
    """
    Universal video downloader:
    - AppX encrypted MP4/MKV -> direct curl_cffi
    - Classplus HLS -> custom HLS + yt-dlp pipeline
    - YouTube / generic -> yt-dlp
    """
    referer = referer if referer.endswith('/') else referer + '/'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': referer,
        'Origin': referer.rstrip('/'),
        'device-id': '39F093FF35F201D9'
    }

    # -- Classplus token-based HLS ------------------------------
    if user_id and user_id in user_tokens and ('classplusapp' in url or 'testbook.com' in url):
        cp_token = user_tokens[user_id]
        headers_api = {
            'host': 'api.classplusapp.com',
            'x-access-token': cp_token,
            'accept-language': 'EN',
            'api-version': '18',
            'app-version': '1.4.73.2',
            'build-number': '35',
            'connection': 'Keep-Alive',
            'content-type': 'application/json',
            'device-details': 'Xiaomi_Redmi 7_SDK-32',
            'device-id': 'c28d3cb16bbdac01',
            'region': 'IN',
            'user-agent': 'Mobile-Android',
            'accept-encoding': 'gzip'
        }
        try:
            res = requests.get(
                f'https://api.classplusapp.com/cams/uploader/video/jw-signed-url?url={url.split("&")[0]}',
                headers=headers_api
            )
            if res.status_code == 200:
                new_url = res.json().get('data', {}).get('url')
                if new_url:
                    url = new_url
            headers['x-access-token'] = cp_token
        except Exception as e:
            print(f"JW Logic Error: {e}")

    # -- Classplus direct HLS download --------------------------
    if 'classplus' in url and 'token=' in url:
        def sync_classplus_dl():
            try:
                token_match  = re.search(r"token=([^&]+)", url)
                content_id_match = re.search(r"contentId=([^&]+)", url)
                course_id_match  = re.search(r"courseId=([^&]+)", url)
                folder_id_match  = re.search(r"folderId=([^&]+)", url)

                nonlocal url  # noqa
                if token_match and content_id_match and course_id_match and folder_id_match:
                    cp_headers = {
                        'host': 'api.classplusapp.com',
                        'x-access-token': token_match.group(1),
                        'api-version': '29',
                        'app-version': '1.4.65.3',
                        'device-id': '39F093FF35F201D9',
                        'user-agent': 'Mobile-Android'
                    }
                    resp = requests.get(
                        f"https://api.classplusapp.com/v2/course/content/get?"
                        f"courseId={course_id_match.group(1)}&folderId={folder_id_match.group(1)}",
                        headers=cp_headers
                    )
                    if resp.status_code == 200:
                        import urllib.parse
                        for item in resp.json().get("data", {}).get("courseContent", []):
                            if str(item.get("id")) == content_id_match.group(1):
                                fresh_url = item.get("url")
                                fresh_hash = item.get("contentHashId")
                                if fresh_url and fresh_hash:
                                    url = (f"{fresh_url}?contentHashId="
                                           f"{urllib.parse.quote(fresh_hash, safe='')}"
                                           f"&token={token_match.group(1)}")
                                break

                r = requests.get(url, headers=headers)
                r.raise_for_status()
                import urllib.parse
                master_text = r.text
                base_hls = url.split('?')[0].rsplit('/', 1)[0] + '/'
                query = '?' + url.split('?')[1] if '?' in url else ''

                max_bw, best_url = 0, None
                m3u8_lines = master_text.splitlines()
                for j, ln in enumerate(m3u8_lines):
                    if ln.startswith('#EXT-X-STREAM-INF'):
                        bw_m = re.search(r'BANDWIDTH=(\d+)', ln)
                        bw = int(bw_m.group(1)) if bw_m else 0
                        if bw >= max_bw:
                            max_bw = bw
                            best_url = m3u8_lines[j+1].strip()

                if best_url:
                    if not best_url.startswith('http'):
                        best_url = urllib.parse.urljoin(base_hls, best_url)
                    r2 = cffi_requests.get(best_url + query, headers=headers, impersonate='chrome')
                    r2.raise_for_status()
                    sub_text = r2.text
                    base_hls = best_url.split('?')[0].rsplit('/', 1)[0] + '/'
                else:
                    sub_text = master_text

                new_lines = []
                for ln in sub_text.splitlines():
                    if ln.startswith('#EXT-X-KEY'):
                        uri_m = re.search(r'URI="([^"]+)"', ln)
                        if uri_m:
                            uri = uri_m.group(1)
                            abs_uri = urllib.parse.urljoin(base_hls, uri) if not uri.startswith('http') else uri
                            ln = ln.replace(f'URI="{uri}"', f'URI="{abs_uri}{query}"')
                        new_lines.append(ln)
                    elif ln and not ln.startswith('#'):
                        abs_ln = urllib.parse.urljoin(base_hls, ln) if not ln.startswith('http') else ln
                        new_lines.append(abs_ln + query)
                    else:
                        new_lines.append(ln)

                local_m3u8 = output_path + '.m3u8'
                with open(local_m3u8, 'w') as f:
                    f.write('\n'.join(new_lines))

                with yt_dlp.YoutubeDL({'format': 'best', 'outtmpl': output_path, 'quiet': False, 'http_headers': headers}) as ydl:
                    ret = ydl.download([local_m3u8])
                if os.path.exists(local_m3u8):
                    os.remove(local_m3u8)
                return ret == 0
            except Exception as e:
                import traceback
                print(f"Classplus DL Error:\n{traceback.format_exc()}")
                return False
        return await asyncio.to_thread(sync_classplus_dl)

    # -- AppX / ClassX direct encrypted files ------------------
    if is_video_link(url) and ('appx' in url or 'classx' in url or 'akamai' in url or 'encrypted' in url):
        return await asyncio.to_thread(sync_download_direct, url, output_path, referer)

    # -- JWT token-based streaming URL -------------------------
    if 'token=' in url:
        token = url.split('token=')[1].split('&')[0]
        headers['x-access-token'] = token
        headers['api-version'] = '18'
        try:
            import base64, json as _json
            payload = token.split('.')[1]
            padded = payload + '=' * ((4 - len(payload) % 4) % 4)
            jwt_data = _json.loads(base64.b64decode(padded).decode('utf-8'))
            if 'fingerprintId' in jwt_data:
                headers['device-id'] = jwt_data['fingerprintId']
            else:
                headers['User-Agent'] = 'Mobile-Android'
                headers['app-version'] = '1.4.65.3'
        except:
            pass

    # -- Generic fallback: yt-dlp ------------------------------
    ydl_opts = {
        'format': 'best',
        'outtmpl': output_path,
        'quiet': False,
        'no_warnings': False,
        'http_headers': headers
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.download([url]) == 0
    except Exception as e:
        import traceback
        print(f"YT-DLP Error:\n{traceback.format_exc()}")
        return False


def sync_download_pdf(url, output_path, referer):
    """Synchronous PDF download. Handles Classplus token URLs and AppX CDN."""
    try:
        h = {'User-Agent': 'Mozilla/5.0', 'device-id': '39F093FF35F201D9'}

        if 'token=' in url:
            token = url.split('token=')[1].split('&')[0]
            h['x-access-token'] = token
            h['api-version'] = '18'
        elif 'appx' in url or 'classx' in url or 'akamai' in url:
            ref = referer if referer.endswith('/') else referer + '/'
            h['Referer'] = ref
            h['Origin'] = ref.rstrip('/')

        r = cffi_requests.get(url, stream=True, headers=h, impersonate='chrome', timeout=60)
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"PDF Download Error: {e}")
        return False


# -------------------------------------------------------------
# BOT COMMANDS
# -------------------------------------------------------------

@Client.on_message(filters.command("token") & filters.private)
async def token_cmd(client: Client, message: Message):
    parts = message.text.split(" ", 1)
    if len(parts) > 1:
        user_tokens[message.from_user.id] = parts[1].strip()
        await message.reply_text("? Token updated!")
    else:
        await message.reply_text("Usage: /token <your_token>")


@Client.on_message(filters.command("stop") & filters.private)
async def stop_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if uid in upload_states and upload_states[uid].get("is_uploading"):
        upload_states[uid]["stop_requested"] = True
        await message.reply_text("?? **Stop requested! Halting after current file.**")
    else:
        await message.reply_text("? No upload currently running.")


@Client.on_message(filters.command("upload") & filters.private)
async def upload_cmd(client: Client, message: Message):
    uid = message.from_user.id
    parts = message.text.split(" ")
    limit = 0
    if len(parts) > 1:
        if parts[1].isdigit():
            limit = int(parts[1])
        elif parts[1].lower() == "all":
            limit = -1
        else:
            await message.reply_text("Usage: /upload [count] or /upload all")
            return
    else:
        await message.reply_text("Usage: /upload [count] or /upload all")
        return

    upload_states[uid] = {
        "waiting_for_file": True,
        "limit": limit,
        "is_uploading": False,
        "stop_requested": False
    }
    await message.reply_text(
        f"? **Ready! Will upload {limit if limit > 0 else 'ALL'} links.**\n\n"
        "?? Send me the .txt file now."
    )


@Client.on_message(filters.document & filters.private)
async def handle_document(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in upload_states or not upload_states[uid].get("waiting_for_file"):
        return

    doc = message.document
    if not doc.file_name.endswith('.txt'):
        await message.reply_text("? Please send a .txt file.")
        return

    state = upload_states[uid]
    state["waiting_for_file"] = False
    state["is_uploading"] = True
    state["stop_requested"] = False
    limit = state["limit"]

    status_msg = await message.reply_text("? **Parsing your file...**")
    file_data = await message.download(in_memory=True)

    try:
        content = file_data.getvalue().decode("utf-8")
        lines = content.splitlines()
    except Exception as e:
        await status_msg.edit_text(f"? Failed to read file: {e}")
        state["is_uploading"] = False
        return

    # -- Parse the txt -----------------------------------------
    links_to_upload = []
    global_referer = "https://web.classplusapp.com/"

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("Course:"):
            continue
        if line.startswith("BaseURL:"):
            raw_base = line.split("BaseURL:", 1)[1].strip()
            global_referer = build_referer(raw_base)
            continue
        # Skip encrypted Video: blobs (not downloadable directly)
        if re.match(r'^(Home\s*>.*?>\s*)?Video:\s*[A-Za-z0-9+/]{20}', line):
            continue
        # Named links: "path > title: https://..."
        if ": " in line:
            name, link = line.split(": ", 1)
            if link.startswith("http"):
                links_to_upload.append({"name": name.strip(), "link": link.strip()})
        elif line.startswith("http"):
            links_to_upload.append({"name": "Video", "link": line.strip()})

    if limit > 0:
        links_to_upload = links_to_upload[:limit]

    if not links_to_upload:
        await status_msg.edit_text("? No valid links found in the file.")
        state["is_uploading"] = False
        return

    await status_msg.edit_text(
        f"?? **Found {len(links_to_upload)} items. Starting...**\n\n_(Send /stop to halt)_"
    )

    uploaded_count = 0

    for i, item in enumerate(links_to_upload):
        if state["stop_requested"]:
            await message.reply_text("?? **Stopped!**")
            break

        name = item["name"]
        raw_link = item["link"]

        prog_msg = await message.reply_text(
            f"? **[{i+1}/{len(links_to_upload)}]**\n{name}"
        )

        # Strip AES key if present
        link, aes_key = strip_aes_key(raw_link)

        # Detect file type
        file_is_pdf   = is_pdf_link(link)
        file_is_video = is_video_link(link)
        # If neither detected by extension, guess from name
        if not file_is_pdf and not file_is_video:
            if 'pdf' in name.lower():
                file_is_pdf = True
            else:
                file_is_video = True

        # -- PDF ------------------------------------------------
        if file_is_pdf:
            await prog_msg.edit_text(f"? **Downloading PDF:**\n{name}")
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', name)
            pdf_path = f"{safe_name}.pdf"

            success = await asyncio.to_thread(sync_download_pdf, link, pdf_path, global_referer)

            if success and aes_key and os.path.exists(pdf_path):
                await prog_msg.edit_text(f"?? **Decrypting PDF...**\n{name}")
                if not decrypt_file(pdf_path, aes_key):
                    success = False

            if state["stop_requested"]:
                break

            if success and os.path.exists(pdf_path):
                start_time = time.time()
                try:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=pdf_path,
                        caption=f"?? **{name}**",
                        progress=progress_bar,
                        progress_args=(prog_msg, start_time)
                    )
                    await prog_msg.delete()
                    uploaded_count += 1
                except Exception as e:
                    await prog_msg.edit_text(f"? Upload failed:\n{e}")
            else:
                await prog_msg.edit_text(f"? PDF download failed:\n{name}")

            if os.path.exists(pdf_path):
                os.remove(pdf_path)

        # -- VIDEO ----------------------------------------------
        else:
            await prog_msg.edit_text(f"? **Downloading Video:**\n{name}")
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', name)
            # Preserve mkv extension if needed
            ext = '.mkv' if link.split('?')[0].lower().endswith('.mkv') else '.mp4'
            vid_path = f"{safe_name}{ext}"

            success = await download_video(link, vid_path, global_referer, user_id=uid)

            if success and aes_key and os.path.exists(vid_path):
                await prog_msg.edit_text(f"?? **Decrypting...**\n{name}")
                if not decrypt_file(vid_path, aes_key):
                    success = False

            if state["stop_requested"]:
                if os.path.exists(vid_path):
                    os.remove(vid_path)
                break

            if success and os.path.exists(vid_path):
                start_time = time.time()
                try:
                    parts_name = [p.strip() for p in name.split(">")]
                    video_title = parts_name[-1]
                    topic_name  = parts_name[-2] if len(parts_name) > 1 else "Home"
                    batch_name  = parts_name[1] if len(parts_name) > 2 else parts_name[0]
                    vid_id      = f"{i+1:03d}"

                    caption = (
                        f"[??] **Vid Id** : {vid_id}\n"
                        f"**Video Title** : {video_title}\n"
                        f"**Topic Name** : {topic_name}\n"
                        f"**Batch Name** : {batch_name}\n\n"
                        f"**Extracted By** ? Clean Leach Bot"
                    )

                    # Metadata with ffprobe
                    thumb_path = f"{vid_path}.jpg"
                    duration, width, height = 0, 0, 0
                    try:
                        import subprocess, json as _json2
                        meta = _json2.loads(
                            subprocess.check_output(
                                ["ffprobe", "-v", "quiet", "-print_format", "json",
                                 "-show_format", "-show_streams", vid_path],
                                stderr=subprocess.STDOUT
                            ).decode("utf-8")
                        )
                        duration = int(float(meta.get("format", {}).get("duration", 0)))
                        for stream in meta.get("streams", []):
                            if stream.get("codec_type") == "video":
                                width  = int(stream.get("width", 0))
                                height = int(stream.get("height", 0))
                                break
                        subprocess.check_output(
                            ["ffmpeg", "-y", "-i", vid_path,
                             "-ss", "00:00:02.000", "-vframes", "1", thumb_path],
                            stderr=subprocess.STDOUT
                        )
                        if not os.path.exists(thumb_path):
                            thumb_path = None
                    except:
                        thumb_path = None

                    await client.send_video(
                        chat_id=message.chat.id,
                        video=vid_path,
                        caption=caption,
                        supports_streaming=True,
                        duration=duration,
                        width=width,
                        height=height,
                        thumb=thumb_path,
                        progress=progress_bar,
                        progress_args=(prog_msg, start_time)
                    )
                    if thumb_path and os.path.exists(thumb_path):
                        os.remove(thumb_path)
                    await prog_msg.delete()
                    uploaded_count += 1
                except Exception as e:
                    await prog_msg.edit_text(f"? Upload failed:\n{e}")
                finally:
                    if os.path.exists(vid_path):
                        os.remove(vid_path)
            else:
                await prog_msg.edit_text(f"? Video download failed:\n{name}")

    state["is_uploading"] = False
    await message.reply_text(
        f"? **Done! Processed {uploaded_count}/{len(links_to_upload)} files.**"
    )
