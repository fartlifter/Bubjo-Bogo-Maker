import html
import re
import time as t
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import httpx
from bs4 import BeautifulSoup

KEYWORD_GROUPS = {
    '법원': ['서울중앙지법','서울고법','대법원','헌법재판소','대한변호사협회','서울지방변호사회','한국여성변호사회',
          '서울행정법원','서울가정법원','서울회생법원','법원행정처','특허법원','법무법인'],
    '검찰': ['서울중앙지검','서울고검','대검찰청','서울중앙지방공소청','서울광역공소청','법무부','특검','고위공직자범죄수사처','합동수사본부','중대범죄수사청','공소청','검찰','법제처']
}

MEDIA_MAPPING = {
    "chosun": "조선", "joongang": "중앙", "donga": "동아", "hani": "한겨레",
    "khan": "경향", "hankookilbo": "한국", "segye": "세계", "seoul": "서울",
    "kmib": "국민", "munhwa": "문화", "kbs": "KBS", "sbs": "SBS", "mbn.co": "MBN",
    "imnews": "MBC", "jtbc": "JTBC", "ichannela": "채널A", "tvchosun": "TV조선",
    "mk": "매경", "sedaily": "서경", "hankyung": "한경", "news1": "뉴스1", "www.pressian": "프레시안",
    "newsis": "뉴시스", "yna": "연합", "mt": "머투", "weekly": "주간조선", "www.imaeil": "매일신문",
    "biz.chosun": "조선비즈", "fnnews": "파뉴", "etoday.co": "이투데이", "edaily.co": "이데일리", "tf.co": "더팩트",
    "yonhapnewstv.co": "연뉴TV", "ytn.co": "YTN", "nocutnews.co": "노컷", "asiae.co": "아경", "biz.heraldcorp": "헤경",
    "www.sisajournal": "시사저널", "sisajournal.com": "시사저널", "www.ohmynews": "오마이",
    "dailian.co": "데일리안", "ilyo.co": "일요신문", "sisain.co": "시사IN", "lawtimes": "법률신문"
}

def get_content(url: str, selector: str) -> str:
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code != 200:
            return ""
        soup = BeautifulSoup(res.text, "html.parser")
        content = soup.select_one(selector)
        return content.get_text(separator="\n", strip=True) if content else ""
    except:
        return ""

def fetch_articles_concurrently(article_list, selector, selected_keywords, progress_callback=None, source_name=""):
    results = []
    total = len(article_list)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(get_content, art['url'], selector): art for art in article_list}
        for i, future in enumerate(as_completed(futures)):
            art = futures[future]
            try:
                content = future.result()
                if content:
                    matched = [kw for kw in selected_keywords if kw in content]
                    if not selected_keywords or matched:
                        art['content'] = content
                        art['matched_kw'] = matched
                        results.append(art)
            except:
                pass
            if progress_callback:
                progress_callback((i + 1) / total, f"[{source_name}] 본문 키워드 대조 중... ({i+1}/{total}건)")

    results.sort(key=lambda x: x["datetime"], reverse=True)
    return results

def parse_yonhap(start_dt, end_dt, selected_keywords, progress_callback=None):
    collected = []
    page = 1
    past_streak = 0
    MAX_PAST_STREAK = 5

    if progress_callback:
        progress_callback(0.0, "🔍 [연합뉴스] 기사 목록 탐색 중...")

    while True:
        url = f"https://www.yna.co.kr/society/all/{page}"
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if res.status_code != 200:
                break
        except:
            break

        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.select("ul.list01 > li[data-cid]")
        if not items:
            break

        for item in items:
            cid = item.get("data-cid")
            title_tag = item.select_one(".title01")
            time_tag = item.select_one(".txt-time")
            if not (cid and title_tag and time_tag):
                continue

            try:
                time_str = time_tag.text.strip()
                dt = datetime.strptime(f"{start_dt.year}-{time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul"))
            except:
                continue

            if dt < start_dt:
                past_streak += 1
                if past_streak >= MAX_PAST_STREAK:
                    return fetch_articles_concurrently(collected, "div.story-news.article", selected_keywords, progress_callback, "연합뉴스")
                continue
            else:
                past_streak = 0

            if start_dt <= dt <= end_dt:
                collected.append({
                    "source": "연합뉴스",
                    "datetime": dt,
                    "title": title_tag.text.strip(),
                    "url": f"https://www.yna.co.kr/view/{cid}"
                })

        page += 1

    return fetch_articles_concurrently(collected, "div.story-news.article", selected_keywords, progress_callback, "연합뉴스")

