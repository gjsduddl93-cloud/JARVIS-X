from flask import Flask, render_template, request, session, jsonify
from anthropic import Anthropic
from openai import OpenAI
import httpx
import threading
import uuid

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    GOOGLE_AVAILABLE = True
except ImportError as _google_import_err:
    print(f"[WARN] Google 패키지 미설치, YouTube 기능 비활성화: {_google_import_err}")
    Credentials = None
    Request = None
    Flow = None
    build = None
    HttpError = Exception
    MediaFileUpload = None
    GOOGLE_AVAILABLE = False

from dotenv import load_dotenv
from datetime import datetime
import os
import re
import json
import traceback
import requests
import subprocess

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jarvis_x_secret_key")

MAX_HISTORY = 20

# ── API 클라이언트 초기화 ─────────────────────────────────────────────────────
_timeout = httpx.Timeout(30.0, connect=10.0)

try:
    claude_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=_timeout)
    print("[INFO] Anthropic 클라이언트 초기화 성공")
except Exception as _e:
    print(f"[WARN] Anthropic 클라이언트 초기화 실패: {_e}")
    claude_client = None

try:
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=_timeout)
    print("[INFO] OpenAI 클라이언트 초기화 성공")
except Exception as _e:
    print(f"[WARN] OpenAI 클라이언트 초기화 실패: {_e}")
    openai_client = None

# ── 디렉터리 생성 ─────────────────────────────────────────────────────────────
PROJECTS_DIR = "projects"
VIDEOS_DIR   = os.path.join(PROJECTS_DIR, "videos")
AUDIO_DIR    = os.path.join(PROJECTS_DIR, "audio")
IMAGES_DIR   = os.path.join(PROJECTS_DIR, "images")

for _dir in [PROJECTS_DIR, VIDEOS_DIR, AUDIO_DIR, IMAGES_DIR]:
    os.makedirs(_dir, exist_ok=True)

# ── 백그라운드 작업 저장소 ────────────────────────────────────────────────────
# { job_id: {status, logs, content, video_path, youtube, error, created_at} }
_jobs: dict = {}
_jobs_lock = threading.Lock()

def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None

def _update_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)

