import asyncio
from extractors.spayee_api import SpayeeClient

async def run():
    client = SpayeeClient('https://www.ganitank.com', 'sohamchaudhari1912@gmail.com', 'Tanmay@1912')
    res = await client._fetch_courses_playwright()
    print('Courses fetch result length:', len(res.get('courses', [])))
    for c in res.get('courses', []):
        print(c['title'], '->', c['id'])

asyncio.run(run())