def parse_newsis(start_dt, end_dt, selected_keywords, progress_callback=None):
    collected = []
    page = 1
    past_streak = 0
    MAX_PAST_STREAK = 5

    if progress_callback:
        progress_callback(0.0, "🔍 [뉴시스] 기사 목록 탐색 중...")

    while True:
        url = f"https://www.newsis.com/society/list/?cid=10200&scid=10201&page={page}"
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if res.status_code != 200:
                break
        except:
            break

        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.select("ul.articleList2 > li")
        if not items:
            break

        for item in items:
            title_tag = item.select_one("p.tit > a")
            time_tag = item.select_one("p.time")
            if not (title_tag and time_tag):
                continue

            match = re.search(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}", time_tag.text)
            if not match:
                continue

            dt = datetime.strptime(match.group(), "%Y.%m.%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Seoul"))

            if dt < start_dt:
                past_streak += 1
                if past_streak >= MAX_PAST_STREAK:
                    return fetch_articles_concurrently(collected, "div.viewer", selected_keywords, progress_callback, "뉴시스")
                continue
            else:
                past_streak = 0

            if start_dt <= dt <= end_dt:
                collected.append({
                    "source": "뉴시스",
                    "datetime": dt,
                    "title": title_tag.get_text(strip=True),
                    "url": "https://www.newsis.com" + title_tag.get("href", "")
                })

        page += 1

    return fetch_articles_concurrently(collected, "div.viewer", selected_keywords, progress_callback, "뉴시스")

# === 네이버 단독기사: 과거 코드.py의 검증된 로직 100% 이식 ===
def naver_parse_pubdate(pubdate_str):
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def naver_extract_title_and_body(url):
    try:
        if "n.news.naver.com" not in url and "lawtimes.co.kr" not in url:
            return None, None
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code != 200:
            return None, None
        soup = BeautifulSoup(res.text, "html.parser")
        if "lawtimes.co.kr" in url:
            title_div = soup.find("h1", class_="heading")
            content_div = soup.find("article", id="article-view-content-div")
        else:
            title_div = soup.find("div", class_="media_end_head_title")
            content_div = soup.find("div", id="newsct_article")
        title = title_div.get_text(strip=True) if title_div else None
        body = content_div.get_text(separator="\n", strip=True) if content_div else None
        return title, body
    except:
        return None, None

def naver_extract_media_name(url):
    try:
        domain = url.split("//")[-1].split("/")[0]
        parts = domain.split(".")
        composite_key = f"{parts[-3]}.{parts[-2]}" if len(parts) >= 3 else parts[0]
        if composite_key in MEDIA_MAPPING:
            return MEDIA_MAPPING[composite_key]
        for part in reversed(parts):
            if part in MEDIA_MAPPING:
                return MEDIA_MAPPING[part]
        return composite_key.upper()
    except:
        return "기타"

def safe_api_request(url, headers, params, max_retries=3):
    res = None
    for _ in range(max_retries):
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                return res
            t.sleep(0.5)
        except:
            t.sleep(0.5)
    return res

def fetch_and_filter_naver(item_data):
    item, start_dt, end_dt, selected_keywords = item_data
    link = item.get("link")
    if not link or ("n.news.naver.com" not in link and "lawtimes.co.kr" not in link):
        return None

    # 시간대를 먼저 확인해 불필요한 웹 요청을 줄이되, 시간대 안이면 무조건 원문 제목/본문 확인
    pub_dt = naver_parse_pubdate(item.get("pubDate"))
    if not pub_dt or not (start_dt <= pub_dt <= end_dt):
        return None

    title, body = naver_extract_title_and_body(link)
    if not title or not body:
        return None

    # 과거 코드와 동일하게 직관적인 포함 여부로 검사 (따옴표 붙은 제목 누락 원천 차단)
    if not any(tag in title for tag in ["[단독]", "(단독)", "【단독】", "ⓧ단독", "단독"]):
        return None

    matched_keywords = []
    if selected_keywords:
        matched_keywords = [kw for kw in selected_keywords if kw in body]
        if not matched_keywords:
            return None

    media = naver_extract_media_name(item.get("originallink", ""))
    clean_title = re.sub(r"\[단독\]|\(단독\)|【단독】|ⓧ단독|^단독\s*[:-]?", "", title).strip()

    return {
        "매체": media,
        "title": clean_title,
        "raw_title": title,
        "datetime": pub_dt,
        "content": body,
        "url": link,
        "matched_kw": matched_keywords
    }

def parse_naver_exclusive(start_dt, end_dt, selected_keywords, client_id, client_secret, progress_callback=None):
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }
    seen_links = set()
    all_articles = []

    steps = list(range(1, 1001, 100))
    total_steps = len(steps)

    for idx, start_index in enumerate(steps, 1):
        if progress_callback:
            progress_callback(idx / total_steps, f"🟡 [단독기사] 수집 중... (현재 {len(all_articles)}건 수집됨)")

        params = {
            "query": "[단독]",
            "sort": "date",
            "display": 100,
            "start": start_index
        }
        res = safe_api_request("https://openapi.naver.com/v1/search/news.json", headers, params)
        if not res or res.status_code != 200:
            break
        items = res.json().get("items", [])
        if not items:
            break

        with ThreadPoolExecutor(max_workers=25) as executor:
            futures = [
                executor.submit(fetch_and_filter_naver, (item, start_dt, end_dt, selected_keywords))
                for item in items
            ]
            for future in as_completed(futures):
                result = future.result()
                if result and result["url"] not in seen_links:
                    seen_links.add(result["url"])
                    all_articles.append(result)

    all_articles.sort(key=lambda x: x["datetime"], reverse=True)
    return all_articles
