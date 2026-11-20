import html
import re
import time as t
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
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

# 커넥션 풀 재사용 세션 (Keep-Alive)
SESSION = requests.Session()
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
})

MAX_WORKERS = 12

def clean_html_text(raw_str: str) -> str:
    if not raw_str:
        return ""
    return html.unescape(re.sub(r"<.*?>", "", raw_str)).strip()

def get_content(url: str, selector: str) -> str:
    try:
        res = SESSION.get(url, timeout=(2.5, 4))
        if res.status_code != 200:
            return ""
        soup = BeautifulSoup(res.text, "html.parser")
        content = soup.select_one(selector)
        if not content:
            return ""

        for junk in content.select(
            ".writer-zone, .writer-zone01, .byline, .related-zone, .rel-article, "
            ".comp-box, .img-con, .txt-copyright, .ad-box, figure, script, style"
        ):
            junk.decompose()

        text = content.get_text(separator="\n", strip=True)
        text = re.split(r"\n(?:관련 뉴스|제보는 카카오톡|<저작권자)", text)[0]
        return text.strip()
    except:
        return ""

def fetch_articles_concurrently(article_list, selector, selected_keywords, progress_callback=None, source_name=""):
    results = []
    total = len(article_list)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(get_content, art['url'], selector): art for art in article_list}
        for i, future in enumerate(as_completed(futures)):
            art = futures[future]
            try:
                content = future.result()
                if content:
                    full_search_text = f"{art['title']}\n{content}"
                    matched = [kw for kw in selected_keywords if kw in full_search_text]
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
            res = SESSION.get(url, timeout=(3, 5))
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
            res = SESSION.get(url, timeout=(3, 5))
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

def naver_parse_pubdate(pubdate_str):
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    except:
        return None

def naver_extract_title_and_body(url):
    try:
        res = SESSION.get(url, timeout=(2.5, 4))
        if res.status_code != 200:
            return None, None

        if res.encoding and res.encoding.lower() in ("iso-8859-1", "latin1"):
            res.encoding = res.apparent_encoding

        soup = BeautifulSoup(res.text, "html.parser")

        if "news.naver.com" in url:
            title_div = (
                soup.find("div", class_="media_end_head_title") or
                soup.find("h2", id="title_area") or
                soup.find("h3", id="articleTitle")
            )
            content_div = (
                soup.find("div", id="newsct_article") or
                soup.find("div", id="articeBody") or
                soup.find("div", id="articleBodyContents")
            )
            title = title_div.get_text(strip=True) if title_div else None
            body = content_div.get_text(separator="\n", strip=True) if content_div else None
            return title, body

        og_title = soup.find("meta", property="og:title")
        title_tag = soup.find("h1", class_="heading") or soup.find("h1") or soup.find("title")
        title = og_title.get("content", "").strip() if (og_title and og_title.get("content")) else (title_tag.get_text(strip=True) if title_tag else None)

        for junk in soup.select("script, style, nav, header, footer, aside, iframe, figure, .ad, .advertisement, .related, .copyright"):
            junk.decompose()

        candidates = [
            "#article-view-content-div", "#articleBody", "#article_body",
            "#news_body_area", ".article_view", ".article-body", ".article_body",
            ".view_con", ".news_cnt_detail_wrap", "[itemprop='articleBody']", "article"
        ]
        body = None
        for sel in candidates:
            node = soup.select_one(sel)
            if node:
                txt = node.get_text(separator="\n", strip=True)
                if len(txt) >= 80:
                    body = txt
                    break

        if not body:
            p_lines = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) >= 25]
            if p_lines:
                body = "\n".join(p_lines)

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
            res = SESSION.get(url, headers=headers, params=params, timeout=(3, 5))
            if res.status_code == 200:
                return res
            t.sleep(0.5)
        except:
            t.sleep(0.5)
    return res

def fetch_and_filter_naver(item_data):
    item, start_dt, end_dt, selected_keywords = item_data

    naver_link = item.get("link", "")
    orig_link = item.get("originallink", "")
    target_url = naver_link if "news.naver.com" in naver_link else (orig_link or naver_link)
    if not target_url:
        return None

    pub_dt = naver_parse_pubdate(item.get("pubDate"))
    if not pub_dt or not (start_dt <= pub_dt <= end_dt):
        return None

    api_title = clean_html_text(item.get("title", ""))
    api_desc = clean_html_text(item.get("description", ""))

    # 웹 접속 전 1차 필터링: API 제목에 '단독'이 아예 없는 기사(본문 단순 일치)는 불필요한 크롤링 생략
    if "단독" not in api_title:
        return None

    title, body = naver_extract_title_and_body(target_url)

    if not title:
        title = api_title
    if not body:
        body = api_desc

    if not title or not body:
        return None

    matched_keywords = []
    if selected_keywords:
        search_target = f"{title}\n{body}\n{api_desc}"
        matched_keywords = [kw for kw in selected_keywords if kw in search_target]
        if not matched_keywords:
            return None

    media = naver_extract_media_name(orig_link or target_url)
    base_title = api_title if api_title else title
    clean_title = re.sub(r"\[단독\]|\(단독\)|【단독】|ⓧ단독|^단독\s*[:-]?", "", base_title).strip()

    return {
        "매체": media,
        "title": clean_title,
        "raw_title": base_title,
        "datetime": pub_dt,
        "content": body,
        "url": target_url,
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

        # 이 페이지(100건)의 기사가 모두 start_dt보다 과거인지 확인 (조기 종료)
        page_has_valid_time = False
        for it in items:
            p_dt = naver_parse_pubdate(it.get("pubDate"))
            if p_dt and p_dt >= start_dt:
                page_has_valid_time = True
                break

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(fetch_and_filter_naver, (item, start_dt, end_dt, selected_keywords))
                for item in items
            ]
            for future in as_completed(futures):
                result = future.result()
                if result and result["url"] not in seen_links:
                    seen_links.add(result["url"])
                    all_articles.append(result)

        if not page_has_valid_time:
            break

    all_articles.sort(key=lambda x: x["datetime"], reverse=True)
    return all_articles
