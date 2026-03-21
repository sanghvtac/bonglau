import json
import asyncio
import re
import hashlib
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image

BASE_DOMAIN   = "https://live20.chuoichientv.com"
COVER_IMAGE   = "https://live20.chuoichientv.com/_nuxt/img/09caa87.png"
GITHUB_REPO   = "sanghvtac/bonglau"
GITHUB_BRANCH = "main"
THUMBS_DIR    = "thumbs"

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Origin":  "https://live20.chuoichientv.com",
    "Referer": "https://live20.chuoichientv.com/",
}

# status field trong API: Live vs Upcoming
LIVE_STATUSES = {"1h", "2h", "ht", "et", "pen", "bt", "live"}

# ──────────────────────────────────────────────
# ID
# ──────────────────────────────────────────────
def generate_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]

# ──────────────────────────────────────────────
# ANH: Ghep 2 logo -> luu file PNG -> tra URL
# ──────────────────────────────────────────────
def _fetch_logo(url):
    try:
        proxy = f"https://images.weserv.nl/?url={url}&w=100&h=100&fit=contain&output=png&bg=ececec"
        res = requests.get(proxy, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None

def _build_and_save_thumb(logo_a_url, logo_b_url, match_id):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    path = os.path.join(THUMBS_DIR, f"{match_id}.png")
    try:
        canvas = Image.new("RGBA", (220, 100), (236, 236, 236, 255))
        img_a = _fetch_logo(logo_a_url) if logo_a_url else None
        img_b = _fetch_logo(logo_b_url) if logo_b_url else None
        if img_a:
            canvas.paste(img_a, (0, 0), img_a)
        if img_b:
            canvas.paste(img_b, (110, 0), img_b)
        canvas.save(path, format="PNG", optimize=True)
    except:
        if not os.path.exists(path):
            Image.new("RGBA", (220, 100), (236, 236, 236, 255)).save(path, format="PNG")
    return (f"https://raw.githubusercontent.com/{GITHUB_REPO}"
            f"/refs/heads/{GITHUB_BRANCH}/{path}")

async def make_thumb_async(logo_a_url, logo_b_url, match_id, executor):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, _build_and_save_thumb, logo_a_url, logo_b_url, match_id
    )

# ──────────────────────────────────────────────
# TIMEZONE
# ──────────────────────────────────────────────
def detect_time_offset():
    local_now  = datetime.now()
    utc_now    = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = round((local_now - utc_now).total_seconds() / 3600)
    print(f"[INFO] Local timezone: UTC+{diff_hours}")
    return diff_hours

def iso_to_vn_time(iso_str: str) -> str:
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt + timedelta(hours=7)
        return dt.strftime("%H:%M %d/%m")
    except:
        return ""

# ──────────────────────────────────────────────
# PARSE 1 ITEM TU /v2/matches
# ──────────────────────────────────────────────
def parse_match_item(item: dict) -> dict:
    teams   = item.get("teams", {})
    home    = teams.get("home", {})
    away    = teams.get("away", {})
    status  = str(item.get("status", "")).lower().strip()
    is_live = status in LIVE_STATUSES
    ext_id  = str(item.get("externalId", ""))
    slug    = item.get("slug") or ext_id
    match_url = f"{BASE_DOMAIN}/live/{ext_id}/{slug}"

    # Uu tien blvs_bonglau, fallback blvs
    blv_list = item.get("blvs_bonglau") or item.get("blvs") or []
    streams  = []
    for blv in blv_list:
        blv_id   = blv.get("username") or blv.get("id") or ""
        blv_name = blv.get("name") or blv_id
        hls = [s for s in blv.get("streams", [])
               if isinstance(s, dict) and s.get("url", "").endswith(".m3u8")]
        hd_url  = next((s["url"] for s in hls
                        if s.get("label","").upper().startswith("HD")),  "")
        fhd_url = next((s["url"] for s in hls
                        if s.get("label","").upper().startswith("FHD")), "")
        if hd_url or fhd_url:
            streams.append({"blv_id": blv_id, "blv_name": blv_name,
                            "stream_hd": hd_url, "stream_fhd": fhd_url})
    return {
        "match_id":       ext_id,
        "url":            match_url,
        "home":           home.get("name", ""),
        "away":           away.get("name", ""),
        "logo_home":      home.get("logo", ""),
        "logo_away":      away.get("logo", ""),
        "start_time_iso": item.get("matchTime", ""),
        "is_live":        is_live,
        "streams":        streams,
        "img_url":        "",
    }

