import json
import cloudscraper # Thay thế selenium bằng cloudscraper
from bs4 import BeautifulSoup

TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"
scraper = cloudscraper.create_scraper()

def get_matches():
    response = scraper.get(TARGET_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    match_data = []
    
    # Tìm các link trận đấu
    elements = soup.select('a[href*="/xem-truc-tiep/"]')
    for el in elements:
        url = "https://sv1.thiendinh.live" + el['href'] if el['href'].startswith('/') else el['href']
        name = el.get_text(strip=True)
        if not any(d['url'] == url for d in match_data):
            match_data.append({"url": url, "name": name, "stream_url": ""})
    return match_data

def get_stream_url(match_url):
    try:
        resp = scraper.get(match_url)
        # Tìm link m3u8 trong mã nguồn
        import re
        m3u8 = re.search(r'https://.*?\.m3u8', resp.text)
        return m3u8.group(0) if m3u8 else ""
    except: return ""

# --- CHẠY CHÍNH ---
match_data = get_matches()
for item in match_data:
    if "LIVE" in item['name'].upper() or "TRỰC TIẾP" in item['name'].upper():
        item['stream_url'] = get_stream_url(item['url'])

# Xuất file JSON, TXT... (Giữ nguyên logic xuất file cũ của bạn)
with open("thiendinh.json", "w", encoding="utf-8") as f: json.dump(match_data, f, ensure_ascii=False, indent=4)
# ... (Thêm code xuất file TXT giống như bạn đã làm)
