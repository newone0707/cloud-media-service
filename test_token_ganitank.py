import asyncio
from extractors.spayee_api import SpayeeClient

async def run():
    client = SpayeeClient('https://www.ganitank.com', 'token', 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJwIjoiNDNiZDlkY2VmMTk5MGYyZDhlOWYyYWEzMGNkOTg0ZTN4Mm9lb0kxamxSRkM3aXQrUlUyaFpnPT0iLCJlIjoiZjcwZWZhNWYzMTc5MWViY2QwMTM3NzFkMDE3ODM5YTJ5c0VsT1M0VHRheTRWNTdaaDJiblU2b0JCUG5JTm54b2FuMDcrenFreVZvaXZZTHlocVhjcU1RQTFNaVF5WUgxIiwiZXhwIjoxNzg0MzA5MDkwfQ.LnqF9rPPuPAI-0R0-8mUoLOXVWTOymYifCdkKl6WQ2Q')
    courses_resp = await client.fetch_courses()
    print('Courses fetch result:', courses_resp.get('success'))
    
    if courses_resp.get('success'):
        for c in courses_resp['courses']:
            title = c["title"]
            cid = c["id"]
            print(f"Extracting course {title} with URL {cid}")
            links = await client.extract_links(cid)
            print(f"Found {len(links)} links!")
            break

asyncio.run(run())
