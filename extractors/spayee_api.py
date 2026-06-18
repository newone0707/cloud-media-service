import asyncio
import json
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
            if getattr(self, 'session_id', None) and getattr(self, 'token', None):
                # Try API fetch first if cookies exist
                api_result = await self._fetch_courses_api()
                if api_result.get("success"):
                    return api_result
                    
            # ONLY use Playwright-based scraping to get the ACTUAL enrolled courses
            # Store scraping gets junk public courses that the user isn't enrolled in.
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
                            
                            # Try the Graphy 2-step flow first: name="identifier" -> Continue -> name="password" -> Continue
                            try:
                                identifier_input = iframe.locator('input[name="identifier"], input#input-identifier').first
                                await identifier_input.wait_for(state="visible", timeout=5000)
                                await identifier_input.fill(self.email)
                                await iframe.locator('button[type="submit"]').first.click()
                                await asyncio.sleep(2)
                                
                                pass_input = iframe.locator('input[name="password"], input[type="password"]').first
                                await pass_input.wait_for(state="visible", timeout=5000)
                                await pass_input.fill(self.password)
                                await iframe.locator('button[type="submit"]').first.click()
                            except:
                                # Fallback to standard 1-step or standard email toggle
                                try:
                                    email_toggle = iframe.locator('text="Email", text="Continue with Email", text="Login with Email"').first
                                    await email_toggle.wait_for(state="visible", timeout=3000)
                                    await email_toggle.click()
                                    await asyncio.sleep(2)
                                except:
                                    pass
                                    
                                await iframe.locator(email_sel).first.wait_for(state="attached", timeout=10000)
                                await iframe.locator(email_sel).first.fill(self.email)
                                await iframe.locator(pass_sel).first.fill(self.password)
                                await iframe.locator(btn_sel).first.click()
                        except:
                            await browser.close()
                            return {"success": False, "error": f"Login failed. Try: {self.domain_url} token*<your_c_ujwt_cookie>"}
                    await asyncio.sleep(5)
                
                js_eval = '''() => {
                    let root = document.querySelector('main') || document.querySelector('.dashboard-container') || document.querySelector('.my-courses') || document;
                    const links = Array.from(root.querySelectorAll('a'));
                    
                    const validLinks = links.filter(a => {
                        let parent = a.parentElement;
                        while(parent && parent !== document.body) {
                            if (parent.tagName === 'NAV' || parent.tagName === 'HEADER' || parent.tagName === 'FOOTER' || parent.id.toLowerCase().includes('header') || parent.className.toLowerCase().includes('header')) return false;
                            parent = parent.parentElement;
                        }
                        return true;
                    });

                    const courseLinks = validLinks.filter(a => 
                        a.href.includes('/s/store/courses/') || 
                        a.href.includes('/courses/') ||
                        a.href.includes('/course/') ||
                        a.href.includes('/products/') ||
                        a.href.includes('/t/c/')
                    );
                    
                    const unique = [];
                    const seen = new Set();
                    courseLinks.forEach(a => {
                        // ignore pure fragment or javascript links
                        if (a.href.includes('javascript:') || a.getAttribute('href') === '#') return;
                        if (!seen.has(a.href)) {
                            seen.add(a.href);
                            let title = a.innerText.trim();
                            if (!title) { const img = a.querySelector('img'); if (img && img.alt) title = img.alt; }
                            if (!title) title = "Course";
                            if (title.length > 3) unique.push({id: a.href, title: title});
                        }
                    });
                    return unique;
                }'''
                
                # Try multiple pages
                for url_path in ["/s/mycourses", "/t/u/activeCourses"]:
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

    async def _fetch_courses_api(self):
        """Fetch courses using the internal API directly if cookies are present"""
        import aiohttp
        url = f"{self.domain_url}/s/mycourses/get?skip=0&limit=100&queryData=%7B%7D&isVerticalFilters=true&categoryLevel=0&archived=false"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        
        # Build cookies
        cookies = {}
        if self.token:
            cookies["jwt"] = self.token
            cookies["c_ujwt"] = self.token
        if self.session_id:
            cookies["SESSIONID"] = self.session_id
            
        try:
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"API returned {response.status}. Session expired or invalid cookies."}
                    data = await response.json()
                    
                    if "data" not in data or "data" not in data["data"]:
                        return {"success": False, "error": "Unexpected API response format."}
                        
                    unique = []
                    for item in data["data"]["data"]:
                        res_data = item.get("spayee:resource", {})
                        title = res_data.get("spayee:title", "Course")
                        course_url_slug = res_data.get("spayee:courseUrl", item.get("_id"))
                        # Construct link with ID appended so extract_links can use it
                        course_id = item.get("_id")
                        link = f"{self.domain_url}/s/store/courses/description/{course_url_slug}?id={course_id}"
                        unique.append({"id": link, "title": title})
                    
                    if not unique:
                        return {"success": False, "error": "No enrolled courses found in API response."}
                        
                    self.courses = unique
                    return {"success": True, "courses": unique}
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
            import aiohttp
            import uuid
            
            # The course_id is either passed as a slug or ID. But since we modified _fetch_courses_api,
            # it might be the slug if it's the old format, or the ID.
            # If the user gives a URL like /s/store/courses/description/xyz, we need the course_id.
            # We can get courseId by fetching the description page and finding `courseId: "..."`.
            
            course_obj_id = course_id
            if "id=" in course_id:
                m = re.search(r'id=([a-f0-9]{24})', course_id)
                if m:
                    course_obj_id = m.group(1)
            elif "ganitank.com" in course_id or "description" in course_id:
                # Need to extract course ID. The HTML description page requires auth and doesn't contain the ID.
                # Let's map the slug back using the user's enrolled courses API.
                m = re.search(r'/description/([^?]+)', course_id)
                if m:
                    slug = m.group(1)
                    # fetch from API
                    import aiohttp
                    api_url = f"{self.domain_url}/s/mycourses/get?skip=0&limit=100&queryData=%7B%7D"
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "application/json"
                    }
                    cookies = {}
                    if getattr(self, 'token', None):
                        cookies["jwt"] = self.token
                        cookies["c_ujwt"] = self.token
                    if getattr(self, 'session_id', None):
                        cookies["SESSIONID"] = self.session_id
                    
                    async with aiohttp.ClientSession(cookies=cookies) as session:
                        async with session.get(api_url, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                for item in data.get("data", {}).get("data", []):
                                    if item.get("spayee:resource", {}).get("spayee:courseUrl") == slug:
                                        course_obj_id = item.get("_id")
                                        break
                                        
                if course_obj_id == course_id:
                    # failed to map
                    return []
            else:
                # If it's a URL, extract the ID
                m = re.search(r'([a-f0-9]{24})', course_id)
                if m:
                    course_obj_id = m.group(1)
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json"
            }
            cookies = {}
            if getattr(self, 'token', None):
                cookies["jwt"] = self.token
                cookies["c_ujwt"] = self.token
            if getattr(self, 'session_id', None):
                cookies["SESSIONID"] = self.session_id
                
            async with aiohttp.ClientSession(cookies=cookies) as session:
                # Fetch TOC
                toc_url = f"{self.domain_url}/s/store/course/{course_obj_id}/toc"
                async with session.get(toc_url, headers=headers) as resp:
                    if resp.status != 200:
                        return []
                    toc_data = await resp.json()
                    
                if "toc" not in toc_data:
                    return []
                    
                formatted_links = []
                
                # Helper to traverse TOC
                async def process_items(items, chapter_title):
                    for item in items:
                        item_type = item.get("type", "")
                        item_id = item.get("id", "")
                        item_title = item.get("title", "Item")
                        
                        full_title = f"({chapter_title}) {item_title}" if chapter_title else item_title
                        
                        if item_type == "label" and "items" in item:
                            await process_items(item["items"], item_title)
                            
                        elif item_type == "video":
                            # Fetch video URL
                            v_url = f"{self.domain_url}/s/courses/{course_obj_id}/videos/{item_id}/get"
                            async with session.get(v_url, headers=headers) as vresp:
                                if vresp.status == 200:
                                    vdata = await vresp.json()
                                    resource = vdata.get("spayee:resource", {})
                                    stream_url = resource.get("spayee:streamUrl")
                                    if stream_url:
                                        self.total_videos += 1
                                        suffix = f"*{uuid.uuid4()}"
                                        formatted_links.append(f"{full_title} : {stream_url}{suffix}")
                                        
                        elif item_type == "pdf":
                            # Fetch PDF URL
                            p_url = f"{self.domain_url}/s/courses/{course_obj_id}/pdfs/{item_id}/preview/url"
                            async with session.get(p_url, headers=headers) as presp:
                                if presp.status == 200:
                                    pdata = await presp.json()
                                    pdf_url = pdata.get("url")
                                    if pdf_url:
                                        self.total_pdfs += 1
                                        suffix = f"*{uuid.uuid4()}"
                                        formatted_links.append(f"{full_title} : {pdf_url}{suffix}")
                
                await process_items(toc_data["toc"], "")
                
                # Deduplicate
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
