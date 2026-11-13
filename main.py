import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from collector import KEYWORD_GROUPS, parse_yonhap, parse_newsis, parse_naver_exclusive
from summarizer import summarize_with_gemini

app = FastAPI(title="법조 보고 생성기 API")

# GitHub Pages 웹페이지에서의 접속 허용 (CORS 해제)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "R7Q2OeVNhj8wZtNNFBwL")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "49E810CBKY")
raw_keys = os.environ.get("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEY", ""))
GEMINI_API_KEYS = [k.strip() for k in str(raw_keys).split(",") if k.strip()]

class CollectRequest(BaseModel):
    start_dt: str  # ISO 포맷: "2026-11-13T00:00"
    end_dt: str    # ISO 포맷: "2026-11-13T09:00"
    groups: List[str]

class SummarizeRequest(BaseModel):
    raw_report_text: str

@app.get("/api/groups")
def get_keyword_groups():
    return KEYWORD_GROUPS

@app.post("/api/collect")
def collect_articles(req: CollectRequest):
    try:
        tz = ZoneInfo("Asia/Seoul")
        s_dt = datetime.fromisoformat(req.start_dt).replace(tzinfo=tz)
        e_dt = datetime.fromisoformat(req.end_dt).replace(tzinfo=tz)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"날짜 형식 오류: {e}")

    selected_keywords = [kw for g in req.groups if g in KEYWORD_GROUPS for kw in KEYWORD_GROUPS[g]]

    yonhap = parse_yonhap(s_dt, e_dt, selected_keywords)
    newsis = parse_newsis(s_dt, e_dt, selected_keywords)
    wire = yonhap + newsis
    wire.sort(key=lambda x: x["datetime"], reverse=True)

    naver = parse_naver_exclusive(s_dt, e_dt, selected_keywords, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)

    # JSON 직렬화를 위해 datetime 객체를 문자열로 변환
    for art in wire:
        art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
        del art["datetime"]
    for art in naver:
        art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
        del art["datetime"]

    return {
        "wire": wire,
        "naver": naver,
        "counts": {
            "yonhap": len(yonhap),
            "newsis": len(newsis),
            "wire_total": len(wire),
            "naver": len(naver)
        }
    }

@app.post("/api/summarize")
def summarize(req: SummarizeRequest):
    if not req.raw_report_text.strip():
        raise HTTPException(status_code=400, detail="요약할 본문이 없습니다.")
    try:
        summary = summarize_with_gemini(req.raw_report_text, GEMINI_API_KEYS)
        return {"summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
