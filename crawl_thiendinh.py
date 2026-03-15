import json
import re
import cloudscraper

# ĐỊA CHỈ API ẨN CỦA THIÊN ĐỈNH (Nơi chứa dữ liệu thật)
API_URL = "https://api.thiendinh.live/api/match/list?by=state&value=live&sport=bong-da"
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})

def main():
    print(f"--- Đang lấy dữ liệu từ API: {API_URL} ---")
    try:
        response = scraper.get(API_URL, timeout=30)
        
        if response.status_code != 200:
            print(f"❌ Lỗi truy cập API. Status: {response.status_code}")
            return

        data = response.json()
        # Giả sử cấu trúc JSON trả về danh sách trận trong mục 'data' hoặc 'matches'
        # Chúng ta sẽ lấy danh sách các trận đấu từ JSON
        raw_matches = data.get('data', [])
        
        match_data = []
        print(f"--- Tìm thấy {len(raw_matches)} trận đấu từ hệ thống ---")

        for match in raw_matches:
            # Tạo link xem trực tiếp từ ID trận đấu
            match_id = match.get('id')
            slug = match.get('slug')
            name = f"{match.get('time')} {match.get('home_team_name')} vs {match.get('away_team_name')}"
            url = f"https://sv1.thiendinh.live/xem-truc-tiep/bong-da/{slug}.{match_id}"
            
            stream_url = ""
            # Nếu trận đang live (thường có status hoặc dựa vào flag của API)
            if match.get('is_live') or "LIVE" in name.upper():
                try:
                    detail_resp = scraper.get(url, timeout=20)
                    links = re.findall(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', detail_resp.text)
                    if links:
                        stream_url = max(links, key=len)
                        print(f" ✅ {name}: Đã có link.")
                except: pass
            
            match_data.append({"url": url, "name": name, "stream_url": stream_url})

        # --- GHI FILE (Giữ nguyên logic cũ) ---
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
                else:
                    f.write(f'#EXTINF:-1 group-title="Sắp diễn ra",{ch["name"]}\n#\n')

        print("--- HOÀN TẤT ---")

    except Exception as e:
        print(f"Lỗi: {e}")

if __name__ == "__main__":
    main()
