"""
THU: may GitHub Actions co goi THANG duoc API khandai khong?

Chay rieng, tach khoi crawl_khandai.py. Khong ghi de file ket qua nao.
Goi 6 lan, cach nhau vai giay, roi in ra ma HTTP, thoi gian va ket luan.

Thu CA HAI dang link:
  - ?status=live               -> Django tra trang HTML (giao dien xem API)
                                  neu trinh duyet xin HTML, tra JSON neu xin JSON
  - ?status=live&format=json   -> luon tra JSON thuan
Cung mot nguon du lieu, chi khac cach trinh bay. Nhung Cloudflare co the
dat luat rieng cho tung dang, nen thu ca hai cho chac.
"""
import html
import json
import os
import re
import subprocess
import time

import requests

BASE = os.environ.get("KHD_BASE", "https://khandai1.link")
OUT_DIR = "probe_out"
GAP = 8   # giay nghi giua 2 lan goi, tranh tu gay ra gioi han tan suat

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
CLIENT_HINTS = {
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Giong lenh fetch cua app (xin JSON)
HDR_XHR = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": f"{BASE}/lich-truc-tiep",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    **CLIENT_HINTS,
}
# Giong nguoi go link vao thanh dia chi (xin HTML) - dung nhu anh mo tay
HDR_NAV = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    **CLIENT_HINTS,
}

URL_PLAIN       = f"{BASE}/api/matches/?status=live"
URL_FORMAT_JSON = f"{BASE}/api/matches/?status=live&format=json"
URL_APP_EXACT   = f"{BASE}/api/matches/?status=live&page_size=100&ordering=smart"

CHALLENGE_RE = re.compile(
    r"just a moment|checking your browser|cf[-_]chl|challenge-platform|"
    r"attention required|captcha", re.I)


def via_requests(url, headers):
    r = requests.get(url, headers=headers, timeout=20)
    h = {k.lower(): v for k, v in r.headers.items()}
    return r.status_code, h, r.text


def via_curl(url, headers):
    """curl co dau van TLS khac han requests -> cho biet chan theo gi."""
    hf, bf = f"{OUT_DIR}/_curl_head.txt", f"{OUT_DIR}/_curl_body.txt"
    cmd = ["curl", "-sS", "-m", "20", "--compressed",
           "-D", hf, "-o", bf, "-w", "%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    code = int(r.stdout.strip() or 0)
    h = {}
    try:
        # Lay khoi header CUOI (bo qua cac khoi trung gian neu co chuyen huong)
        blocks = open(hf, encoding="utf-8", errors="replace").read() \
            .replace("\r\n", "\n").strip().split("\n\n")
        for line in blocks[-1].splitlines()[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                h[k.strip().lower()] = v.strip()
    except Exception:
        pass
    try:
        body = open(bf, encoding="utf-8", errors="replace").read()
    except Exception:
        body = ""
    return code, h, body


def extract_json(body: str):
    """Lay du lieu tu JSON thuan, hoac tu trang HTML xem API cua Django
    (JSON nam trong the <pre class="prettyprint">, da bi ma hoa HTML)."""
    try:
        return json.loads(body), "JSON"
    except Exception:
        pass
    m = re.search(r'<pre[^>]*class="[^"]*prettyprint[^"]*"[^>]*>(.*?)</pre>',
                  body or "", re.S)
    if not m:
        return None, None
    text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    i = text.find("{")
    if i < 0:
        return None, None
    try:
        return json.loads(text[i:]), "HTML (trang xem API)"
    except Exception:
        return None, None


TESTS = [
    ("1. Python tran, link ?format=json",
     via_requests, URL_FORMAT_JSON, {}),
    ("2. Python gia trinh duyet, link ?format=json",
     via_requests, URL_FORMAT_JSON, HDR_XHR),
    ("3. Python gia trinh duyet, link KHONG format, xin JSON",
     via_requests, URL_PLAIN, HDR_XHR),
    ("4. Python gia trinh duyet, link KHONG format, xin HTML (nhu anh mo tay)",
     via_requests, URL_PLAIN, HDR_NAV),
    ("5. Python gia trinh duyet, y het lenh app goi",
     via_requests, URL_APP_EXACT, HDR_XHR),
    ("6. curl gia trinh duyet, link KHONG format, xin HTML",
     via_curl, URL_PLAIN, HDR_NAV),
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ket_qua = []
    for i, (ten, fn, url, headers) in enumerate(TESTS):
        if i:
            time.sleep(GAP)
        print(f"\n=== {ten}")
        print(f"    {url}")
        t0 = time.time()
        try:
            code, h, body = fn(url, headers)
        except Exception as e:
            print(f"    LOI KET NOI: {e}")
            ket_qua.append((ten, False))
            continue
        ms = int((time.time() - t0) * 1000)

        print(f"    HTTP {code} | {ms} ms | {len(body or '')} byte | "
              f"content-type: {h.get('content-type', '?')}")
        print(f"    server: {h.get('server', '?')} | cf-ray: {h.get('cf-ray', '-')}"
              f" | cf-cache: {h.get('cf-cache-status', '-')}"
              f" | cf-mitigated: {h.get('cf-mitigated', '-')}")
        with open(f"{OUT_DIR}/test{i + 1}.txt", "w", encoding="utf-8") as f:
            f.write(f"HTTP {code}\n{json.dumps(h, indent=2)}\n\n{body}")

        data, dang = extract_json(body) if code == 200 else (None, None)
        if isinstance(data, dict) and "results" in data:
            res = data["results"]
            print(f"    >>> DUOC ({dang}): {len(res)} tran dang live")
            for m in res:
                n_link = sum(1 for c in (m.get("commentators") or [])
                             if (c.get("stream_url") or "").strip())
                print(f"        - {m.get('home_team_name')} vs "
                      f"{m.get('away_team_name')} ({m.get('sport_name')}): "
                      f"{n_link} link stream")
            ket_qua.append((ten, True))
        elif CHALLENGE_RE.search(body or ""):
            print("    >>> BI CHAN: trang kiem tra chong bot cua Cloudflare")
            ket_qua.append((ten, False))
        else:
            snippet = " ".join((body or "").split())[:160]
            print(f"    >>> KHONG DUOC. Noi dung: {snippet!r}")
            ket_qua.append((ten, False))

    ok = sum(1 for _, v in ket_qua if v)
    print("\n" + "=" * 64)
    print("TONG HOP:")
    for ten, v in ket_qua:
        print(f"  {'DUOC ' if v else 'CHAN '}  {ten}")
    print("-" * 64)
    if ok == len(TESTS):
        print(f"KET LUAN: GOI THANG DUOC ({ok}/{len(TESTS)}).")
        print("Chay them 2-3 lan o cac gio khac nhau; neu lan nao cung vay")
        print("thi co the chuyen khandai sang kieu chi dung API.")
    elif ok:
        print(f"KET LUAN: LUC DUOC LUC KHONG ({ok}/{len(TESTS)}).")
        print("Xem bang tren: kieu nao DUOC la manh moi Cloudflare chan theo gi.")
    else:
        print(f"KET LUAN: BI CHAN HOAN TOAN (0/{len(TESTS)}).")
        print("Ca hai dang link deu bi chan nhu nhau.")
        print("Giu nguyen crawl_khandai.py dung trinh duyet nhu hien tai.")
    print("=" * 64)


if __name__ == "__main__":
    main()
