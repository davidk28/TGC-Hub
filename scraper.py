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

SELF_DEFINED_HINTS = [
    "作業要點", "作業規範", "作業程序", "實施計畫", "維護計畫",
    "管理規定", "查核", "檢核", "自行評估", "內部控制", "委外"
]
RISK_GOV_HINTS = [
    "風險評鑑", "風險評估", "資產盤點", "資安", "資通安全",
    "個資", "個人資料", "弱點", "工控", "OT", "IoT", "稽核", "通報",
    "物聯網", "SCADA", "零信任", "防護基準", "施行細則"
]
CENTRAL_HUB_CODES = {"ACS", "NICS", "FSC", "PDPC"}


def score_and_classify_doc(title: str, agency_code: str) -> tuple[int, bool, str]:
    """回傳 (相關度分數, 是否為公部門自訂/衍生規範, 建議文件類型)"""
    t_lower = title.lower()
    score = 0
    for kw in RISK_GOV_HINTS:
        if kw.lower() in t_lower:
            score += 2

    is_self_defined = any(h.lower() in t_lower for h in SELF_DEFINED_HINTS) or (
        agency_code not in CENTRAL_HUB_CODES
    )
    if is_self_defined:
        score += 2

    if any(k in t_lower for k in ["工控", "ot", "iot", "物聯網", "scada"]):
        suggested_cat = "OT / 工控 / IoT"
    elif any(k in t_lower for k in ["法", "辦法", "細則", "要點"]):
        suggested_cat = "法令與子法"
    elif any(k in t_lower for k in ["基準", "控制"]):
        suggested_cat = "防護基準"
    elif any(k in t_lower for k in ["查核", "檢核", "範本", "手冊", "計畫", "程序"]):
        suggested_cat = "稽核與範本"
    else:
        suggested_cat = "技術指引"

    return score, is_self_defined, suggested_cat


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
    default_tags: list[str],
    existing_map: dict,
    today_str: str,
) -> None:
    html = fetch_html(list_url)
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")
    existing_urls = {v.get("source_url") for v in existing_map.values()}
    candidates = []

    for a in soup.find_all("a", href=True):
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        href = a["href"].strip()
        if not text or len(text) < 6 or len(text) > 120:
            continue
        full_url = urljoin(list_url, href)
        if full_url in existing_urls or full_url.startswith("javascript:"):
            continue

        score, is_self_defined, suggested_cat = score_and_classify_doc(text, agency_code)
        if score >= 2:
            candidates.append((score, text, full_url, is_self_defined, suggested_cat))

    candidates.sort(key=lambda x: x[0], reverse=True)
    added = 0
    for score, text, full_url, is_self_defined, suggested_cat in candidates[:5]:
        new_id = f"{agency_code.lower()}-auto-{abs(hash(full_url)) % 10**8}"
        tags = list(default_tags)
        if is_self_defined and "公部門自訂" not in tags:
            tags.append("公部門自訂")

        existing_map[new_id] = {
            "id": new_id,
            "agency": agency_name,
            "agency_code": agency_code,
            "category": suggested_cat,
            "domain_tags": tags,
            "is_agency_self_defined": is_self_defined,
            "title": text,
            "publish_date": today_str,
            "updated_date": today_str,
            "source_url": full_url,
            "summary": f"由排程自動自「{agency_name}」識別擷取（相關度評分 {score}），請點擊原文連結確認完整說明與附件。",
            "highlights": [
                f"來源頁面：{list_url}",
                "識別屬性：" + ("公部門自訂／衍生規範" if is_self_defined else "主管機關法規／通用指引")
            ],
            "is_recent": True,
        }
        existing_urls.add(full_url)
        added += 1

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

    # 確保既有項目具備 is_agency_self_defined 欄位
    for item in existing_map.values():
        if "is_agency_self_defined" not in item:
            _, inferred_self, _ = score_and_classify_doc(item.get("title", ""), item.get("agency_code", ""))
            item["is_agency_self_defined"] = inferred_self

    refresh_known_pages(existing_map)

    scan_targets = [
        ("PDPC", "個人資料保護專責機關／國發會", "https://www.pdpc.gov.tw/", ["個資法", "IT"]),
        ("FSC", "金融監督管理委員會", "https://www.fsc.gov.tw/ch/home.jsp?id=1072&parentpath=0,7,478", ["IT", "金融資安"]),
        ("FSC", "金融監督管理委員會", "https://dsp.tpex.org.tw/web/listing/security.php", ["IT", "公司治理"]),
        ("PDPC", "個人資料保護專責機關／國發會", "https://www.ndc.gov.tw/", ["個資法", "IT"]),
        ("NICS", "國家資通安全研究院", "https://www.nics.nat.gov.tw/", ["IT", "OT / 工控 / IoT"]),
        ("MOE", "教育部", "https://www.k12ea.gov.tw/", ["IT", "教育體系"]),
        ("MOHW", "衛生福利部", "https://www.mohw.gov.tw/", ["IT", "醫療OT設備"]),
    ]

    for code, name, url, tags in scan_targets:
        scan_agency_links(code, name, url, tags, existing_map, today_str)

    output = {
        "last_updated": now_str,
        "items": list(existing_map.values()),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] data.json 已更新（{now_str}），目前共 {len(output['items'])} 筆資料。")


if __name__ == "__main__":
    main()