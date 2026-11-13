import os
import uuid
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

JOBS: Dict[str, Dict[str, Any]] = {}

def cleanup_old_jobs():
    now_ts = time.time()
    expired = [jid for jid, job in JOBS.items() if now_ts - job.get("created_at", 0) > 1800]
    for jid in expired:
        JOBS.pop(jid, None)

class CollectRequest(BaseModel):
    start_dt: str
    end_dt: str
    groups: List[str]

class SummarizeRequest(BaseModel):
    raw_report_text: str

@app.get("/api/groups")
def get_keyword_groups():
    return KEYWORD_GROUPS

@app.post("/api/collect_start")
def start_collect_job(req: CollectRequest):
    cleanup_old_jobs()
    try:
        tz = ZoneInfo("Asia/Seoul")
        s_dt = datetime.fromisoformat(req.start_dt).replace(tzinfo=tz)
        e_dt = datetime.fromisoformat(req.end_dt).replace(tzinfo=tz)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"날짜 형식 오류: {e}")

    selected_keywords = [kw for g in req.groups if g in KEYWORD_GROUPS for kw in KEYWORD_GROUPS[g]]
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "percent": 2,
        "message": "🔍 [연합뉴스] 기사 목록 탐색 중...",
        "data": None,
        "error": None,
        "created_at": time.time()
    }

    def run_task():
        try:
            def cb_yonhap(ratio, msg):
                JOBS[job_id]["percent"] = max(2, int(ratio * 33))
                JOBS[job_id]["message"] = msg
            yonhap = parse_yonhap(s_dt, e_dt, selected_keywords, progress_callback=cb_yonhap)

            def cb_newsis(ratio, msg):
                JOBS[job_id]["percent"] = 33 + int(ratio * 33)
                JOBS[job_id]["message"] = msg
            JOBS[job_id]["percent"] = 35
            JOBS[job_id]["message"] = "🔍 [뉴시스] 기사 목록 탐색 중..."
            newsis = parse_newsis(s_dt, e_dt, selected_keywords, progress_callback=cb_newsis)

            wire = yonhap + newsis
            wire.sort(key=lambda x: x["datetime"], reverse=True)

            def cb_naver(ratio, msg):
                JOBS[job_id]["percent"] = 66 + int(ratio * 34)
                JOBS[job_id]["message"] = msg
            JOBS[job_id]["percent"] = 68
            JOBS[job_id]["message"] = "🔍 [단독기사] 네이버 API 탐색 시작..."
            naver = parse_naver_exclusive(s_dt, e_dt, selected_keywords, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, progress_callback=cb_naver)

            for art in wire:
                art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
                del art["datetime"]
            for art in naver:
                art["datetime_str"] = art["datetime"].strftime("%Y-%m-%d %H:%M")
                del art["datetime"]

            JOBS[job_id]["data"] = {
                "wire": wire,
                "naver": naver,
                "counts": {
                    "yonhap": len(yonhap),
                    "newsis": len(newsis),
                    "wire_total": len(wire),
                    "naver": len(naver)
                }
            }
            JOBS[job_id]["percent"] = 100
            JOBS[job_id]["message"] = "✅ 모든 수집 완료!"
            JOBS[job_id]["status"] = "done"
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)

    threading.Thread(target=run_task, daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/job_status/{job_id}")
def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job

@app.get("/api/job_keepalive/{job_id}")
def keep_cpu_alive_until_done(job_id: str):
    """요청 처리 중에만 CPU를 할당하는 무료 모드에서 작업 완료 시까지 CPU를 깨워두는 롱폴링 엔드포인트"""
    start_wait = time.time()
    while time.time() - start_wait < 120:
        job = JOBS.get(job_id)
        if not job or job["status"] in ("done", "error"):
            return {"status": job["status"] if job else "not_found"}
        time.sleep(0.5)
    return {"status": "timeout"}

@app.post("/api/summarize_start")
def start_summarize_job(req: SummarizeRequest):
    cleanup_old_jobs()
    if not req.raw_report_text.strip():
        raise HTTPException(status_code=400, detail="요약할 본문이 없습니다.")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "running",
        "summary": None,
        "error": None,
        "created_at": time.time()
    }

    def run_sum():
        try:
            summary = summarize_with_gemini(req.raw_report_text, GEMINI_API_KEYS)
            JOBS[job_id]["summary"] = summary
            JOBS[job_id]["status"] = "done"
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)

    threading.Thread(target=run_sum, daemon=True).start()
    return {"job_id": job_id}
