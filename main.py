import os
import json
import queue
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from collector import KEYWORD_GROUPS, parse_yonhap, parse_newsis, parse_naver_exclusive
from summarizer import summarize_with_gemini

app = FastAPI(title="법조 보고 생성기 API")

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
    start_dt: str
    end_dt: str
    groups: List[str]

class SummarizeRequest(BaseModel):
    raw_report_text: str

@app.get("/api/groups")
def get_keyword_groups():
    return KEYWORD_GROUPS

@app.post("/api/collect_stream")
def collect_articles_stream(req: CollectRequest):
    try:
        tz = ZoneInfo("Asia/Seoul")
        s_dt = datetime.fromisoformat(req.start_dt).replace(tzinfo=tz)
        e_dt = datetime.fromisoformat(req.end_dt).replace(tzinfo=tz)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"날짜 형식 오류: {e}")

    selected_keywords = [kw for g in req.groups if g in KEYWORD_GROUPS for kw in KEYWORD_GROUPS[g]]
    q = queue.Queue()

    def run_collection():
        try:
            # 1단계: 연합뉴스 (전체 진행률의 0% ~ 33%)
            def cb_yonhap(ratio, msg):
                q.put({"type": "progress", "percent": int(ratio * 33), "message": msg})
            q.put({"type": "progress", "percent": 2, "message": "🔍 [연합뉴스] 기사 목록 탐색 중..."})
            yonhap = parse_yonhap(s_dt, e_dt, selected_keywords, progress_callback=cb_yonhap)

            # 2단계: 뉴시스 (전체 진행률의 33% ~ 66%)
            def cb_newsis(ratio, msg):
                q.put({"type": "progress", "percent": 33 + int(ratio * 33), "message": msg})
            q.put({"type": "progress", "percent": 35, "message": "🔍 [뉴시스] 기사 목록 탐색 중..."})
            newsis = parse_newsis(s_dt, e_dt, selected_keywords, progress_callback=cb_newsis)

            wire = yonhap + newsis
            wire.sort(key=lambda x: x["datetime"], reverse=True)

            # 3단계: 네이버 단독기사 (전체 진행률의 66% ~ 100%)
            def cb_naver(ratio, msg):
                q.put({"type": "progress", "percent": 66 + int(ratio * 34), "message": msg})
            q.put({"type": "progress", "percent": 68, "message": "🔍 [단독기사] 네이버 API 탐색 시작..."})
            naver = parse_naver_exclusive(s_dt, e_dt, selected_keywords, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, progress_callback=cb_naver)

            for art in wire:
                art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
                del art["datetime"]
            for art in naver:
                art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
                del art["datetime"]

            q.put({
                "type": "done",
                "percent": 100,
                "data": {
                    "wire": wire,
                    "naver": naver,
                    "counts": {
                        "yonhap": len(yonhap),
                        "newsis": len(newsis),
                        "wire_total": len(wire),
                        "naver": len(naver)
                    }
                }
            })
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=run_collection, daemon=True).start()

    def event_stream():
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )

@app.post("/api/summarize")
def summarize(req: SummarizeRequest):
    if not req.raw_report_text.strip():
        raise HTTPException(status_code=400, detail="요약할 본문이 없습니다.")
    try:
        summary = summarize_with_gemini(req.raw_report_text, GEMINI_API_KEYS)
        return {"summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
