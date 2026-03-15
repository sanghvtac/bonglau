import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Chặn tài nguyên thừa (ảnh, font) để tăng tốc độ load trang
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        
        # Cuộn trang để ép website load đủ danh sách
        for _ in range(5):
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(1)
        
        links = await page.eval_on_selector_all("a[href*='/xem-truc-tiep/']", "elements => elements.map(e => ({url: e.href, name: e.innerText}))")
        print(f"--- Đã tìm thấy {len(links)} trận đấu ---")
        
        match_data = []
        for item in links:
            if any(d['url'] == item['url'] for d in match_data): continue
            
            m3u8_info = {"url": "", "referer": item['url']}
            def intercept_response(response):
                if ".m3u8" in response.url: m3u8_info["url"] = response.url
            
            page.on("response", intercept_response)
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            except: pass
            page.remove_listener("response", intercept_response)
            
            match_data.append({"title": item['name'].strip(), **m3u8_info})

        # --- XUẤT 3 FILE ĐỊNH DẠNG CHUẨN ---

        # 1. File IPTV M3U (Chuẩn cho TiviMate, IPTV Smarters)
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch["url"]:
                    f.write(f'#EXTINF:-1 group-title="🔴 Live",{ch["title"]}\n{ch["url"]}|Referer={ch["referer"]}\n')
                else:
                    f.write(f'#EXTINF:-1 group-title="Sắp diễn ra",{ch["title"]}\n#\n')

        # 2. File VLC (Định dạng đơn giản, tương thích VLC)
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch["url"]:
                    f.write(f'#EXTINF:-1,{ch["title"]}\n{ch["url"]}\n')
        
        # 3. File JSON (Định dạng phân cấp cho các App IPTV tùy chỉnh)
        json_output = {
            "name": "ThienDinh TV",
            "groups": [{"name": "🔴 Live", "channels": []}]
        }
        for ch in match_data:
            if ch["url"]:
                channel = {
                    "name": ch["title"],
                    "sources": [{"contents": [{"streams": [{"stream_links": [{
                        "url": ch["url"],
                        "request_headers": [{"key": "Referer", "value": ch["referer"]}]
                    }]}]}]}]
                }
                json_output["groups"][0]["channels"].append(channel)
            
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(json_output, f, ensure_ascii=False, indent=4)
        
        print("--- HOÀN TẤT: Đã tạo 3 file (IPTV, VLC, JSON) ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
