import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent="Mozilla/5.0")
        await page.goto(TARGET_URL, wait_until="networkidle")
        await asyncio.sleep(5) # Đợi JS render hoàn toàn
        
        # Lấy toàn bộ khối trận đấu
        match_elements = await page.query_selector_all("a[href*='/xem-truc-tiep/']")
        match_data = []
        
        for el in match_elements:
            # 1. Trích xuất logo
            logos = await el.evaluate("el => Array.from(el.querySelectorAll('img')).map(i => i.src)")
            
            # 2. Trích xuất tên đội bằng cách tìm các thẻ con có chứa tên (dựa trên class text-sm hoặc tương tự)
            # Dùng evaluate để lấy text từ từng thẻ con một cách sạch sẽ
            details = await el.evaluate("""el => {
                const textNodes = Array.from(el.querySelectorAll('div, span')).map(e => e.innerText.trim());
                return textNodes.filter(t => t.length > 0);
            }""")
            
            # 3. Lọc tên đội và tỷ số từ danh sách detail
            # Thường tên đội nằm ở các node chứa text dài, tỷ số nằm ở giữa
            # Ta dựa vào các từ khóa đặc trưng
            name = " ".join(details[:3]) # Lấy tạm 3 node đầu để build tên
            
            match_data.append({
                "name": name,
                "logo": logos[0] if logos else "",
                "url": "https://sv1.thiendinh.live" + await el.get_attribute("href")
            })

        # Đào stream (Giữ nguyên logic của bạn)
        for item in match_data:
            m3u8 = None
            page.on("response", lambda res: globals().update(m3u8=res.url) if ".m3u8" in res.url else None)
            try:
                await page.goto(item['url'], wait_until="domcontentloaded", timeout=3000)
                await asyncio.sleep(2)
                item['stream'] = m3u8 if m3u8 else ""
            except: item['stream'] = ""
            
        # Xuất JSON
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump({"channels": match_data}, f, ensure_ascii=False, indent=4)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
