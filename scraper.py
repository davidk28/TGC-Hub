import json
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
TZ_TW = timezone(timedelta(hours=8))

KEYWORDS = [
    "資安", "資通安全", "個資", "個人資料", "弱點",
    "工控", "OT", "IoT", "物聯網", "SCADA", "零信任", "防護基準", "施行細則"
]


def fetch_html(url: str, timeout: int = 15) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as exc:
        print(f"[WARN] 無法取得網頁 ({url}): {exc}")
        return None


def refresh_known_pages(existing_map: dict) -> None:
    known_urls = [
        ("acs-21256", "https://moda.gov.tw/ACS/operations/aisec/21256"),
        ("acs-ciip-115-ot", "https://moda.gov.tw/ACS/operations/ciip/650"),
        ("acs-law-sub-202601", "https://moda.gov.tw/ACS/laws/regulations/624"),
    ]
    for item_id, url in known_urls:
        html = fetch_html(url)
        if html and item_id in existing_map:
            soup = BeautifulSoup(html, "html.parser")
            h2 = soup.find("h2")
            if h2 and h2.get_text(strip=True):
                existing_map[item_id]["title"] = h2.get_text(strip=True)
        time.sleep(1.2)


def scan_agency_links(
    agency_code: str,
    agency_name: str,
    list_url: str,
    default_category: str,
    default_tags: list[str],
    existing_map: dict,
    today_str: str,
) -> None:
    html = fetch_html(list_url)
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")
    existing_urls = {v.get("source_url") for v in existing_map.values()}
    added = 0

    for a in soup.find_all("a", href=True):
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = a["href"].strip()
        if not text or len(text) < 6 or len(text) > 120:
            continue
        if not any(kw.lower() in text.lower() for kw in KEYWORDS):
            continue

        full_url = urljoin(list_url, href)
        if full_url in existing_urls or full_url.startswith("javascript:"):
            continue

        new_id = f"{agency_code.lower()}-auto-{abs(hash(full_url)) % 10**8}"
        existing_map[new_id] = {
            "id": new_id,
            "agency": agency_name,
            "agency_code": agency_code,
            "category": default_category,
            "domain_tags": default_tags,
            "title": text,
            "publish_date": today_str,
            "updated_date": today_str,
            "source_url": full_url,
            "summary": f"由排程自動自「{agency_name}」公告頁面擷取，請點擊原文連結確認完整說明與附件。",
            "highlights": [f"來源頁面：{list_url}"],
            "is_recent": True,
        }
        existing_urls.add(full_url)
        added += 1
        if added >= 5:
            break

    print(f"[INFO] {agency_code} ({list_url}) 新增 {added} 筆相關公告。")
    time.sleep(1.5)


def main() -> None:
    now_tw = datetime.now(TZ_TW)
    now_str = now_tw.strftime("%Y-%m-%d %H:%M")
    today_str = now_tw.strftime("%Y-%m-%d")

    try:
        with open("data.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
            items = raw.get("items", []) if isinstance(raw, dict) else raw
    except FileNotFoundError:
        items = []

    existing_map = {item["id"]: item for item in items if "id" in item}

    refresh_known_pages(existing_map)

    scan_targets = [
        ("PDPC", "個人資料保護專責機關／國發會", "https://www.pdpc.gov.tw/", "法令與子法", ["個資法", "IT"]),
        ("FSC", "金融監督管理委員會", "https://www.fsc.gov.tw/ch/home.jsp?id=1072&parentpath=0,7,478", "技術指引", ["IT", "金融資安"]),
        ("FSC", "金融監督管理委員會", "https://dsp.tpex.org.tw/web/listing/security.php", "防護基準", ["IT", "公司治理"]),
        ("PDPC", "個人資料保護專責機關／國發會", "https://www.ndc.gov.tw/", "法令與子法", ["個資法", "IT"]),
        ("NICS", "國家資通安全研究院", "https://www.nics.nat.gov.tw/", "防護基準", ["IT", "OT / 工控 / IoT"]),
    ]

    for code, name, url, cat, tags in scan_targets:
        scan_agency_links(code, name, url, cat, tags, existing_map, today_str)

    output = {
        "last_updated": now_str,
        "items": list(existing_map.values()),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] data.json 已更新（{now_str}），目前共 {len(output['items'])} 筆資料。")


if __name__ == "__main__":
    main()