def _append_log(job_id: str, msg: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["logs"].append(msg)
    print(f"[JOB {job_id[:8]}] {msg}")

def _cleanup_old_jobs() -> None:
    """100개 초과 시 오래된 작업 정리"""
    with _jobs_lock:
        if len(_jobs) > 100:
            old = sorted(_jobs, key=lambda k: _jobs[k]["created_at"])[:50]
            for k in old:
                del _jobs[k]

# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
당신은 JARVIS-X이다.

사용자의 목표:
- AI 콘텐츠 자동화로 월 10~50만원 부수입 만들기
- 유튜브 쇼츠, 인스타 릴스, 틱톡, 블로그 동시 활용
- 장기적으로 더 큰 자동화 수익 시스템 구축

콘텐츠 방향:
- 40% AI 활용법
- 30% 돈 버는 방법/부업/AI 수익화
- 30% 해외 신기한 사실/미스터리/흥미로운 정보

답변 규칙:
1. 항상 한국어로 답변
2. 기본 답변은 3~5줄 이내
3. 사용자가 자세히 요청할 때만 길게 설명
4. 목록은 최대 5개
5. 실행 가능한 내용만 말하기
6. JSON 형식 요청시 정확한 JSON만 반환
"""

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_FILE = "youtube_token.json"

# Render 환경변수에서 YouTube 토큰 로드 (매 시작마다 덮어쓰기 — 에피머럴 파일시스템)
_yt_token_env = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
if _yt_token_env:
    try:
        _yt_token_data = json.loads(_yt_token_env)   # JSON 유효성 먼저 확인
        with open(TOKEN_FILE, "w", encoding="utf-8") as _f:
            json.dump(_yt_token_data, _f)
        print(f"[INFO] YouTube 토큰 환경변수에서 로드 완료: {TOKEN_FILE}")
        print(f"[INFO] 토큰 필드: {list(_yt_token_data.keys())}")
    except json.JSONDecodeError as _e:
        print(f"[ERROR] YOUTUBE_TOKEN_JSON 환경변수가 유효하지 않은 JSON: {_e}")
    except Exception as _e:
        print(f"[ERROR] YouTube 토큰 파일 저장 실패: {_e}")
elif os.path.exists(TOKEN_FILE):
    print(f"[INFO] YouTube 토큰 파일 존재 (env var 없음): {TOKEN_FILE}")
else:
    print("[WARN] YouTube 토큰 없음 — YOUTUBE_TOKEN_JSON 환경변수를 Render에 설정하세요")


# ── AI 호출 ──────────────────────────────────────────────────────────────────

def ask_claude(user_prompt, max_tokens=1024):
    if not claude_client:
        print("[WARN] ask_claude: ANTHROPIC_API_KEY 미설정")
        return None
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        print(f"[ERROR] ask_claude 실패: {e}")
        print(traceback.format_exc())
        return None


def ask_chatgpt(user_prompt, max_tokens=1024):
    if not openai_client:
        print("[WARN] ask_chatgpt: OPENAI_API_KEY 미설정")
        return None
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=max_tokens,
            temperature=0.7
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[ERROR] ask_chatgpt 실패: {e}")
        print(traceback.format_exc())
        return None


def ask_ai(user_prompt, max_tokens=1024, prefer_claude=True):
    """Claude 우선, 실패 시 ChatGPT 자동 전환"""
    if prefer_claude:
        result = ask_claude(user_prompt, max_tokens)
        return result if result else ask_chatgpt(user_prompt, max_tokens)
    else:
        result = ask_chatgpt(user_prompt, max_tokens)
        return result if result else ask_claude(user_prompt, max_tokens)


# ── 이미지 / 영상 생성 ────────────────────────────────────────────────────────


def _find_korean_font():
    """시스템에서 한글 지원 폰트 경로 반환. 없으면 None."""
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/malgun.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            return fp
    return None


def _text_wrap(text, width):
    """텍스트를 width 글자 단위로 줄바꿈 (\\n 구분)."""
    lines = [text[i:i+width] for i in range(0, len(text), width)]
    return r"\n".join(lines)


def _ffmpeg_escape(text):
    """FFmpeg drawtext 필터용 텍스트 이스케이프."""
    return (text
            .replace("\\", "\\\\")
            .replace("'",  "\\'")
            .replace(":",  "\\:")
            .replace("[",  "\\[")
            .replace("]",  "\\]")
            .replace("%",  "\\%"))


def _ascii_only(text):
    """ASCII 출력 가능한 문자만 남김 (내장 폰트용)."""
    return "".join(c for c in text if 32 <= ord(c) < 127)


def _find_font():
    """직접 경로로 폰트 파일 탐색 (fontconfig 스캔 없음)."""
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            print(f"[FONT] 발견: {p}")
            return p
    print("[FONT] 사용 가능한 폰트 없음")
    return None


def _build_drawtext_vf(font_path, title, narration):
    """fontfile 지정 drawtext VF (한국어 지원)."""
    fp = font_path.replace("\\", "/")   # Windows 경로 보정
    parts = [
        f"drawtext=fontfile='{fp}'"
        f":text='{_ffmpeg_escape(title[:35])}'"
        f":fontsize=72:fontcolor=white"
        f":x=(w-text_w)/2:y=280"
        f":box=1:boxcolor=black@0.6:boxborderw=12",
    ]
    lines = [narration[i:i+22] for i in range(0, min(len(narration), 110), 22)]
    for idx, line in enumerate(lines):
        parts.append(
            f"drawtext=fontfile='{fp}'"
            f":text='{_ffmpeg_escape(line)}'"
            f":fontsize=48:fontcolor=0xccddff"
            f":x=(w-text_w)/2:y={460 + idx * 90}"
            f":box=1:boxcolor=black@0.4:boxborderw=8"
        )
    return ",".join(parts)


def _build_ascii_drawtext_vf(title, narration):
    """폰트 없이 FFmpeg 내장 폰트 사용 (ASCII 전용)."""
    atitle = _ascii_only(title) or "JARVIS-X Auto Video"
    anarr  = _ascii_only(narration) or "AI Generated Content"
    parts  = [
        f"drawtext=text='{_ffmpeg_escape(atitle[:40])}'"
        f":fontsize=72:fontcolor=white"
        f":x=(w-text_w)/2:y=280"
        f":box=1:boxcolor=black@0.6:boxborderw=12",
    ]
    lines = [anarr[i:i+30] for i in range(0, min(len(anarr), 120), 30)]
    for idx, line in enumerate(lines):
        parts.append(
            f"drawtext=text='{_ffmpeg_escape(line)}'"
            f":fontsize=48:fontcolor=0xccddff"
            f":x=(w-text_w)/2:y={460 + idx * 90}"
            f":box=1:boxcolor=black@0.4:boxborderw=8"
        )
    return ",".join(parts)


def _run_ffmpeg(cmd, log_path, timeout=90):
    """FFmpeg 실행 (Popen+파일 로그). 반환값: (returncode, log_content)."""
    print(f"[FFMPEG CMD] {' '.join(cmd)}")   # 전체 명령어 로그
    with open(log_path, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, close_fds=True)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        print("[FFMPEG] TIMEOUT")
        return -1, "TIMEOUT"
    try:
        with open(log_path, encoding="utf-8", errors="replace") as lf:
            content = lf.read()
        # 에러 줄만 추출해서 요약 출력
        error_lines = [l for l in content.splitlines() if "Error" in l or "error" in l or "Invalid" in l]
        if error_lines:
            print(f"[FFMPEG ERR] {chr(10).join(error_lines[:8])}")
        print(f"[FFMPEG] rc={rc}, log_size={len(content)}B")
        return rc, content
    except Exception:
        return rc, "(로그 읽기 실패)"


def create_simple_video(content_data):
    """FFmpeg 1080×1920 쇼츠 영상 생성. 3단계 텍스트 오버레이 전략."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_path = os.path.join(VIDEOS_DIR, f"video_{ts}.mp4")
        log_path   = os.path.join(os.path.abspath(IMAGES_DIR), f"ffmpeg_{ts}.log")

        try:
            chk = subprocess.run(["ffmpeg", "-version"],
                                 capture_output=True, text=True, timeout=10)
            if chk.returncode != 0:
                print("[ERROR] ffmpeg 응답 비정상")
                return None
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[ERROR] ffmpeg 실행 불가: {e}")
            return None

        title      = content_data.get("title", "JARVIS-X")
        narration  = content_data.get("narration", "")
        # 영문 텍스트 — FFmpeg 내장 폰트(ASCII)로 항상 표시 가능
        title_en   = content_data.get("title_en", "") or _ascii_only(title) or "JARVIS-X"
        narr_en    = content_data.get("narration_en", "") or _ascii_only(narration) or "AI Generated Content"
        font_path  = _find_font()

        print(f"[VIDEO] title_en={title_en!r}, narr_en_len={len(narr_en)}")
        print(f"[VIDEO] font_path={font_path}")

        base_in  = ["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "color=c=0x0d0d1a:size=1080x1920:rate=24"]
        base_out = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-t", "10", "-pix_fmt", "yuv420p", "-threads", "1", video_path]

        def _try(label, vf):
            if os.path.exists(video_path):
                os.remove(video_path)
            cmd = base_in + ["-vf", vf] + base_out
            print(f"[VIDEO] 전략 시도: {label}")
            rc, log = _run_ffmpeg(cmd, log_path)
            ok = rc == 0 and os.path.exists(video_path) and os.path.getsize(video_path) > 1000
            if ok:
                print(f"[VIDEO] 성공 ({label}): {os.path.getsize(video_path)}B")
            else:
                print(f"[VIDEO] 실패 ({label}) rc={rc}")
                if os.path.exists(video_path):
                    os.remove(video_path)
            return ok

        # 전략 1: 내장 폰트 drawtext, 영문 텍스트 (폰트 설치 불필요 — 항상 동작)
        if _try("ascii-builtin", _build_ascii_drawtext_vf(title_en, narr_en)):
            return video_path

        # 전략 2: fontfile 지정 drawtext, 한국어 (NanumGothic 있을 때만)
        if font_path and _try("korean-fontfile",
                              _build_drawtext_vf(font_path, title, narration)):
            return video_path

        # 전략 3: 단색 배경 폴백
        print("[VIDEO] 단색 배경 폴백...")
        if os.path.exists(video_path):
            os.remove(video_path)
        rc, _ = _run_ffmpeg(base_in + base_out, log_path)
        if rc == 0 and os.path.exists(video_path):
            print(f"[VIDEO] 단색 완료: {os.path.getsize(video_path)}B")
            return video_path

        print("[ERROR] 모든 FFmpeg 전략 실패")
        return None

    except Exception as e:
        print(f"[ERROR] create_simple_video 예외: {e}")
        print(traceback.format_exc())
        return None


# ── YouTube ──────────────────────────────────────────────────────────────────

def get_youtube_service():
    if not GOOGLE_AVAILABLE:
        print("[WARN] get_youtube_service: Google 패키지 미설치")
        return None, "google_packages_not_installed"
    try:
        if not os.path.exists(TOKEN_FILE):
            print(f"[ERROR] get_youtube_service: 토큰 파일 없음 ({TOKEN_FILE})")
            return None, "token_file_not_found"

        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)
        print(f"[INFO] get_youtube_service: valid={creds.valid}, expired={creds.expired}, "
              f"has_refresh_token={bool(creds.refresh_token)}")

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                print("[INFO] get_youtube_service: 액세스 토큰 만료 → 갱신 중...")
                creds.refresh(Request())
                # 갱신된 토큰을 파일에 저장 (세션 내 재사용)
                with open(TOKEN_FILE, "w", encoding="utf-8") as _f:
                    _f.write(creds.to_json())
                print("[INFO] get_youtube_service: 토큰 갱신 완료 및 저장")
            else:
                print(f"[ERROR] get_youtube_service: 재인증 필요 "
                      f"(expired={creds.expired}, has_refresh={bool(creds.refresh_token)})")
                return None, "auth_required"

        svc = build("youtube", "v3", credentials=creds)
        print("[INFO] get_youtube_service: YouTube API 서비스 초기화 성공")
        return svc, None
    except Exception as e:
        print(f"[ERROR] get_youtube_service 예외: {e}")
        print(traceback.format_exc())
        return None, str(e)


def upload_to_youtube(video_path, title, description, tags):
    if not GOOGLE_AVAILABLE:
        return {"status": "skipped", "message": "google_packages_not_installed"}

    file_size = os.path.getsize(video_path) if os.path.exists(video_path) else -1
    print(f"[INFO] upload_to_youtube: 시작 — title={title[:40]!r}, "
          f"file={video_path}, size={file_size}B")

    if file_size < 0:
        return {"status": "error", "message": f"영상 파일 없음: {video_path}"}

    try:
        svc, err = get_youtube_service()
        if not svc:
            status = "auth_required" if err in (
                "auth_required", "token_file_not_found") else "error"
            return {"status": status, "message": err}

        body = {
            "snippet": {
                "title":       title[:100],
                "description": description[:5000],
                "tags":        (tags or [])[:30],
                "categoryId":  "22",
                "defaultLanguage": "ko",
            },
            "status": {
                "privacyStatus":          "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        print(f"[INFO] upload_to_youtube: body snippet.title={body['snippet']['title']!r}")
        media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True,
                                chunksize=5 * 1024 * 1024)
        yt_req = svc.videos().insert(part="snippet,status", body=body, media_body=media)
        print("[INFO] upload_to_youtube: API 업로드 요청 시작 (resumable, 5MB chunks)")

        response = None
        chunk = 0
        while response is None:
            try:
                status_obj, response = yt_req.next_chunk()
                chunk += 1
                if status_obj:
                    pct = int(status_obj.resumable_progress /
                              status_obj.resumable_total * 100)
                    print(f"[INFO] upload_to_youtube: {pct}% (chunk {chunk})")
            except HttpError as e:
                body_text = e.content.decode("utf-8", errors="ignore")[:500]
                print(f"[ERROR] upload_to_youtube HttpError: status={e.resp.status}\n{body_text}")
                print(traceback.format_exc())
                return {"status": "error",
                        "message": f"HTTP {e.resp.status}: {body_text}"}

        # 전체 응답 로깅
        print(f"[INFO] upload_to_youtube: 전체 응답 = {json.dumps(response, ensure_ascii=False)[:800]}")

        video_id      = response.get("id", "")
        upload_status = response.get("status", {}).get("uploadStatus", "unknown")
        privacy       = response.get("status", {}).get("privacyStatus", "unknown")

        print(f"[INFO] upload_to_youtube: video_id={video_id!r}, "
              f"uploadStatus={upload_status!r}, privacyStatus={privacy!r}")

        if not video_id:
            print("[ERROR] upload_to_youtube: video_id 가 비어있음 — 업로드 실패로 처리")
            return {"status": "error", "message": f"video_id 없음. 응답={response}"}

        return {
            "status":        "success",
            "video_id":      video_id,
            "upload_status": upload_status,
            "privacy":       privacy,
            "url":           f"https://www.youtube.com/watch?v={video_id}",
        }
    except Exception as e:
        print(f"[ERROR] upload_to_youtube 예외: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def clean_filename(text):
    return re.sub(r'[\\/*?:"<>|]', "", text)[:30].strip()


def video_package_json():
    prompt = """
유튜브 쇼츠/릴스용 30초 영상 데이터를 JSON 형식으로 정확하게 생성해줘.

반드시 이 JSON 형식으로만 응답:
{
  "title": "한국어 영상 제목 (30자 이내)",
  "title_en": "English title for video overlay (max 40 chars, ASCII only)",
  "description": "YouTube 설명 (100자 이내)",
  "tags": ["태그1", "태그2", "태그3"],
  "narration": "30초 분량의 나레이션 (약 150자)",
  "narration_en": "English narration for video overlay (max 120 chars, ASCII only, 3-4 sentences)"
}

JSON 외에 다른 텍스트는 절대 포함하지 말것!
"""
    print("[INFO] video_package_json: AI 요청 중...")
    response = ask_ai(prompt, 800)
    if not response:
        print("[ERROR] video_package_json: AI 응답 없음")
        return None
    try:
        s = response.find("{")
        e = response.rfind("}") + 1
        if s < 0 or e <= 0:
            print(f"[ERROR] video_package_json: JSON 없음. 원문:\n{response}")
            return None
        data = json.loads(response[s:e])
        print(f"[INFO] video_package_json: 파싱 성공 - {data.get('title')}")
        return data
    except json.JSONDecodeError as e:
        print(f"[ERROR] video_package_json: JSON 파싱 실패 - {e}\n원문:\n{response}")
        return None


# ── 백그라운드 작업 실행기 ────────────────────────────────────────────────────

def _run_video_job(job_id: str) -> None:
    """영상 제작 파이프라인 - 백그라운드 스레드에서 실행"""
    try:
        _update_job(job_id, status="running")

        # Step 1: AI 콘텐츠 생성
        _append_log(job_id, "1️⃣ AI 콘텐츠 생성 중...")
        content_data = video_package_json()
        if not content_data:
            _append_log(job_id, "❌ 콘텐츠 생성 실패 (API 키 확인 필요: /debug)")
            _update_job(job_id, status="error", error="AI 콘텐츠 생성 실패")
            return
        _append_log(job_id, f"✅ 콘텐츠 완료: {content_data.get('title')}")
        _update_job(job_id, content=content_data)

        # Step 2: 이미지 + 영상 생성
        _append_log(job_id, "2️⃣ FFmpeg 영상 생성 중 (텍스트 오버레이)...")
        video_path = create_simple_video(content_data)
        if not video_path:
            _append_log(job_id, "❌ 영상 생성 실패 (ffmpeg 확인 필요)")
            _update_job(job_id, status="error",
                        error="영상 생성 실패", content=content_data)
            return
        _append_log(job_id, f"✅ 영상 완료: {video_path}")
        _update_job(job_id, video_path=video_path)

        # Step 3: YouTube 업로드
        _append_log(job_id, "3️⃣ YouTube 업로드 시도 중...")
        upload = upload_to_youtube(
            video_path,
            content_data.get("title", ""),
            content_data.get("description", ""),
            content_data.get("tags", [])
        )
        yt_status = upload.get("status")
        if yt_status == "success":
            _append_log(job_id, f"✅ YouTube 업로드 완료!\n🔗 {upload.get('url')}")
        elif yt_status == "auth_required":
            _append_log(job_id,
                f"⚠️ YouTube 인증 필요: {upload.get('message', '')}\n"
                f"→ Render 환경변수 YOUTUBE_TOKEN_JSON 확인 후 /test-youtube 로 진단")
        elif yt_status == "skipped":
            _append_log(job_id, "💾 YouTube 건너뜀 (Google 패키지 미설치)")
        else:
            _append_log(job_id, f"❌ YouTube 업로드 실패: {upload.get('message', '알 수 없는 오류')}")

        _update_job(job_id, status="done", youtube=upload)

    except Exception as e:
        print(f"[ERROR] Job {job_id} 예외: {e}\n{traceback.format_exc()}")
        _append_log(job_id, f"❌ 예외 발생: {e}")
        _update_job(job_id, status="error", error=str(e))


# ── 라우트 ───────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def home():
    if "history" not in session:
        session["history"] = []

    if request.method == "POST":
        question = request.form.get("question", "").strip()
        if question:
            if question == "VIDEO_PACKAGE":
                # 버튼이 JS로 처리되지만 form submit 폴백도 안내
                answer = "🎬 영상 제작은 백그라운드로 실행됩니다.\n버튼 클릭 시 자동으로 진행 상황이 업데이트됩니다."
            elif question == "GET_TRENDS":
                answer = ask_ai("글로벌 SNS에서 주목받을 쇼츠/릴스 트렌드 5개를 추천해줘.", 800)
            elif question == "MAKE_SHORTS":
                answer = ask_ai("조회수가 잘 나올 쇼츠 아이디어 3개를 만들어줘. 형식: 제목\\n- 설명\\n- 조회수 잠재력", 900)
            elif question == "CONTENT_PACKAGE":
                answer = ask_ai("유튜브 쇼츠, 인스타 릴스, 틱톡 동시 업로드 콘텐츠 1개를 만들어줘.", 900)
            elif question == "MONEY_IDEAS":
                answer = ask_ai("월 10~50만원 부수입 목표로 자동화 가능한 아이디어 5개를 추천해줘.", 900)
            elif question == "AI_NEWS":
                answer = ask_ai("AI 콘텐츠 사업자가 참고할 만한 AI/테크 이슈 후보 5개를 알려줘.", 800)
            elif question == "GLOBAL_ISSUES":
                answer = ask_ai("해외 시청자를 노릴 글로벌 이슈형 콘텐츠 주제 5개를 추천해줘.", 800)
            elif question == "IMAGE_PROMPT":
                answer = ask_ai("유튜브 쇼츠용 이미지 생성 프롬프트를 영어로 5개 만들어줘.", 900)
            else:
                answer = ask_ai(question, 800)

            if not answer:
                answer = "⚠️ AI 응답 실패. /debug 에서 API 키를 확인하세요."

            session["history"].append({"role": "user",    "content": question})
            session["history"].append({"role": "assistant","content": answer})
            if len(session["history"]) > MAX_HISTORY:
                session["history"] = session["history"][-MAX_HISTORY:]
            session.modified = True

    return render_template("index.html", history=session.get("history", []))


@app.route("/start-video", methods=["POST"])
def start_video():
    """영상 제작 백그라운드 작업 시작 → 즉시 job_id 반환"""
    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "logs": ["⏳ 작업 대기 중..."],
            "created_at": datetime.now().isoformat(),
            "content": None,
            "video_path": None,
            "youtube": None,
            "error": None
        }
    t = threading.Thread(target=_run_video_job, args=(job_id,), daemon=True)
    t.start()
    print(f"[INFO] 백그라운드 작업 시작: {job_id}")
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route("/status/<job_id>", methods=["GET"])
def job_status(job_id):
    """작업 진행 상황 폴링"""
    job = _get_job(job_id)
    if not job:
        return jsonify({
            "error": "server_restarted",
            "message": "서버가 재시작되어 작업 정보가 사라졌습니다. 버튼을 다시 클릭해주세요."
        }), 404
    return jsonify(job), 200


@app.route("/save-chat", methods=["POST"])
def save_chat():
    """완료된 작업 결과를 채팅 히스토리에 저장"""
    data = request.get_json(silent=True) or {}
    user_msg = data.get("user", "")
    asst_msg = data.get("assistant", "")
    if not user_msg or not asst_msg:
        return jsonify({"error": "user/assistant 필드 필요"}), 400

    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user",     "content": user_msg})
    session["history"].append({"role": "assistant", "content": asst_msg})
    if len(session["history"]) > MAX_HISTORY:
        session["history"] = session["history"][-MAX_HISTORY:]
    session.modified = True
    return jsonify({"status": "ok"}), 200


@app.route("/video-package", methods=["GET", "POST"])
def video_package():
    """영상 패키지 생성 JSON API (동기 - 직접 호출용)"""
    print("[INFO] /video-package 동기 요청 시작")
    try:
        content_data = video_package_json()
        if not content_data:
            return jsonify({"status": "error", "step": "content",
                            "message": "AI 콘텐츠 생성 실패"}), 500
        video_path = create_simple_video(content_data)
        if not video_path:
            return jsonify({"status": "partial", "step": "video",
                            "message": "영상 생성 실패", "content": content_data}), 500
        upload = upload_to_youtube(
            video_path,
            content_data.get("title", ""),
            content_data.get("description", ""),
            content_data.get("tags", [])
        )
        return jsonify({"status": "success", "video_path": video_path,
                        "content": content_data, "youtube": upload}), 200
    except Exception as e:
        print(f"[ERROR] /video-package: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/auto-create", methods=["GET"])
def auto_create():
    auth_key = request.args.get("key")
    if auth_key != os.getenv("AUTO_KEY", "secret123"):
        return {"status": "error", "message": "Unauthorized"}, 401

    results = []
    try:
        content_data = video_package_json()
        if content_data:
            video_path = create_simple_video(content_data)
            if video_path:
                results.append("✅ 영상 생성 완료")
                upload = upload_to_youtube(video_path, content_data.get("title"),
                                           content_data.get("description"),
                                           content_data.get("tags", []))
                if upload.get("status") == "success":
                    results.append(f"✅ YouTube: {upload.get('url')}")
                else:
                    results.append("✅ 영상 준비 완료 (YouTube 업로드 대기)")
            else:
                results.append("❌ 영상 생성 실패")
        else:
            results.append("❌ 콘텐츠 생성 실패")
    except Exception as e:
        results.append(f"❌ 오류: {e}")

    return {"status": "success", "message": "\n".join(results),
            "timestamp": datetime.now().isoformat(),
            "count": sum(1 for r in results if r.startswith("✅"))}, 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}, 200


@app.route("/debug", methods=["GET"])
def debug():
    import anthropic as _am, openai as _om
    ffmpeg_ok, ffmpeg_ver = False, None
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        ffmpeg_ok = r.returncode == 0
        ffmpeg_ver = (r.stdout if ffmpeg_ok else r.stderr).splitlines()[0]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        ffmpeg_ver = str(e)

    return jsonify({
        "version":               "v6-ascii-drawtext",
        "anthropic_key_set":     bool(os.getenv("ANTHROPIC_API_KEY")),
        "anthropic_client_ok":   claude_client is not None,
        "anthropic_sdk_version": getattr(_am, "__version__", "unknown"),
        "openai_key_set":        bool(os.getenv("OPENAI_API_KEY")),
        "openai_client_ok":      openai_client is not None,
        "openai_sdk_version":    getattr(_om, "__version__", "unknown"),
        "google_available":      GOOGLE_AVAILABLE,
        "ffmpeg_available":      ffmpeg_ok,
        "ffmpeg_version":        ffmpeg_ver,
        "active_jobs":           len(_jobs),
        "python_version":        __import__("sys").version,
        "timestamp":             datetime.now().isoformat()
    }), 200


@app.route("/test-youtube", methods=["GET"])
def test_youtube():
    """YouTube 연결 상태 진단"""
    result = {
        "google_available":   GOOGLE_AVAILABLE,
        "token_file_exists":  os.path.exists(TOKEN_FILE),
        "token_env_set":      bool(os.getenv("YOUTUBE_TOKEN_JSON", "").strip()),
        "timestamp":          datetime.now().isoformat(),
    }

    if not GOOGLE_AVAILABLE:
        result["status"] = "error"
        result["message"] = "Google 패키지 미설치 (requirements.txt 확인)"
        return jsonify(result), 200

    if not os.path.exists(TOKEN_FILE):
        result["status"] = "error"
        result["message"] = ("youtube_token.json 없음 — "
                             "Render 환경변수 YOUTUBE_TOKEN_JSON 설정 필요")
        return jsonify(result), 200

    # 토큰 파일 내용 확인 (민감정보 제외)
    try:
        with open(TOKEN_FILE, encoding="utf-8") as _f:
            td = json.load(_f)
        result["token_fields"]      = list(td.keys())
        result["has_refresh_token"] = bool(td.get("refresh_token"))
        result["has_client_id"]     = bool(td.get("client_id"))
        result["token_expiry"]      = td.get("expiry") or td.get("token_expiry", "unknown")
    except Exception as e:
        result["status"] = "error"
        result["message"] = f"토큰 파일 파싱 오류: {e}"
        return jsonify(result), 200

    # YouTube API 서비스 초기화 및 채널 정보 조회
    try:
        svc, err = get_youtube_service()
        if not svc:
            result["status"] = "error"
            result["auth_error"] = err
            return jsonify(result), 200

        ch = svc.channels().list(
            part="snippet,statistics", mine=True).execute()
        items = ch.get("items", [])
        if items:
            item = items[0]
            snip = item["snippet"]
            stat = item.get("statistics", {})
            result["channel_name"]        = snip["title"]
            result["channel_id"]          = item["id"]
            result["channel_url"]         = f"https://www.youtube.com/channel/{item['id']}"
            result["channel_custom_url"]  = snip.get("customUrl", "")
            result["channel_subscribers"] = stat.get("subscriberCount", "N/A")
            result["channel_video_count"] = stat.get("videoCount", "N/A")
            result["upload_target"]       = (
                f"{snip['title']} "
                f"({snip.get('customUrl', item['id'])})"
            )
            result["status"]  = "ok"
            result["message"] = (
                f"YouTube API 연결 정상 — 업로드 대상: {snip['title']} 채널"
            )
            print(f"[TEST-YT] 연결 채널: {snip['title']} / {item['id']} "
                  f"/ {snip.get('customUrl','')}")
        else:
            result["status"]  = "warning"
            result["message"] = "API 연결 성공이나 채널 정보 없음 (권한 확인)"
    except Exception as e:
        result["status"]    = "error"
        result["api_error"] = str(e)
        result["traceback"] = traceback.format_exc()[-600:]

    return jsonify(result), 200


@app.route("/check-system", methods=["GET"])
def check_system():
    """폰트·FFmpeg·시스템 상태 진단 엔드포인트."""
    result = {"timestamp": datetime.now().isoformat()}

    # 폰트 파일 존재 여부 직접 확인
    font_candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    result["font_checks"] = {p: os.path.exists(p) for p in font_candidates}
    result["font_found"]  = _find_font()

    # fc-list (fontconfig)로 설치된 폰트 목록
    try:
        fc = subprocess.run(["fc-list", ":lang=ko"],
                            capture_output=True, text=True, timeout=10)
        result["fc_list_ko"] = fc.stdout[:600] or "(없음)"
    except Exception as e:
        result["fc_list_ko"] = f"fc-list 실패: {e}"

    # /usr/share/fonts 디렉터리 구조
    try:
        ls = subprocess.run(["find", "/usr/share/fonts", "-name", "*.ttf", "-o",
                             "-name", "*.otf", "-o", "-name", "*.ttc"],
                            capture_output=True, text=True, timeout=10)
        result["font_files"] = ls.stdout[:800] or "(없음)"
    except Exception as e:
        result["font_files"] = f"find 실패: {e}"

    # FFmpeg 버전
    try:
        fv = subprocess.run(["ffmpeg", "-version"],
                            capture_output=True, text=True, timeout=5)
        result["ffmpeg_version"] = fv.stdout.splitlines()[0] if fv.stdout else fv.stderr[:100]
    except Exception as e:
        result["ffmpeg_version"] = str(e)

    # 디스크 여유 공간
    try:
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        result["disk"] = df.stdout
    except Exception as e:
        result["disk"] = str(e)

    return jsonify(result), 200


@app.route("/test-ffmpeg", methods=["GET"])
def test_ffmpeg():
    """FFmpeg drawtext 3단계 전략 동기 테스트 (결과 즉시 반환)."""
    import tempfile, shutil
    result = {"timestamp": datetime.now().isoformat()}
    tmpdir = tempfile.mkdtemp()

    base_in  = ["ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=0x0d0d1a:size=1080x1920:rate=24"]
    base_out = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-t", "3", "-pix_fmt", "yuv420p", "-threads", "1"]

    def _sync_run(cmd, label):
        out_path = os.path.join(tmpdir, f"{label}.mp4")
        full_cmd = cmd + [out_path]
        result[f"{label}_cmd"] = " ".join(full_cmd)
        try:
            r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
            result[f"{label}_rc"]   = r.returncode
            result[f"{label}_size"] = os.path.getsize(out_path) if os.path.exists(out_path) else 0
            result[f"{label}_err"]  = r.stderr[-400:]
        except Exception as e:
            result[f"{label}_rc"]  = -1
            result[f"{label}_err"] = str(e)

    # 1) 기본 단색
    _sync_run(base_in + base_out, "plain")

    # 2) fontfile drawtext
    font_path = _find_font()
    result["font_path"] = font_path
    if font_path:
        vf = _build_drawtext_vf(font_path, "JARVIS-X TEST", "텍스트 오버레이 테스트")
        result["drawtext_vf"] = vf[:300]
        _sync_run(base_in + ["-vf", vf] + base_out, "drawtext_font")

    # 3) 내장 폰트 drawtext (ASCII)
    vf_ascii = _build_ascii_drawtext_vf("JARVIS-X ASCII Test", "AI Generated Short Video")
    result["ascii_vf"] = vf_ascii[:300]
    _sync_run(base_in + ["-vf", vf_ascii] + base_out, "drawtext_ascii")

    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    return jsonify(result), 200


@app.route("/test-ai", methods=["GET"])
def test_ai():
    result = {}
    try:
        if not claude_client:
            result["claude"] = {"status": "error", "message": "클라이언트 미초기화"}
        else:
            msg = claude_client.messages.create(
                model="claude-sonnet-4-6", max_tokens=30,
                messages=[{"role": "user", "content": "hi"}]
            )
            result["claude"] = {"status": "ok", "response": msg.content[0].text}
    except Exception as e:
        result["claude"] = {"status": "error", "message": str(e)}

    try:
        if not openai_client:
            result["chatgpt"] = {"status": "error", "message": "클라이언트 미초기화"}
        else:
            resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=30
            )
            result["chatgpt"] = {"status": "ok", "response": resp.choices[0].message.content}
    except Exception as e:
        result["chatgpt"] = {"status": "error", "message": str(e)}

    return jsonify(result), 200


@app.route("/reset")
def reset():
    session.pop("history", None)
    return '<h2>대화기록 초기화 완료</h2><a href="/">JARVIS-X 돌아가기</a>'


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_ENV") != "production"
    app.run(debug=debug_mode, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
