import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # DANH SÁCH LƯU TRỮ
        match_data = []

        # HÀM LẮNG NGHE LINK M3U8
        m3u8_links = []
        def intercept_response(response):
            if ".m3u8" in response.url:
                m3u8_links.append(response.url)

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
        # Lấy danh sách trận
        links = await page.eval_on_selector_all("a[href*='/xem-truc-tiep/']", "elements => elements.map(e => ({url: e.href, name: e.innerText}))")
        
        for item in links:
            print(f"-> Đang kiểm tra: {item['name']}")
            m3u8_links.clear()
            page.on("response", intercept_response)
            
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(5) # Đợi link stream được gửi về
                stream = max(m3u8_links, key=len) if m3u8_links else ""
                match_data.append({"url": item['url'], "name": item['name'].strip(), "stream_url": stream})
            except:
                match_data.append({"url": item['url'], "name": item['name'].strip(), "stream_url": ""})
            
            page.remove_listener("response", intercept_response)

        # XUẤT FILE
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, ensure_ascii=False, indent=4)
        
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1,{ch["name"]}\n{ch["stream_url"]}\n')
                    
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["name"]}\n{ch["stream_url"]}|Referer=https://sv1.thiendinh.live/\n')
        
        print("--- HOÀN TẤT ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
