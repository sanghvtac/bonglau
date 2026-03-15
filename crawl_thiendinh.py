import json
import re
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Chặn các tài nguyên thừa để load cực nhanh
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3) # Đợi trang list load xong
        
        # Lấy danh sách trận
        links = await page.eval_on_selector_all("a[href*='/xem-truc-tiep/']", "elements => elements.map(e => ({url: e.href, name: e.innerText}))")
        
        match_data = []
        for item in links:
            print(f"-> Kiểm tra trận: {item['name'].strip()}")
            m3u8_list = []
            
            # Hàm bắt link mạng
            def intercept_response(response):
                if ".m3u8" in response.url:
                    m3u8_list.append(response.url)
            
            page.on("response", intercept_response)
            
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2) # Giảm thời gian chờ xuống 2s để tăng tốc
                stream = max(m3u8_list, key=len) if m3u8_list else ""
                match_data.append({"title": item['name'].strip(), "url": stream})
                if stream: print(f"  ✅ Tìm thấy stream.")
            except:
                match_data.append({"title": item['name'].strip(), "url": ""})
            
            page.remove_listener("response", intercept_response)

        # XUẤT FILE CHUẨN IPTV/VLC/JSON
        # 1. File IPTV chuẩn (dùng được cho TiviMate)
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U x-tvg-url=\"\"\n")
            for ch in match_data:
                if ch['url']:
                    f.write(f'#EXTINF:-1 group-title="Bong Da",{ch["title"]}\n{ch["url"]}|Referer=https://sv1.thiendinh.live/\n')
        
        # 2. File VLC đơn giản
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['url']:
                    f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["url"]}\n')
        
        # 3. File JSON chuẩn cho các App tùy chỉnh
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, ensure_ascii=False, indent=4)
        
        print("--- HOÀN TẤT: Đã cập nhật 3 file chuẩn ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
