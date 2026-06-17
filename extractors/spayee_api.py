import asyncio
import re
import requests
from urllib.parse import urlparse, unquote
from playwright.async_api import async_playwright

class SpayeeClient:
    def __init__(self, base_url, email, password):
        self.base_url = base_url
        self.email = email
        self.password = password
        
        parsed_url = urlparse(base_url)
        self.domain_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        self.domain_host = parsed_url.netloc
        
        self.total_videos = 0
        self.total_pdfs = 0
        self.courses = []
        self.raw_links = []
        
        # Detect auth mode
        if self.email and self.email.lower() == "token":
            self.token = self.password
            self.session_id = None
        elif self.email and self.email.lower() == "cookie":
            # cookie mode: password contains "SESSIONID=xxx;c_ujwt=yyy"
            self.token = None
            self.session_id = None
            self._parse_cookies(self.password)
        else:
            self.token = None
            self.session_id = None
    
    def _parse_cookies(self, cookie_str):
        """Parse cookie string like SESSIONID=xxx;c_ujwt=yyy"""
        self.cookie_dict = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.cookie_dict[k.strip()] = v.strip()
        self.session_id = self.cookie_dict.get("SESSIONID")
        self.token = self.cookie_dict.get("c_ujwt") or self.cookie_dict.get("jwt")
        
    async def fetch_courses(self):
        try:
            # Method 1: Try to get courses from public store page (works without full auth)
            courses = self._fetch_courses_from_store()
            if courses:
                self.courses = courses
                return {"success": True, "courses": courses}
            
            # Method 2: Try Playwright-based scraping
            return await self._fetch_courses_playwright()
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _fetch_courses_from_store(self):
        """Fetch courses from the public store page using requests (fast, no browser needed)"""
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            
            if self.token:
                session.cookies.set("c_ujwt", self.token, domain=self.domain_host, path="/")
                session.cookies.set("jwt", self.token, domain=self.domain_host, path="/")
            if self.session_id:
                session.cookies.set("SESSIONID", self.session_id, domain=self.domain_host, path="/")
            
            # Try store page
            r = session.get(f"{self.domain_url}/s/store", timeout=15)
            
            if r.status_code != 200:
                return []
            
            courses = []
            seen = set()
            
            # Pattern 1: /courses/Title-CourseId (new Graphy URLs)
            matches = re.findall(r'href="(https?://[^"]*?/courses/([^"]+)-([a-f0-9]{24}))"', r.text)
            for full_url, title_slug, course_id in matches:
                if course_id not in seen:
                    seen.add(course_id)
                    title = unquote(title_slug).replace('-', ' ')
                    courses.append({"id": full_url, "title": title})
            
            # Pattern 2: /s/store/courses/CategoryName (category pages)
            cat_matches = re.findall(r'href="(https?://[^"]*?/s/store/courses/([^"]+))"', r.text)
            for full_url, cat_name in cat_matches:
                cat_name_decoded = unquote(cat_name)
                if cat_name_decoded not in seen and not cat_name_decoded.startswith('<'):
                    seen.add(cat_name_decoded)
                    # Fetch category page for individual courses
                    try:
                        cr = session.get(full_url, timeout=10)
                        cat_courses = re.findall(r'href="(https?://[^"]*?/courses/([^"]+)-([a-f0-9]{24}))"', cr.text)
                        for curl, ctitle, cid in cat_courses:
                            if cid not in seen:
                                seen.add(cid)
                                courses.append({"id": curl, "title": unquote(ctitle).replace('-', ' ')})
                    except:
                        pass
            
            # Pattern 3: /s/store/content/CourseId
            content_matches = re.findall(r'/s/store/content/([a-f0-9]{24})', r.text)
            for cid in content_matches:
                if cid not in seen:
                    seen.add(cid)
                    courses.append({"id": f"{self.domain_url}/s/store/content/{cid}", "title": f"Course {cid[:8]}"})
            
            return courses
            
        except Exception as e:
            print(f"Store fetch error: {e}")
            return []
    
    async def _fetch_courses_playwright(self):
        """Fallback: use Playwright to scrape courses page"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                context = await browser.new_context(user_agent=ua)
                
                cookies = []
                if self.token:
                    cookies.append({"name": "jwt", "value": self.token, "domain": self.domain_host, "path": "/"})
                    cookies.append({"name": "c_ujwt", "value": self.token, "domain": self.domain_host, "path": "/"})
                if self.session_id:
                    cookies.append({"name": "SESSIONID", "value": self.session_id, "domain": self.domain_host, "path": "/"})
                if cookies:
                    await context.add_cookies(cookies)
                
                page = await context.new_page()
                
                if not self.token and not self.session_id:
                    # Try email/password login
                    login_url = f"{self.domain_url}/s/authenticate"
                    await page.goto(login_url, wait_until="domcontentloaded")
                    
                    email_sel = 'input[type="email"], input[name="email"], #email'
                    pass_sel = 'input[type="password"], input[name="password"], #password'
                    btn_sel = 'button[type="submit"], #login-btn, .login-btn'
                    
                    try:
                        await page.wait_for_selector(email_sel, timeout=5000)
                        await page.fill(email_sel, self.email)
                        await page.fill(pass_sel, self.password)
                        await page.click(btn_sel)
                    except:
                        try:
                            iframe = page.frame_locator('iframe#microfe-popup-login, iframe[src*="login"]')
                            await iframe.locator(email_sel).first.wait_for(state="attached", timeout=15000)
                            await iframe.locator(email_sel).first.fill(self.email)
                            await iframe.locator(pass_sel).first.fill(self.password)
                            await iframe.locator(btn_sel).first.click()
                        except:
                            await browser.close()
                            return {"success": False, "error": f"Login failed. Try: {self.domain_url} token*<your_c_ujwt_cookie>"}
                    await asyncio.sleep(5)
                
                js_eval = '''() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    const courseLinks = links.filter(a => 
                        a.href.includes('/s/store/courses/') || 
                        a.href.includes('/courses/') ||
                        a.href.includes('/course/') ||
                        a.href.includes('/products/') ||
                        a.href.includes('/t/c/')
                    );
                    const unique = [];
                    const seen = new Set();
                    courseLinks.forEach(a => {
                        if (!seen.has(a.href)) {
                            seen.add(a.href);
                            let title = a.innerText.trim();
                            if (!title) { const img = a.querySelector('img'); if (img && img.alt) title = img.alt; }
                            if (!title) title = "Unknown Course";
                            if (title.length > 3) unique.push({id: a.href, title: title});
                        }
                    });
                    return unique;
                }'''
                
                # Try multiple pages
                for url_path in ["/s/mycourses", "/t/u/activeCourses", "/s/store"]:
                    try:
                        await page.goto(f"{self.domain_url}{url_path}", wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(5)
                        courses_dict = await page.evaluate(js_eval)
                        if courses_dict:
                            await browser.close()
                            self.courses = courses_dict
                            return {"success": True, "courses": courses_dict}
                    except:
                        continue
                
                await browser.close()
                return {"success": False, "error": "No courses found. Try: URL token*<your_c_ujwt_cookie_value>"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def extract_links(self, course_id):
        """Extract video/PDF links from a course page"""
        self.total_videos = 0
        self.total_pdfs = 0
        self.raw_links = []
        self.title_map = {}
        self.id_map = {}
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                context = await browser.new_context(user_agent=ua)
                
                cookies = []
                if getattr(self, 'token', None):
                    cookies.append({"name": "jwt", "value": self.token, "domain": self.domain_host, "path": "/"})
                    cookies.append({"name": "c_ujwt", "value": self.token, "domain": self.domain_host, "path": "/"})
                if getattr(self, 'session_id', None):
                    cookies.append({"name": "SESSIONID", "value": self.session_id, "domain": self.domain_host, "path": "/"})
                if cookies:
                    await context.add_cookies(cookies)
                
                page = await context.new_page()
                
                if not getattr(self, 'token', None) and not getattr(self, 'session_id', None):
                    login_url = f"{self.domain_url}/s/authenticate"
                    await page.goto(login_url, wait_until="domcontentloaded")
                    email_sel = 'input[type="email"], input[name="email"], #email'
                    pass_sel = 'input[type="password"], input[name="password"], #password'
                    btn_sel = 'button[type="submit"], #login-btn, .login-btn'
                    try:
                        await page.wait_for_selector(email_sel, timeout=5000)
                        await page.fill(email_sel, self.email)
                        await page.fill(pass_sel, self.password)
                        await page.click(btn_sel)
                    except:
                        try:
                            iframe = page.frame_locator('iframe#microfe-popup-login, iframe[src*="login"]')
                            await iframe.locator(email_sel).first.wait_for(state="attached", timeout=15000)
                            await iframe.locator(email_sel).first.fill(self.email)
                            await iframe.locator(pass_sel).first.fill(self.password)
                            await iframe.locator(btn_sel).first.click()
                        except:
                            pass
                    await asyncio.sleep(5)
                
                def _is_content_url(u):
                    if not isinstance(u, str): return False
                    return ("cloudfront.net" in u or "spayee" in u or ".m3u8" in u or ".pdf" in u)
                
                def _is_junk(u):
                    if not isinstance(u, str): return False
                    return any(ext in u.lower() for ext in ['.jpg', '.png', '.jpeg', '.css', '.woff', '.ttf', '.js', '.ts', '.svg', '.ico'])
                
                def _extract_titles(data, current_chapter=""):
                    import json
                    if isinstance(data, dict):
                        title = data.get('title') or data.get('chapterTitle') or data.get('name') or data.get('spayee:title') or ""
                        if 'spayee:resource' in data:
                            title = data['spayee:resource'].get('spayee:title', title)
                            
                        # If this has items/children, it's probably a chapter
                        has_children = 'items' in data or 'resources' in data or 'children' in data
                        new_chapter = title if has_children and title else current_chapter
                        
                        item_id = data.get('id') or data.get('_id') or data.get('spayee:id') or data.get('resourceId')
                        if 'spayee:resource' in data:
                            item_id = data['spayee:resource'].get('spayee:id', item_id)
                            
                        # Extract URLs
                        url = None
                        for k in ['url', 'contentUrl', 'drmUrl', 'fileUrl', 'videoUrl', 'pdfUrl', 'spayee:url', 'spayee:hlsUrl']:
                            v = data.get(k)
                            if _is_content_url(v):
                                url = v
                                break
                        if 'spayee:resource' in data:
                            for k in ['spayee:url', 'spayee:hlsUrl', 'spayee:pdfUrl']:
                                v = data['spayee:resource'].get(k)
                                if _is_content_url(v):
                                    url = v
                                    break
                                    
                        # Save mapping
                        full_title = f"({new_chapter}) {title}" if new_chapter else title
                        if not full_title.strip():
                            full_title = "Item"
                            
                        if url and not _is_junk(url):
                            if url not in self.raw_links:
                                self.raw_links.append(url)
                            self.title_map[url] = full_title
                            if item_id:
                                self.id_map[url] = item_id
                                
                        if item_id and title:
                            self.title_map[item_id] = full_title
                            
                        for v in data.values():
                            _extract_titles(v, new_chapter)
                            
                    elif isinstance(data, list):
                        for item in data:
                            _extract_titles(item, current_chapter)

                async def handle_request(request):
                    try:
                        u = request.url
                        if _is_content_url(u) and not _is_junk(u):
                            if u not in self.raw_links:
                                self.raw_links.append(u)
                    except:
                        pass
                page.on("request", handle_request)
                
                async def handle_response(response):
                    try:
                        ct = response.headers.get("content-type", "")
                        if "application/json" in ct:
                            text = await response.text()
                            
                            # Parse JSON to map titles
                            try:
                                import json
                                data = json.loads(text)
                                _extract_titles(data)
                            except:
                                pass
                                
                            urls = re.findall(r'https?://[^\s\'"<>]+', text)
                            for u in urls:
                                if _is_content_url(u) and not _is_junk(u):
                                    if u not in self.raw_links:
                                        self.raw_links.append(u)
                    except:
                        pass
                page.on("response", handle_response)
                
                # Navigate to course page
                await page.goto(course_id, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(15)
                
                # Try to trigger dynamic loads by scrolling
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await asyncio.sleep(5)
                
                # Also scrape HTML content for URLs and JSON strings
                page_content = await page.content()
                
                # Try to find JSON embedded in scripts
                import json
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', page_content, re.DOTALL)
                for script in scripts:
                    try:
                        # naive JSON extraction
                        json_strs = re.findall(r'\{.*\}', script)
                        for js_str in json_strs:
                            try:
                                data = json.loads(js_str)
                                _extract_titles(data)
                            except:
                                pass
                    except:
                        pass
                
                urls = re.findall(r'https?://[^\s\'"<>]+', page_content)
                for u in urls:
                    if _is_content_url(u) and not _is_junk(u):
                        if u not in self.raw_links:
                            self.raw_links.append(u)
                                    
                await browser.close()
                
                # Format output
                formatted_links = []
                for link in self.raw_links:
                    name = self.title_map.get(link)
                    
                    # If name not directly mapped by URL, try to map by ID in the URL
                    if not name:
                        for k, v in self.title_map.items():
                            if str(k) in link and len(str(k)) > 10:
                                name = v
                                break
                    
                    if not name:
                        name = "Document PDF" if ".pdf" in link else "Video File"
                    
                    # Check if we should append *ID
                    suffix = ""
                    mapped_id = self.id_map.get(link)
                    if mapped_id:
                        suffix = f"*{mapped_id}"
                    else:
                        # try to find uuid in link
                        import re
                        m = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', link)
                        if m:
                            suffix = f"*{m.group(1)}"
                            # Clean the UUID from the link itself if it's not a parameter
                            if f"*{m.group(1)}" not in link:
                                pass
                        else:
                            # maybe the 24-char hex id
                            m = re.search(r'([a-f0-9]{24})', link)
                            if m:
                                suffix = f"*{m.group(1)}"

                    # If the link already contains *, don't add it again
                    if "*" in link:
                        final_url = link
                    else:
                        final_url = f"{link}{suffix}"
                        
                    formatted_links.append(f"{name} : {final_url}")
                    
                    if ".pdf" in link:
                        self.total_pdfs += 1
                    else:
                        self.total_videos += 1
                    
                # Deduplicate by final URL
                unique_links = []
                seen = set()
                for flink in formatted_links:
                    if flink not in seen:
                        seen.add(flink)
                        unique_links.append(flink)
                        
                return unique_links
        except Exception as e:
            print(f"Extraction error: {e}")
            return []