# ──────────────────────────────────────────────
# LAY DANH SACH TRAN TU API
# ──────────────────────────────────────────────
def fetch_match_list() -> list[dict]:
    url = "https://api-v2.chuoichientv.com/v2/matches"
    try:
        r = requests.get(url, headers=API_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("data") or data.get("matches") or []
        else:
            items = []
        print(f"[INFO] API /v2/matches -> {len(items)} tran")
        return items
    except Exception as e:
        print(f"[ERROR] fetch_match_list: {e}")
        return []

# ──────────────────────────────────────────────
# TIEU DE
# ──────────────────────────────────────────────
def build_title(time_str, home, away, blv_and_quality=""):
    parts = []
    if time_str:
        parts.append(time_str)
    if home and away:
        parts.append(f"{home} VS {away}")
    elif home:
        parts.append(home)
    if blv_and_quality:
        parts.append(f"[{blv_and_quality}]")
    return " ".join(parts)

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
async def main():
    now_utc = datetime.now(timezone.utc)
    vn_time = now_utc + timedelta(hours=7)
    now_str = vn_time.strftime("%H:%M %d/%m/%Y")
    detect_time_offset()
    executor = ThreadPoolExecutor(max_workers=8)

    # Buoc 1: Lay danh sach tran tu API (khong can Playwright)
    raw_items = fetch_match_list()
    if not raw_items:
        print("[ERROR] Khong lay duoc danh sach tran.")
        return

    match_data     = [parse_match_item(item) for item in raw_items]
    live_count     = sum(1 for m in match_data if m["is_live"])
    upcoming_count = sum(1 for m in match_data if not m["is_live"])
    print(f"[INFO] {len(match_data)} tran: {live_count} live, {upcoming_count} sap dien ra")

    # Buoc 2: Ghep anh song song
    thumb_tasks = [
        make_thumb_async(m["logo_home"], m["logo_away"], m["match_id"], executor)
        for m in match_data
    ]
    thumb_results = await asyncio.gather(*thumb_tasks)
    for m, img_url in zip(match_data, thumb_results):
        m["img_url"] = img_url or m["logo_home"] or COVER_IMAGE
    executor.shutdown(wait=False)

    # Buoc 3: Xuat file
    json_output = {
        "name": f"Chuoi Chien TV ({now_str})",
        "image": {"url": COVER_IMAGE},
        "groups": [
            {"id": "live",     "name": "🔴 Live",        "channels": []},
            {"id": "upcoming", "name": "🗓 Sắp diễn ra", "channels": []}
        ]
    }
    m3u_content = f"#EXTM3U\n#PLAYLIST: Chuoi Chien TV ({now_str})\n"
    vlc_content = f"#EXTM3U\n#PLAYLIST: Chuoi Chien TV ({now_str})\n"

    def make_entry(m, stream_url, blv_name, quality):
        time_str = iso_to_vn_time(m["start_time_iso"])
        blv_qual = f"{blv_name} - {quality}" if blv_name else ""
        title    = build_title(time_str, m["home"], m["away"], blv_qual)
        entry_id = generate_id(m["url"] + blv_qual)
        group    = "LIVE" if m["is_live"] else "UPCOMING"
        stream   = stream_url or "http://0.0.0.0/not-live"
        referer  = ("https://live.chuoichien.tv/"
                    if stream_url and "chuoichien" not in stream_url
                    else m["url"])
        ch_json = {
            "id":      f"ch-{entry_id}",
            "name":    f"⚽ {title}",
            "type":    "single",
            "display": "thumbnail-only",
            "image": {
                "url":              m["img_url"],
                "display":          "contain",
                "padding":          1,
                "background_color": "#ececec",
            },
            "sources": [{
                "id": f"src-{entry_id}",
                "contents": [{
                    "id": f"ct-{entry_id}",
                    "streams": [{
                        "stream_links": [{
                            "url":  stream_url or "",
                            "type": "hls",
                            "request_headers": [
                                {"key": "Referer",    "value": referer},
                                {"key": "User-Agent", "value": "Mozilla/5.0"},
                            ]
                        }]
                    }]
                }]
            }]
        }
        m3u = (
            f'#EXTINF:-1 tvg-id="{entry_id}" '
            f'group-title="{group}", {title}\n'
            f'#EXTVLCOPT:http-referrer={referer}\n'
            f'#EXTVLCOPT:http-user-agent=Mozilla/5.0\n'
            f'{stream}\n'
        )
        vlc_title = re.sub(r' {2,}', ' ',
                           title.replace("[","").replace("]","")
                                .replace(" - ", " ")).strip()
        vlc = (
            f'#EXTINF:-1 tvg-id="{entry_id}" '
            f'group-title="{group}", ⚽ {vlc_title}\n'
            f'#EXTVLCOPT:network-caching=1000\n'
            f'#EXTVLCOPT:http-referrer={referer}\n'
            f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\n'
            f'{stream}\n'
        )
        return ch_json, m3u, vlc

    total_entries = 0
    for m in match_data:
        group_idx = 0 if m["is_live"] else 1
        if m["is_live"] and m["streams"]:
            for s in m["streams"]:
                blv = s["blv_name"] or s["blv_id"]
                # FHD uu tien HD
                if s["stream_fhd"]:
                    pairs = [("FHD1", s["stream_fhd"])]
                else:
                    pairs = [("HD1", s["stream_hd"])] if s["stream_hd"] else []
                for qual, url in pairs:
                    cj, ml, vl = make_entry(m, url, blv, qual)
                    json_output["groups"][group_idx]["channels"].append(cj)
                    m3u_content += ml
                    vlc_content += vl
                    total_entries += 1
        else:
            cj, ml, vl = make_entry(m, "", "", "")
            json_output["groups"][group_idx]["channels"].append(cj)
            m3u_content += ml
            vlc_content += vl
            total_entries += 1

    with open("chuoichien.json",     "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=4)
    with open("chuoichien_iptv.txt", "w", encoding="utf-8") as f:
        f.write(m3u_content)
    with open("chuoichien_vlc.txt",  "w", encoding="utf-8") as f:
        f.write(vlc_content)

    print(f"\n✅ Hoan thanh luc: {now_str} (Gio VN)")
    print(f"   🔴 Live: {live_count} tran  |  🗓 Sắp diễn ra: {upcoming_count} tran")
    print(f"   📺 Tong entries (BLV x chat luong): {total_entries}")
    print(f"   📄 Da xuat: chuoichien.json | chuoichien_iptv.txt | chuoichien_vlc.txt")

if __name__ == "__main__":
    asyncio.run(main())
