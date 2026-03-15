import json
import asyncio
import uuid
import time
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Chặn tài nguyên thừa để tăng tốc
        await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "media"] else route.continue_())

        print(f"--- Đang mở: {TARGET_URL} ---")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        
        links = await page.eval_on_selector_all("a[href*='/xem-truc-tiep/']", "elements => elements.map(e => ({url: e.href, name: e.innerText}))")
        
        match_data = []
        for item in links:
            print(f"-> Kiểm tra: {item['name'].strip()}")
            m3u8_info = {"url": "", "referer": item['url']}
            
            def intercept_response(response):
                if ".m3u8" in response.url:
                    m3u8_info["url"] = response.url
            
            page.on("response", intercept_response)
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except: pass
            page.remove_listener("response", intercept_response)
            
            if m3u8_info["url"]:
                match_data.append({"title": item['name'].strip(), **m3u8_info})

        # --- XUẤT FILE CHUẨN ---
        
        # 1. File M3U (Giống buncha.txt)
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["title"]}\n')
                f.write(f'{ch["url"]}|Referer={ch["referer"]}\n')

        # 2. File JSON (Giống buncha.json - rút gọn để khớp cấu trúc)
        json_output = {
            "name": "ThienDinh TV",
            "groups": [{"name": "🔴 Live", "channels": []}]
        }
        for ch in match_data:
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
        
        print("--- HOÀN TẤT ---")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
