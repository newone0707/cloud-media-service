import asyncio
from extractors.spayee_api import SpayeeClient

async def run():
    client = SpayeeClient('https://www.ganitank.com', 'sohamchaudhari1912@gmail.com', 'Tanmay@1912')
    courses_resp = await client.fetch_courses()
    print('Courses fetch result:', courses_resp.get('success'))
    
    if courses_resp.get('success'):
        for c in courses_resp['courses']:
            title = c["title"]
            cid = c["id"]
            print(f"Extracting course {title} with ID {cid}")
            links = await client.extract_links(cid)
            print(f"Found {len(links)} links!")
            for l in links[:5]:
                print(l)
            break

asyncio.run(run())
