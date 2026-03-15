import json
import re
import cloudscraper
from bs4 import BeautifulSoup

# Đường dẫn trang lịch thi đấu
TARGET_URL = "https://sv1.thiendinh.live/lich-thi-dau/bong-da?by=state&value=live"
scraper = cloudscraper.create_scraper()

def main():
    print(f"--- Đang quét lịch thi đấu: {TARGET_URL} ---")
    try:
        response = scraper.get(TARGET_URL)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Tìm tất cả các link trận đấu
        elements = soup.select('a[href*="/xem-truc-tiep/"]')
        match_data = []
        processed_links = set()

        for el in elements:
            url = el['href']
            if url.startswith('/'):
                url = "https://sv1.thiendinh.live" + url
            
            name = el.get_text(strip=True).replace('\n', ' ')
            if url not in processed_links:
                processed_links.add(url)
                match_data.append({"url": url, "name": name, "stream_url": ""})

        print(f"Tìm thấy {len(match_data)} trận. Đang lấy link stream...")

        # 2. Đào link m3u8 cho các trận đang LIVE
        for item in match_data:
            name_upper = item['name'].upper()
            if "LIVE" in name_upper or "TRỰC TIẾP" in name_upper:
                try:
                    detail_resp = scraper.get(item['url'])
                    # Tìm link m3u8 dài nhất (chứa token)
                    links = re.findall(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', detail_resp.text)
                    if links:
                        item['stream_url'] = max(links, key=len)
                        print(f" ✅ {item['name']}: Đã lấy link.")
                    else:
                        print(f" ⚠️ {item['name']}: Không tìm thấy link m3u8.")
                except:
                    print(f" ❌ Lỗi khi truy cập: {item['name']}")
            else:
                print(f" ℹ️ {item['name']}: Sắp diễn ra.")

        # 3. XUẤT FILE JSON
        with open("thiendinh.json", "w", encoding="utf-8") as f:
            json.dump(match_data, f, ensure_ascii=False, indent=4)

        # 4. XUẤT FILE TXT CHO VLC (Không hậu tố)
        with open("thiendinh_vlc.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1,{ch["name"]}\n{ch["stream_url"]}\n')

        # 5. XUẤT FILE TXT CHO IPTV (Có hậu tố Referer)
        with open("thiendinh_iptv.txt", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for ch in match_data:
                if ch['stream_url']:
                    f.write(f'#EXTINF:-1 group-title="ThienDinhTV",{ch["name"]}\n{ch["stream_url"]}|Referer=https://sv1.thiendinh.live/\n')
                else:
                    f.write(f'#EXTINF:-1 group-title="Sắp diễn ra",{ch["name"]}\n#\n')

        print("--- HOÀN TẤT VIẾT FILE ---")

    except Exception as e:
        print(f"Lỗi hệ thống: {e}")

if __name__ == "__main__":
    main()
