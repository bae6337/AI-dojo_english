# sdp_server.py
# ─────────────────────────────────────────────────────────────────────────────
# Railway에 배포되는 FastAPI SDP 시그널링 서버.
# 브라우저의 WebRTC Offer를 받아 OpenAI Realtime API와 SDP 교환 후 Answer 반환.
#
# 실행 방법 (로컬):   uvicorn sdp_server:app --port 8016 --reload
# 실행 방법 (Railway): Procfile → web: uvicorn sdp_server:app --host 0.0.0.0 --port $PORT
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import urllib.request
import urllib.error

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from prompt_engine import build_instructions_from_dict

# .env 파일이 있으면 읽음 (로컬 개발용; 클라우드에선 환경변수로 주입)
load_dotenv()

app = FastAPI(title="AI English Dojo — SDP Server", version="1.0.0")

# ──────────────────────────────────────────────────────────────────────────────
# CORS: Streamlit Cloud 도메인에서 오는 POST 요청 허용
# ──────────────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 필요 시 ["https://xxx.streamlit.app"] 으로 좁힐 수 있음
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


# ──────────────────────────────────────────────────────────────────────────────
# 헬스체크 — UptimeRobot ping 대상 / Railway 헬스체크
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "AI English Dojo SDP Server"}


# ──────────────────────────────────────────────────────────────────────────────
# SDP 시그널링 엔드포인트
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/sdp")
async def handle_sdp(request: Request):
    """
    브라우저 WebRTC Offer → OpenAI Realtime API 세션 생성 → SDP Answer 반환.

    Body (JSON):
        sdp      : str  — 브라우저가 생성한 SDP offer 문자열
        settings : dict — 레벨/모드/속도 등 사용자 설정
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return JSONResponse({"error": "OPENAI_API_KEY not configured"}, status_code=500)

    settings     = data.get("settings", {})
    sdp_offer    = data.get("sdp", "")
    target_speed = settings.get("target_speed", settings.get("audio_speed", 1.0))

    # ── 1. AI 지시문 생성 ──────────────────────────────────────────────────────
    generated_instructions = build_instructions_from_dict(settings, target_speed)

    # ── 2. OpenAI Realtime 세션 생성 (임시 토큰 획득) ─────────────────────────
    session_url     = "https://api.openai.com/v1/realtime/sessions"
    session_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    session_payload = {
        "model":       settings.get("model", "gpt-4o-mini-realtime-preview"),
        "instructions": generated_instructions,
        "modalities":  ["text", "audio"],
        "voice":       "alloy",
        "input_audio_transcription": {"model": "whisper-1"},
    }

    # 미사일 모드: 수동 VAD (서버 VAD 끔)
    if settings.get("is_missile_mode", False):
        session_payload["turn_detection"] = None
        print("[CONFIG] Missile Mode ON → turn_detection DISABLED (Manual VAD)")
    else:
        session_payload["turn_detection"] = {
            "type":               "server_vad",
            "threshold":          0.5,
            "prefix_padding_ms":  300,
            "silence_duration_ms": 500,
        }

    print(f"\n=== GENERATED INSTRUCTIONS ===")
    print(f"[SPEED]  target_speed: {target_speed}x")
    print(f"[LEVEL]  {settings.get('level', 'Unknown')}")
    print(f"[MODEL]  {session_payload['model']}")
    print(f"==============================\n")

    try:
        req = urllib.request.Request(
            session_url,
            data=json.dumps(session_payload).encode("utf-8"),
            headers=session_headers,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            session_data = json.loads(resp.read().decode("utf-8"))
            token = session_data["client_secret"]["value"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[ERROR] OpenAI session creation failed: {e.code} {body}")
        return JSONResponse({"error": f"OpenAI session error: {e.code} {body}"}, status_code=502)
    except Exception as e:
        print(f"[ERROR] Session creation exception: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # ── 3. WebRTC SDP 교환 ────────────────────────────────────────────────────
    realtime_url = (
        f"https://api.openai.com/v1/realtime"
        f"?model={session_payload['model']}"
    )
    sdp_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/sdp",
    }

    try:
        req = urllib.request.Request(
            realtime_url,
            data=sdp_offer.encode("utf-8"),
            headers=sdp_headers,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            sdp_answer = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[ERROR] SDP exchange failed: {e.code} {body}")
        return JSONResponse({"error": f"SDP exchange error: {e.code} {body}"}, status_code=502)
    except Exception as e:
        print(f"[ERROR] SDP exchange exception: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # ── 4. Answer 반환 ────────────────────────────────────────────────────────
    return JSONResponse({
        "sdp":          sdp_answer,
        "instructions": session_payload["instructions"],
    })
