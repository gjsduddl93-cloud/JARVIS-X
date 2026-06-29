from flask import Flask, render_template, request, session, jsonify
from anthropic import Anthropic
from openai import OpenAI
import httpx
import threading
import uuid
import time

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
import random
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
    """fontfile 지정 drawtext VF (한국어 지원, 순차 등장)."""
    fp = font_path.replace("\\", "/")
    # 제목: 상단 굵은 흰색 + 아웃라인
    parts = [
        f"drawtext=fontfile='{fp}'"
        f":text='{_ffmpeg_escape(title[:20])}'"
        f":fontsize=62:fontcolor=white"
        f":x=(w-text_w)/2:y=120"
        f":enable='between(t,0,4.5)'"
        f":borderw=5:bordercolor=black@0.95"
        f":shadowx=4:shadowy=4:shadowcolor=black@0.55",
        # 채널 워터마크
        f"drawtext=fontfile='{fp}'"
        ":text='@future.minute'"
        ":fontsize=30:fontcolor=white@0.75"
        ":x=w-text_w-35:y=h-90"
        ":borderw=2:bordercolor=black@0.7",
    ]
    lines = [narration[i:i+18] for i in range(0, min(len(narration), 90), 18)]
    start_times = [0.5, 5.0, 9.5, 14.5, 20.0]
    for idx, line in enumerate(lines):
        t = start_times[idx] if idx < len(start_times) else 0.5 + idx * 5.0
        parts.append(
            f"drawtext=fontfile='{fp}'"
            f":text='{_ffmpeg_escape(line)}'"
            f":fontsize=52:fontcolor=white"
            f":x=(w-text_w)/2:y=h-260"
            f":enable='gte(t,{t})'"
            f":borderw=5:bordercolor=black@0.95"
            f":shadowx=4:shadowy=4:shadowcolor=black@0.5"
        )
    return ",".join(parts)


def _build_ascii_drawtext_vf(title, narration):
    """FFmpeg 내장 폰트 drawtext (ASCII 전용, 순차 등장, 30~45초 영상용)."""
    atitle = _ascii_only(title) or "JARVIS-X"
    anarr  = _ascii_only(narration) or "AI Generated Content"
    # 제목: 상단 굵은 흰색 + 아웃라인
    parts = [
        f"drawtext=text='{_ffmpeg_escape(atitle[:32])}'"
        f":fontsize=62:fontcolor=white"
        f":x=(w-text_w)/2:y=120"
        f":enable='between(t,0,4.5)'"
        f":borderw=5:bordercolor=black@0.95"
        f":shadowx=4:shadowy=4:shadowcolor=black@0.55",
        # 채널 워터마크
        "drawtext=text='@future.minute'"
        ":fontsize=30:fontcolor=white@0.75"
        ":x=w-text_w-35:y=h-90"
        ":borderw=2:bordercolor=black@0.7",
    ]
    # 나레이션: 안전구역 하단, 순차 등장
    lines = [anarr[i:i+36] for i in range(0, min(len(anarr), 200), 36)]
    start_times = [0.5, 5.0, 9.5, 14.5, 20.0]
    for idx, line in enumerate(lines):
        t = start_times[idx] if idx < len(start_times) else 0.5 + idx * 5.0
        parts.append(
            f"drawtext=text='{_ffmpeg_escape(line)}'"
            f":fontsize=52:fontcolor=white"
            f":x=(w-text_w)/2:y=h-260"
            f":enable='gte(t,{t})'"
            f":borderw=5:bordercolor=black@0.95"
            f":shadowx=4:shadowy=4:shadowcolor=black@0.5"
        )
    return ",".join(parts)


def _build_pillarbox_fc(sub_parts: list, audio_fc: str = "") -> str:
    """Pillarbox blur + 자막 + 오디오 filter_complex 문자열 생성.
    배경: 블러 채움 / 전경: 비율 유지 스케일 / 오버레이: 중앙 정렬.
    """
    video_part = (
        "[0:v]split=2[pb_bg][pb_fg];"
        "[pb_bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=50:3,format=yuv420p[pb_blurred];"
        "[pb_fg]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "format=yuv420p[pb_main];"
        "[pb_blurred][pb_main]overlay=(W-w)/2:(H-h)/2"
    )
    if sub_parts:
        video_part += "," + ",".join(sub_parts)
    video_part += ",format=yuv420p[vout]"
    if audio_fc:
        return video_part + ";" + audio_fc
    return video_part


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


def create_tts_audio(text: str, lang: str = "ko") -> str | None:
    """ElevenLabs TTS (우선) → gTTS fallback → None 순서로 MP3 생성."""
    # 150자 제한 → 한국어 TTS 약 40~45초 분량 (Shorts 60초 이내)
    narr = (text or "").strip()[:150]
    if not narr:
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    audio_path = os.path.join(AUDIO_DIR, f"tts_{ts}.mp3")

    # ── 1. ElevenLabs (고품질 한국어) ─────────────────────────────────────────
    el_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if el_key:
        try:
            # EXAVITQu4vr4xnSDxMaL = Sarah (자연스러운 다국어, 한국어 발음 우수)
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            payload = {
                "text": narr,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.60,
                    "similarity_boost": 0.80,
                    "style": 0.30,
                    "use_speaker_boost": True,
                },
            }
            headers = {
                "xi-api-key": el_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            }
            print(f"[TTS] ElevenLabs 요청 중... ({len(narr)}자)")
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                with open(audio_path, "wb") as f:
                    f.write(resp.content)
                size = os.path.getsize(audio_path)
                print(f"[TTS] ElevenLabs 완료: {audio_path} ({size}B)")
                if size > 500:
                    return audio_path
            else:
                print(f"[TTS] ElevenLabs HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[TTS] ElevenLabs 예외: {e}")
    else:
        print("[TTS] ELEVENLABS_API_KEY 없음 → gTTS 사용")

    # ── 2. gTTS fallback ──────────────────────────────────────────────────────
    try:
        from gtts import gTTS
        print(f"[TTS] gTTS 음성 생성 중... ({len(narr[:400])}자)")
        tts = gTTS(text=narr[:400], lang=lang, slow=False)
        tts.save(audio_path)
        size = os.path.getsize(audio_path)
        print(f"[TTS] gTTS 완료: {audio_path} ({size}B)")
        return audio_path if size > 500 else None
    except ImportError:
        print("[TTS] gTTS 미설치")
    except Exception as e:
        print(f"[TTS] gTTS 예외: {e}")

    return None


def create_simple_video(content_data):
    """ElevenLabs/gTTS + 그라데이션 배경 + BGM + 텍스트 오버레이 30~45초 쇼츠."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_path = os.path.join(VIDEOS_DIR, f"video_{ts}.mp4")
        log_path   = os.path.join(os.path.abspath(IMAGES_DIR), f"ffmpeg_{ts}.log")

        try:
            subprocess.run(["ffmpeg", "-version"],
                           capture_output=True, timeout=10).check_returncode()
        except Exception as e:
            print(f"[ERROR] ffmpeg 실행 불가: {e}")
            return None

        title     = content_data.get("title", "JARVIS-X")
        narration = content_data.get("narration", "")
        title_en  = content_data.get("title_en", "") or _ascii_only(title) or "JARVIS-X"
        narr_en   = content_data.get("narration_en", "") or _ascii_only(narration) or "AI Generated Content"
        font_path = _find_font()

        print(f"[VIDEO] title_en={title_en!r}, narr_en_len={len(narr_en)}, font={font_path}")

        # TTS 음성 생성 (ElevenLabs → gTTS → None)
        audio_path = create_tts_audio(narration or title)
        print(f"[VIDEO] TTS={'있음:' + audio_path if audio_path else '없음(무음)'}")

        # ── 공통 빌딩블록 ────────────────────────────────────────────────────
        # 전자음악풍 앰비언트 BGM (4화음 드론) — lavfi로 실시간 생성
        bgm_expr = (
            "0.12*sin(110*2*PI*t)*(0.6+0.4*sin(0.5*2*PI*t))"
            "+0.08*sin(220*2*PI*t)"
            "+0.05*sin(330*2*PI*t)"
            "+0.03*sin(440*2*PI*t)"
        )
        bgm_lavfi = f"aevalsrc={bgm_expr}:s=44100:c=stereo"

        # geq 그라데이션은 Render 0.1vCPU에서 OOM 유발 → 사용 안 함
        bg_in   = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "color=c=0x080818:size=1080x1920:rate=24"]
        vid_enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                   "-pix_fmt", "yuv420p", "-threads", "1"]
        aud_enc = ["-c:a", "aac", "-b:a", "128k"]

        ascii_vf = _build_ascii_drawtext_vf(title_en, narr_en)
        ko_vf    = (_build_drawtext_vf(font_path, title, narration)
                    if font_path else None)

        def _ok(label):
            exists = os.path.exists(video_path)
            size   = os.path.getsize(video_path) if exists else 0
            ok     = exists and size > 2000
            print(f"[VIDEO] {'성공' if ok else '실패'} ({label}){': '+str(size)+'B' if ok else ''}")
            if not ok and exists:
                os.remove(video_path)
            return ok

        def _run(label, cmd, t=90):
            if os.path.exists(video_path):
                os.remove(video_path)
            print(f"[VIDEO] 전략: {label}")
            _run_ffmpeg(cmd, log_path, timeout=t)
            return _ok(label)

        # ── 전략 1 (주력): 단색 + ASCII drawtext + TTS 음성 ─────────────────
        # Render free tier 안정. geq/filter_complex 없음. 최대 45초 하드캡.
        if audio_path and os.path.exists(audio_path):
            cmd1 = (bg_in + ["-i", audio_path]
                    + ["-vf", ascii_vf]
                    + vid_enc + aud_enc + ["-t", "45", "-shortest", video_path])
            if _run("plain+ascii+voice", cmd1):
                return video_path

            # 전략 2: 단색 + 한국어 drawtext + 음성 (NanumGothic 있을 때)
            if ko_vf:
                cmd2 = (bg_in + ["-i", audio_path]
                        + ["-vf", ko_vf]
                        + vid_enc + aud_enc + ["-t", "45", "-shortest", video_path])
                if _run("plain+korean+voice", cmd2):
                    return video_path

            # 전략 3: 단색 + ASCII + 음성 + BGM (filter_complex 오디오만)
            fc3 = (
                f"[0:v]{ascii_vf}[vout];"
                f"[1:a]volume=1.0[voice];"
                f"[2:a]volume=0.25[bgm];"
                f"[voice][bgm]amix=inputs=2:duration=first[aout]"
            )
            cmd3 = (bg_in
                    + ["-i", audio_path]
                    + ["-f", "lavfi", "-i", bgm_lavfi]
                    + ["-filter_complex", fc3]
                    + ["-map", "[vout]", "-map", "[aout]"]
                    + vid_enc + aud_enc + ["-t", "45", "-shortest", video_path])
            if _run("plain+ascii+voice+bgm", cmd3, t=120):
                return video_path

            # 전략 4: 음성만 (텍스트/BGM 모두 실패 시)
            cmd4 = (bg_in + ["-i", audio_path]
                    + vid_enc + aud_enc + ["-t", "45", "-shortest", video_path])
            if _run("plain+voice-only", cmd4):
                return video_path

        # ── 무음 폴백 (40초) ─────────────────────────────────────────────────
        cmd5 = (bg_in + ["-vf", ascii_vf]
                + vid_enc + ["-t", "40", video_path])
        if _run("plain+ascii-noaudio", cmd5):
            return video_path

        cmd6 = bg_in + vid_enc + ["-t", "40", video_path]
        if _run("plain-only", cmd6):
            return video_path

        print("[ERROR] 모든 FFmpeg 전략 실패")
        return None

    except Exception as e:
        print(f"[ERROR] create_simple_video 예외: {e}")
        print(traceback.format_exc())
        return None


# ── Unsplash 이미지 다운로드 ──────────────────────────────────────────────────

def _download_unsplash_images(keywords: list, count: int = 5) -> list:
    """Unsplash API로 이미지 다운로드. portrait 우선 → 부족하면 일반 검색으로 재시도."""
    api_key = os.getenv("UNSPLASH_API_KEY", "").strip()
    if not api_key:
        print("[UNSPLASH] UNSPLASH_API_KEY 없음")
        return []

    base_q   = " ".join(str(k) for k in keywords[:2]) if keywords else "technology AI future"
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    img_paths: list = []

    # 쿼리 시도 순서: portrait 우선 → orientation 없이 → 범용 밝은 키워드
    search_attempts = [
        (base_q, "portrait"),
        (base_q, None),
        ("technology success business", "portrait"),
        ("bright colorful abstract", None),
    ]

    for attempt_q, orientation in search_attempts:
        if len(img_paths) >= count:
            break
        need = count - len(img_paths)
        params: dict = {"query": attempt_q, "per_page": need + 3}
        if orientation:
            params["orientation"] = orientation
        print(f"[UNSPLASH] 검색: '{attempt_q}' orient={orientation} need={need}")
        try:
            resp = requests.get(
                "https://api.unsplash.com/search/photos",
                params=params,
                headers={"Authorization": f"Client-ID {api_key}"},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"[UNSPLASH] HTTP {resp.status_code}")
                continue
            photos = resp.json().get("results", [])
            print(f"[UNSPLASH] 결과: {len(photos)}개")
            for i, photo in enumerate(photos):
                if len(img_paths) >= count:
                    break
                img_url = (photo.get("urls", {}).get("regular") or
                           photo.get("urls", {}).get("small", ""))
                if not img_url:
                    continue
                img_path = os.path.join(IMAGES_DIR, f"unsplash_{ts}_{len(img_paths)}.jpg")
                try:
                    ir = requests.get(img_url, timeout=20, stream=True)
                    if ir.status_code == 200:
                        with open(img_path, "wb") as f:
                            for chunk in ir.iter_content(8192):
                                f.write(chunk)
                        size = os.path.getsize(img_path)
                        if size > 5000:
                            img_paths.append(img_path)
                            print(f"[UNSPLASH] {len(img_paths)}번: {size//1024}KB")
                        else:
                            os.remove(img_path)
                except Exception as e:
                    print(f"[UNSPLASH] 다운로드 실패: {e}")
        except Exception as e:
            print(f"[UNSPLASH] 요청 실패: {e}")

    print(f"[UNSPLASH] 최종 {len(img_paths)}개 준비")
    return img_paths


def _split_narration_subtitles(text: str, n_slides: int) -> list:
    """영어 나레이션을 문장 단위로 분리해 슬라이드 수에 맞게 배정."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [""] * n_slides
    return [sentences[i % len(sentences)] for i in range(n_slides)]


def create_viral_shorts(content_data: dict):
    """
    Unsplash 이미지 슬라이드쇼 + 동적 자막 + TTS + BGM 쇼츠.
    concat demuxer 방식 사용 (filter_complex xfade보다 안정적).
    실패 시 create_simple_video()로 자동 폴백.
    """
    try:
        ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_path = os.path.join(VIDEOS_DIR, f"viral_{ts}.mp4")
        log_path   = os.path.join(IMAGES_DIR,  f"ffmpeg_viral_{ts}.log")

        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10).check_returncode()
        except Exception as e:
            print(f"[VIRAL] ffmpeg 없음: {e}")
            return create_simple_video(content_data)

        # ── 콘텐츠 필드 추출 ─────────────────────────────────────────────────
        title     = content_data.get("title", "JARVIS-X")
        narration = content_data.get("narration", "")
        title_en  = content_data.get("title_en", "") or _ascii_only(title) or "JARVIS-X"
        narr_en   = content_data.get("narration_en", "") or _ascii_only(narration) or "AI Content"
        keywords  = content_data.get("keywords", [title_en])

        # ── 1. Unsplash 이미지 다운로드 (8개로 증가 → 더 다양한 장면) ──────────
        img_paths = _download_unsplash_images(keywords, count=8)
        if len(img_paths) < 2:
            print("[VIRAL] 이미지 부족 → create_simple_video 폴백")
            return create_simple_video(content_data)

        n       = len(img_paths)
        IMG_DUR = 5.5  # 이미지당 표시 시간 (초) — 더 여유있는 호흡

        # ── 2. TTS 음성 생성 ─────────────────────────────────────────────────
        audio_path = create_tts_audio(narration or title)
        print(f"[VIRAL] TTS={'있음: '+audio_path if audio_path else '없음'}")

        # ── 3. 자막 분할 ──────────────────────────────────────────────────────
        subtitle_lines = _split_narration_subtitles(narr_en, n)

        # ── 4. BGM ────────────────────────────────────────────────────────────
        bgm_expr  = (
            "0.12*sin(110*2*PI*t)*(0.6+0.4*sin(0.5*2*PI*t))"
            "+0.08*sin(220*2*PI*t)+0.05*sin(330*2*PI*t)+0.03*sin(440*2*PI*t)"
        )
        bgm_lavfi = f"aevalsrc={bgm_expr}:s=44100:c=stereo"

        vid_enc = ["-c:v", "libx264", "-preset", "ultrafast",
                   "-crf", "28", "-pix_fmt", "yuv420p", "-threads", "1"]
        aud_enc = ["-c:a", "aac", "-b:a", "128k"]

        # ── 5. Pexels 클립 준비 (PEXELS_API_KEY 있으면) ─────────────────────
        pexels_clips = _fetch_pexels_clips(keywords, clip_sec=IMG_DUR, max_clips=2)
        print(f"[VIRAL] Pexels 클립: {len(pexels_clips)}개")

        # ── 5b. concat 파일 리스트 생성 (이미지 + Pexels 클립 혼합) ─────────
        # 전체 슬라이드 리스트: 이미지 3장마다 Pexels 클립 1개 삽입
        concat_txt  = os.path.join(IMAGES_DIR, f"concat_{ts}.txt")
        slide_items = []  # (path, is_video)
        clip_q      = list(pexels_clips)
        for i, ip in enumerate(img_paths):
            slide_items.append((os.path.abspath(ip), False))
            if clip_q and (i + 1) % 3 == 0:
                slide_items.append((os.path.abspath(clip_q.pop(0)), True))
        n = len(slide_items)  # 자막 수 재계산

        with open(concat_txt, "w") as f:
            for path, is_video in slide_items:
                f.write(f"file '{path}'\n")
                if not is_video:
                    f.write(f"duration {IMG_DUR}\n")
            # last-frame 보장 (마지막 이미지 재사용)
            last_img = next((p for p, v in reversed(slide_items) if not v), None)
            if last_img:
                f.write(f"file '{last_img}'\n")

        # ── 6. 자막 VF 구성 ──────────────────────────────────────────────────
        sub_parts = []

        # 자막 영역 반투명 배경박스 (가독성 향상 — 모든 drawtext보다 먼저 렌더)
        sub_parts.append(
            "drawbox=x=0:y=h-310:w=iw:h=230:color=black@0.45:t=fill"
        )

        # 상단 제목 (첫 4.5초): 굵은 흰색 + 검은 아웃라인 — 훅 역할
        safe_title = _ffmpeg_escape(_ascii_only(title_en[:32]))
        if safe_title:
            sub_parts.append(
                f"drawtext=text='{safe_title}'"
                f":fontsize=62:fontcolor=white"
                f":x=(w-text_w)/2:y=120"
                f":enable='between(t,0,4.5)'"
                f":borderw=5:bordercolor=black@0.95"
                f":shadowx=4:shadowy=4:shadowcolor=black@0.55"
            )

        # 채널 워터마크 (항상, 우하단 안전구역)
        sub_parts.append(
            "drawtext=text='@future.minute'"
            ":fontsize=30:fontcolor=white@0.75"
            ":x=w-text_w-35:y=h-90"
            ":borderw=2:bordercolor=black@0.7"
        )

        # 나레이션 자막 (이미지별, 안전구역 y=h-240)
        for idx, line in enumerate(subtitle_lines):
            t_s = idx * IMG_DUR + 0.5
            t_e = t_s + IMG_DUR - 1.0
            safe_line = _ffmpeg_escape(_ascii_only(str(line))[:36])
            sub_parts.append(
                f"drawtext=text='{safe_line}'"
                f":fontsize=52:fontcolor=yellow"
                f":x=(w-text_w)/2:y=h-240"
                f":enable='between(t,{t_s:.1f},{t_e:.1f})'"
                f":borderw=4:bordercolor=black@0.95"
                f":shadowx=3:shadowy=3:shadowcolor=black@0.8"
            )
        print(f"[VIRAL] 자막 {len(subtitle_lines)}줄 (y=h-240, 52px 노란색+박스)")

        # ── Ken Burns + 색상보정 VF 구성 ─────────────────────────────────────
        D = IMG_DUR
        _kb_pans = [
            f"crop=1080:1920:x='min(540,max(0,540*mod(t,{D})/{D}))':y=480",
            f"crop=1080:1920:x='min(540,max(0,540*(1-mod(t,{D})/{D})))':y=480",
            f"crop=1080:1920:x=270:y='min(960,max(0,960*mod(t,{D})/{D}))'",
            f"crop=1080:1920:x=270:y='min(960,max(0,960*(1-mod(t,{D})/{D})))'",
            f"crop=1080:1920:x='min(540,max(0,540*mod(t,{D})/{D}))':y='min(480,max(0,480*mod(t,{D})/{D}))'",
        ]
        kb_pan  = random.choice(_kb_pans)
        base_vf = (
            f"scale=1620:2880:force_original_aspect_ratio=increase,"
            f"{kb_pan},"
            f"eq=brightness=0.15:contrast=1.2:saturation=1.7"
        )
        sub_vf  = base_vf + "," + ",".join(sub_parts) + ",format=yuv420p"
        print(f"[VIRAL] Ken Burns+색상보정: {kb_pan[:40]}...")

        # concat demuxer 입력 (input 0) — "ffmpeg -y" 포함 필수
        concat_in = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt]

        def _try(label, cmd, t=120):
            if os.path.exists(video_path):
                os.remove(video_path)
            print(f"[VIRAL] 전략: {label}")
            _run_ffmpeg(cmd, log_path, timeout=t)
            exists = os.path.exists(video_path)
            size   = os.path.getsize(video_path) if exists else 0
            ok     = exists and size > 2000
            print(f"[VIRAL] {'✅ 성공' if ok else '❌ 실패'} ({label}): {size}B")
            if not ok and exists:
                os.remove(video_path)
            return ok

        # ── 전략 실행 ─────────────────────────────────────────────────────────

        if audio_path and os.path.exists(audio_path):
            # 전략 1: 슬라이드쇼 + 자막 + 음성 + BGM
            fc1 = (
                "[1:a]volume=1.0[voice];"
                "[2:a]volume=0.25[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first[aout]"
            )
            cmd1 = (
                concat_in + ["-i", audio_path] +
                ["-f", "lavfi", "-i", bgm_lavfi] +
                ["-filter_complex", fc1] +
                ["-vf", sub_vf] +
                ["-map", "0:v", "-map", "[aout]"] +
                vid_enc + aud_enc + ["-t", "45", "-shortest", video_path]
            )
            if _try("concat+sub+voice+bgm", cmd1):
                return video_path

            # 전략 2: 슬라이드쇼 + 자막 + 음성 (BGM 없음)
            cmd2 = (
                concat_in + ["-i", audio_path] +
                ["-vf", sub_vf] +
                ["-map", "0:v", "-map", "1:a"] +
                vid_enc + aud_enc + ["-t", "45", "-shortest", video_path]
            )
            if _try("concat+sub+voice", cmd2):
                return video_path

            # 전략 3: 슬라이드쇼 + 음성 (자막 없음)
            cmd3 = (
                concat_in + ["-i", audio_path] +
                ["-vf", base_vf] +
                ["-map", "0:v", "-map", "1:a"] +
                vid_enc + aud_enc + ["-t", "45", "-shortest", video_path]
            )
            if _try("concat+voice", cmd3):
                return video_path

        # 전략 4: 무음 슬라이드쇼
        cmd4 = (
            concat_in +
            ["-vf", base_vf] +
            ["-map", "0:v"] +
            vid_enc + ["-t", str(n * IMG_DUR), video_path]
        )
        if _try("concat-only", cmd4):
            return video_path

        print("[VIRAL] 모든 슬라이드쇼 전략 실패 → create_simple_video 폴백")
        return create_simple_video(content_data)

    except Exception as e:
        print(f"[ERROR] create_viral_shorts 예외: {e}")
        print(traceback.format_exc())
        return create_simple_video(content_data)


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


# ── YouTube SEO + 썸네일 최적화 ──────────────────────────────────────────────

def _generate_seo_title(title: str, narration: str) -> str:
    """Claude API로 CTR 최적화 제목 생성. 실패 시 원본 반환."""
    prompt = (
        f"아래 유튜브 쇼츠 제목을 CTR이 높아지도록 리라이팅해줘.\n"
        f"원본: {title}\n내용: {narration[:80]}\n\n"
        "규칙: 한국어, 100자 이내, 숫자/결과/궁금증 포함, 제목만 반환."
    )
    result = ask_claude(prompt, 200)
    if result:
        result = result.strip().strip('"').strip("'").strip()
        if result and len(result) <= 100:
            return result
    return title


def _create_thumbnail(title_en: str, ts: str | None = None) -> str | None:
    """FFmpeg로 YouTube 썸네일 생성 (1280x720 JPG). 강렬한 컬러 배경 디자인."""
    if ts is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out      = os.path.join(IMAGES_DIR, f"thumbnail_{ts}.jpg")
    log_path = os.path.join(IMAGES_DIR, f"thumb_{ts}.log")

    # 배경/강조색 랜덤 선택 (클릭률 높은 조합)
    schemes = [
        ("0x0d1b2a", "0xffd700", "0xff6b35"),  # 딥네이비 + 골드 + 오렌지
        ("0x1a0533", "0x00ff9d", "0xff3cac"),  # 딥퍼플 + 민트 + 핑크
        ("0x0a2200", "0xadff2f", "0xffd700"),  # 딥그린 + 라임 + 골드
        ("0x1a0000", "0xff4444", "0xffd700"),  # 딥레드 + 레드 + 골드
        ("0x001a33", "0x00d4ff", "0xffd700"),  # 딥블루 + 하늘 + 골드
    ]
    bg, accent, accent2 = random.choice(schemes)

    # 제목 두 줄 분리 (단어 경계 기준)
    words = _ascii_only(title_en).split()
    mid   = max(1, len(words) // 2)
    line1 = _ffmpeg_escape(" ".join(words[:mid])[:30])
    line2 = _ffmpeg_escape(" ".join(words[mid:])[:30])

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={bg}:size=1280x720:rate=1",
        "-vf", (
            # 상단 강조 바
            f"drawbox=x=0:y=0:w=1280:h=10:color={accent}:t=fill,"
            # 하단 강조 바
            f"drawbox=x=0:y=710:w=1280:h=10:color={accent}:t=fill,"
            # 좌측 수직 강조 바
            f"drawbox=x=0:y=0:w=12:h=720:color={accent2}:t=fill,"
            # 제목 첫째 줄 (흰색)
            f"drawtext=text='{line1}':fontsize=88:fontcolor=white"
            ":x=(w-text_w)/2:y=200"
            ":borderw=5:bordercolor=black@0.95"
            ":shadowx=4:shadowy=4:shadowcolor=black@0.7,"
            # 제목 둘째 줄 (강조색)
            f"drawtext=text='{line2}':fontsize=88:fontcolor={accent}"
            ":x=(w-text_w)/2:y=315"
            ":borderw=5:bordercolor=black@0.95"
            ":shadowx=4:shadowy=4:shadowcolor=black@0.7,"
            # 채널 뱃지 배경
            "drawbox=x=50:y=620:w=320:h=60:color=white@0.12:t=fill,"
            # 채널명
            f"drawtext=text='@future.minute':fontsize=32:fontcolor={accent}"
            ":x=70:y=634:borderw=2:bordercolor=black@0.6"
        ),
        "-frames:v", "1",
        out
    ]
    try:
        _run_ffmpeg(cmd, log_path, timeout=30)
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            print(f"[THUMB] 생성 완료: {out}")
            return out
    except Exception as e:
        print(f"[THUMB] 생성 실패: {e}")
    return None


def optimize_youtube_metadata(
    video_id: str,
    optimized_title: str,
    description: str,
    tags: list,
    thumbnail_path: str | None = None,
) -> dict:
    """YouTube 영상 제목/설명/태그 업데이트 + 썸네일 설정."""
    if not GOOGLE_AVAILABLE:
        return {"status": "skipped", "message": "google_packages_not_installed"}
    try:
        svc, err = get_youtube_service()
        if not svc:
            return {"status": "error", "message": err}

        # 제목/설명/태그 업데이트
        body = {
            "id": video_id,
            "snippet": {
                "title":       optimized_title[:100],
                "description": description[:5000],
                "tags":        (tags or [])[:30],
                "categoryId":  "22",
                "defaultLanguage": "ko",
            },
        }
        svc.videos().update(part="snippet", body=body).execute()
        print(f"[META] 제목 업데이트: {optimized_title[:50]}")

        # 썸네일 업로드 (있을 경우)
        if thumbnail_path and os.path.exists(thumbnail_path) and MediaFileUpload:
            media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            svc.thumbnails().set(videoId=video_id, media_body=media).execute()
            print(f"[META] 썸네일 업로드: {thumbnail_path}")

        return {
            "status":   "success",
            "video_id": video_id,
            "title":    optimized_title,
            "thumbnail": thumbnail_path or "없음",
        }
    except Exception as e:
        print(f"[META] 업데이트 실패: {e}")
        return {"status": "error", "message": str(e)}


# ── Instagram Reels 준비 ──────────────────────────────────────────────────────

def _generate_instagram_caption(title: str, narration: str, tags: list) -> str:
    """YouTube 콘텐츠 → Instagram 캡션 + 해시태그 자동 생성 (최대 2200자)."""
    prompt = (
        f"Instagram Reels 캡션을 생성해줘.\n제목: {title}\n내용: {narration[:120]}\n\n"
        "형식: 첫줄 이모지 포함 카피, 2~3줄 핵심 요약, 해시태그 12개. 500자 이내. 캡션만 반환."
    )
    result = ask_claude(prompt, 400)
    if result and len(result.strip()) > 10:
        return result.strip()[:2200]
    # 폴백 기본 캡션
    ht = " ".join(f"#{t}" for t in (tags or [])[:5])
    return (
        f"🤖 {title}\n\n{narration[:200]}\n\n"
        f"{ht} #AI #부업 #수익화 #쇼츠 #인스타릴스 #자동화 #재테크 #미래직업"
    )


def _convert_resolution_for_instagram(video_path: str) -> str | None:
    """YouTube 1080x1920 → Instagram Reels 파일 준비.
    Reels는 9:16(1080x1920) 그대로 지원하므로 파일 복사만 수행."""
    import shutil
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(VIDEOS_DIR, f"instagram_{ts}.mp4")
    try:
        shutil.copy2(video_path, out)
        print(f"[INSTAGRAM] Reels 파일 준비: {out}")
        return out
    except Exception as e:
        print(f"[INSTAGRAM] 파일 복사 실패: {e}")
        return None


def upload_to_instagram_reels(video_path: str, caption: str) -> dict:
    """Instagram Reels 업로드 (Meta Graph API).
    INSTAGRAM_ACCESS_TOKEN 환경변수가 설정되면 즉시 활성화.
    현재는 Render ephemeral FS → 외부 공개 URL이 없어 CDN 연동 필요."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    ig_id = os.getenv("INSTAGRAM_USER_ID", "").strip()

    if not token:
        return {
            "status":  "pending",
            "message": "INSTAGRAM_ACCESS_TOKEN 환경변수 설정 후 즉시 활성화",
        }
    if not ig_id:
        return {
            "status":  "pending",
            "message": "INSTAGRAM_USER_ID 환경변수 설정 필요",
        }

    # Meta Graph API Reels 업로드는 공개 CDN URL 필요
    # (Render ephemeral FS는 외부 접근 불가 → S3/Cloudinary 연동 후 완전 활성화)
    return {
        "status":  "pending",
        "message": "CDN URL 필요 (Render ephemeral FS 미지원). S3 연동 후 활성화 예정.",
        "caption_preview": caption[:100],
    }


def video_package_json():
    # 오늘의 트렌드 카테고리 주입
    today_topic = select_todays_topic()
    category    = today_topic["category"]
    kw_sample   = ", ".join(today_topic["keywords"][:3])

    prompt = f"""
유튜브 쇼츠/릴스용 30~45초 영상 데이터를 JSON 형식으로 정확하게 생성해줘.

오늘의 트렌드 카테고리: [{category}]
핵심 키워드 예시: {kw_sample}
위 카테고리와 키워드에 맞는 주제로 만들어줘.

반드시 이 JSON 형식으로만 응답:
{{
  "title": "한국어 영상 제목 (30자 이내, 클릭률 높은 제목, 숫자/이모지 활용)",
  "title_en": "English title for video overlay (max 40 chars, ASCII only)",
  "description": "YouTube 설명 (150자 이내, 핵심 키워드 포함)",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "keywords": ["english keyword1", "keyword2", "keyword3"],
  "narration": "30~45초 분량의 나레이션 (120~150자, 자연스럽고 몰입감 있는 한국어, 반드시 150자 이하)",
  "narration_en": "English narration summary for overlay (max 200 chars, ASCII only, 4-5 key sentences)"
}}

keywords는 Unsplash 이미지 검색용 영어 키워드 2~3개 (예: ["AI technology", "money success", "future"])

JSON 외에 다른 텍스트는 절대 포함하지 말것!
"""
    print("[INFO] video_package_json: AI 요청 중...")
    response = ask_ai(prompt, 1200)
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

        # Step 2: Unsplash 이미지 + 슬라이드쇼 영상 생성
        pexels_on = bool(os.getenv("PEXELS_API_KEY"))
        _append_log(job_id, f"2️⃣ 영상 생성 중... (Pexels={'✅ ON' if pexels_on else '⏸ OFF - 키 없음'})")
        video_path = create_viral_shorts(content_data)
        if not video_path:
            _append_log(job_id, "❌ 영상 생성 실패 (ffmpeg 확인 필요)")
            _update_job(job_id, status="error",
                        error="영상 생성 실패", content=content_data)
            return
        _append_log(job_id, f"✅ 영상 완료: {video_path}")
        _update_job(job_id, video_path=video_path)

        # Step 3: YouTube 업로드
        title_ko  = content_data.get("title", "")
        title_en  = content_data.get("title_en", "") or _ascii_only(title_ko)
        narration = content_data.get("narration", "")
        tags      = content_data.get("tags", [])
        desc      = content_data.get("description", "")

        _append_log(job_id, "3️⃣ YouTube 업로드 시도 중...")
        upload = upload_to_youtube(video_path, title_ko, desc, tags)
        yt_status = upload.get("status")
        if yt_status == "success":
            _append_log(job_id, f"✅ YouTube 업로드 완료!\n🔗 {upload.get('url')}")
        elif yt_status == "auth_required":
            _append_log(job_id,
                f"⚠️ YouTube 인증 필요: {upload.get('message', '')}")
        elif yt_status == "skipped":
            _append_log(job_id, "💾 YouTube 건너뜀 (Google 패키지 미설치)")
        else:
            msg = upload.get("message", "알 수 없는 오류")
            if "uploadLimitExceeded" in msg:
                _append_log(job_id, "⚠️ YouTube 일일 업로드 한도 초과 (자정 UTC=09:00 KST 자동 초기화)")
            else:
                _append_log(job_id, f"❌ YouTube 업로드 실패: {msg}")

        # Step 4: YouTube 메타 최적화 (업로드 성공 시에만)
        meta_result = {}
        if yt_status == "success":
            video_id = upload.get("video_id", "")
            ts_now   = datetime.now().strftime("%Y%m%d_%H%M%S")

            _append_log(job_id, "4️⃣ YouTube SEO 제목 최적화 중...")
            seo_title = _generate_seo_title(title_ko, narration)
            _append_log(job_id, f"✅ SEO 제목: {seo_title[:40]}")

            _append_log(job_id, "5️⃣ 썸네일 생성 중 (FFmpeg)...")
            thumb_path = _create_thumbnail(title_en or title_ko, ts_now)
            if thumb_path:
                _append_log(job_id, f"✅ 썸네일 생성: {thumb_path}")
            else:
                _append_log(job_id, "⚠️ 썸네일 생성 실패 (원본 제목 사용)")

            _append_log(job_id, "6️⃣ YouTube 제목/썸네일 업데이트 중...")
            meta_result = optimize_youtube_metadata(
                video_id, seo_title, desc, tags, thumb_path
            )
            if meta_result.get("status") == "success":
                _append_log(job_id, f"✅ 메타 업데이트 완료: {seo_title[:30]}")
            else:
                _append_log(job_id, f"⚠️ 메타 업데이트 실패: {meta_result.get('message','')}")

        # Step 5: Instagram Reels 준비
        _append_log(job_id, "7️⃣ Instagram Reels 준비 중...")
        ig_caption  = _generate_instagram_caption(title_ko, narration, tags)
        ig_vid_path = _convert_resolution_for_instagram(video_path)
        ig_result   = upload_to_instagram_reels(ig_vid_path or video_path, ig_caption)
        ig_status   = ig_result.get("status")
        if ig_status == "success":
            _append_log(job_id, f"✅ Instagram 업로드 완료!")
        else:
            _append_log(job_id, f"📱 Instagram: {ig_result.get('message','')}")

        _update_job(job_id, status="done", youtube=upload,
                    youtube_meta=meta_result, instagram=ig_result)

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


# ===== YouTube 트렌드 학습 + 다국어 생성 (v18) =====

def get_youtube_trends(keyword: str, region: str = "KR") -> dict:
    """YouTube 지역별 트렌드 기반 고CTR 제목 생성."""
    try:
        prompt = (
            f"YouTube {region} 트렌드를 반영해서 '{keyword}' 키워드로 "
            f"고CTR 제목을 3개 만들어줘.\n"
            f"조건: 15~60자, 숫자/감정사/궁금증 유도, {region} 시장 특성 반영, SEO 최적화.\n"
            f"형식: 제목1\\n제목2\\n제목3"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        titles = [t.strip() for t in resp.content[0].text.split("\n") if t.strip()]
        return {
            "region": region,
            "keyword": keyword,
            "titles": titles,
            "best": titles[0] if titles else keyword,
        }
    except Exception as e:
        logger.error(f"트렌드 분석 실패: {e}")
        return {"error": str(e), "best": keyword}


def generate_multilingual_content(keyword: str) -> dict:
    """한글/영문 YouTube 제목 + 해시태그 동시 생성."""
    try:
        kr = get_youtube_trends(keyword, region="KR")
        en = get_youtube_trends(keyword, region="US")
        return {
            "korean":  {
                "titles": kr.get("titles", []),
                "best":   kr.get("best", keyword),
                "hashtags": ["#AI", "#기술", "#수익화", "#자동화"],
            },
            "english": {
                "titles": en.get("titles", []),
                "best":   en.get("best", keyword),
                "hashtags": ["#AI", "#Technology", "#Automation", "#Monetization"],
            },
        }
    except Exception as e:
        logger.error(f"다국어 생성 실패: {e}")
        return {"error": str(e)}


# ===== 트렌드 기반 자동 주제 시스템 (v20) =====

_TRENDING_CATEGORIES = [
    {"category": "AI",        "keywords": ["AI로 번다", "ChatGPT 최신", "AI 자동화", "머신러닝 쉽게", "AI 투자"]},
    {"category": "부업/수익", "keywords": ["부업으로 돈버는법", "하루 10만원 버는법", "집에서 월 300만원", "자동화 수익", "투자로 돈벌기"]},
    {"category": "기술",      "keywords": ["코딩 쉽게배우기", "파이썬 10분", "개발자 월급", "웹개발 초보", "프로그래밍 팁"]},
    {"category": "건강/운동", "keywords": ["복부지방 제거", "3주 다이어트", "홈트 효과", "근력운동 팁", "건강한 식단"]},
    {"category": "금융/투자", "keywords": ["주식 초보자", "암호화폐 뉴스", "부동산 투자", "적금 고금리", "재테크 팁"]},
    {"category": "일상/라이프","keywords": ["생활팁 20가지", "시간 절약법", "돈절약 꿀팁", "집정리 미니멀", "일중독 탈출"]},
    {"category": "해외/트렌드","keywords": ["해외 바이럴 영상", "외국인 반응", "국제 뉴스", "글로벌 트렌드", "해외문화"]},
    {"category": "엔터",      "keywords": ["게임 최신정보", "유튜브 인기", "넷플릭스 추천", "영화 리뷰", "연예뉴스"]},
]

_CATEGORY_HASHTAGS = {
    "AI":        ["#AI", "#ChatGPT", "#자동화", "#기술", "#미래"],
    "부업/수익": ["#부업", "#돈버는법", "#수익화", "#자유", "#재테크"],
    "기술":      ["#개발", "#코딩", "#프로그래밍", "#파이썬", "#웹개발"],
    "건강/운동": ["#다이어트", "#운동", "#건강", "#헬스", "#피트니스"],
    "금융/투자": ["#투자", "#재테크", "#주식", "#금융", "#부자"],
    "일상/라이프":["#라이프팁", "#일상", "#꿀팁", "#미니멀", "#자기계발"],
    "해외/트렌드":["#해외", "#트렌드", "#바이럴", "#글로벌", "#반응"],
    "엔터":      ["#게임", "#유튜브", "#영화", "#넷플릭스", "#엔터"],
}


def select_todays_topic() -> dict:
    """날짜 기반으로 매일 다른 카테고리 순환."""
    idx = (datetime.now().day - 1) % len(_TRENDING_CATEGORIES)
    return _TRENDING_CATEGORIES[idx]


def generate_trending_title(keyword: str = None) -> str:
    """트렌드 기반 고CTR 제목 생성 (Claude Haiku)."""
    if not keyword:
        keyword = select_todays_topic()["keywords"][0]
    try:
        prompt = (
            f"YouTube 한국 트렌드 키워드 '{keyword}'로 클릭률 높은 제목 3개 만들어줘.\n"
            f"조건: 15~60자, 숫자/의외성/호기심 포함, 이모지 활용, '초보자도'/'5분만에'/'대공개' 같은 강렬한 단어.\n"
            f"형식: 제목1\\n제목2\\n제목3"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        titles = [t.strip() for t in resp.content[0].text.split("\n") if t.strip()]
        return titles[0] if titles else f"【대공개】{keyword}"
    except Exception as e:
        logger.error(f"트렌드 제목 생성 실패: {e}")
        return f"【반드시 봐야 할】{keyword}"


def generate_trending_hashtags(category: str = None) -> list:
    """카테고리별 해시태그 + 영문 태그 조합."""
    if not category:
        category = select_todays_topic()["category"]
    return _CATEGORY_HASHTAGS.get(category, ["#트렌드", "#필수시청"])


def generate_trending_description(title: str, category: str = None) -> str:
    """트렌드 기반 YouTube 설명 자동 생성."""
    if not category:
        category = select_todays_topic()["category"]
    tags = " ".join(generate_trending_hashtags(category))
    return (
        f"{title}\n\n"
        f"오늘의 트렌드 주제: {category}\n\n"
        f"이 영상에서 다루는 내용:\n"
        f"✓ 최신 트렌드 정보\n"
        f"✓ 실생활 바로 적용\n"
        f"✓ 전문가 팁 포함\n\n"
        f"구독 & 알림 설정으로 매일 새 콘텐츠를 받아보세요!\n\n"
        f"{tags}"
    )


def get_trending_metadata() -> dict:
    """오늘의 트렌드 기반 영상 메타데이터 일괄 생성."""
    topic    = select_todays_topic()
    category = topic["category"]
    keyword  = topic["keywords"][0]
    title    = generate_trending_title(keyword)
    return {
        "title":       title,
        "description": generate_trending_description(title, category),
        "keywords":    topic["keywords"],
        "hashtags":    generate_trending_hashtags(category),
        "category":    category,
    }


# ===== v20 준비: Pexels 영상 검색 (PEXELS_API_KEY 설정 시 활성화) =====

def get_pexels_videos(keyword: str, count: int = 5) -> list:
    """Pexels API에서 저작권 무료 영상 URL 목록 반환."""
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        logger.warning("PEXELS_API_KEY 미설정 — v20 배포 시 필요")
        return []
    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "per_page": count},
            timeout=15,
        )
        if resp.status_code == 200:
            return [
                v["video_files"][0]["link"]
                for v in resp.json().get("videos", [])
                if v.get("video_files")
            ]
    except Exception as e:
        logger.error(f"Pexels 영상 검색 실패: {e}")
    return []


def _fetch_pexels_clips(keywords: list, clip_sec: float = 5.5, max_clips: int = 2) -> list:
    """
    Pexels에서 영상 URL 가져와 clip_sec짜리 세로(1080×1920) 클립으로 전처리.
    PEXELS_API_KEY 없으면 빈 리스트 반환 (이미지 전용 모드로 폴백).
    """
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return []

    query = " ".join(str(k) for k in keywords[:2]) if keywords else "technology"
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clips = []

    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": max_clips + 2, "orientation": "portrait"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[PEXELS] HTTP {resp.status_code}")
            return []

        for idx, v in enumerate(resp.json().get("videos", [])):
            if len(clips) >= max_clips:
                break
            files = v.get("video_files", [])
            # SD 화질 우선 (용량 작고 빠름)
            target = next((f for f in files if f.get("quality") == "sd"), files[0] if files else None)
            if not target:
                continue

            video_url = target["link"]
            raw_path  = os.path.join(IMAGES_DIR, f"pexels_raw_{ts}_{idx}.mp4")
            clip_path = os.path.join(IMAGES_DIR, f"pexels_clip_{ts}_{idx}.mp4")

            # 원본 다운로드
            try:
                dl = requests.get(video_url, timeout=30, stream=True)
                if dl.status_code != 200:
                    continue
                with open(raw_path, "wb") as f:
                    for chunk in dl.iter_content(65536):
                        f.write(chunk)
                if os.path.getsize(raw_path) < 10000:
                    continue
            except Exception as e:
                logger.warning(f"[PEXELS] 다운로드 실패: {e}")
                continue

            # 전처리: clip_sec 자르기 + 세로 1080×1920 스케일 + 색상보정 + 오디오 제거
            cmd = [
                "ffmpeg", "-y", "-ss", "0", "-i", raw_path,
                "-t", str(clip_sec),
                "-vf", (
                    f"scale=1620:2880:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920,"
                    f"eq=brightness=0.15:contrast=1.2:saturation=1.7,"
                    f"format=yuv420p"
                ),
                "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-threads", "1",
                clip_path,
            ]
            log_p = os.path.join(IMAGES_DIR, f"pexels_clip_{ts}_{idx}.log")
            _run_ffmpeg(cmd, log_p, timeout=60)

            if os.path.exists(clip_path) and os.path.getsize(clip_path) > 5000:
                clips.append(clip_path)
                logger.info(f"[PEXELS] 클립 준비: {clip_path}")
            try:
                os.remove(raw_path)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"[PEXELS] 클립 준비 실패: {e}")

    return clips


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


@app.route("/batch-video", methods=["POST"])
def batch_video():
    """배치 영상 제작 (최대 5개, 순차 실행). POST body: {"count": 5}"""
    data  = request.get_json(silent=True) or {}
    count = min(max(int(data.get("count", 5)), 1), 5)

    job_ids = []
    for i in range(count):
        _cleanup_old_jobs()
        job_id = uuid.uuid4().hex[:12]
        with _jobs_lock:
            _jobs[job_id] = {
                "status":     "queued",
                "logs":       [f"⏳ 배치 {i+1}/{count} — 이전 작업 완료 대기 중..."],
                "created_at": datetime.now().isoformat(),
                "content":    None,
                "video_path": None,
                "youtube":    None,
                "error":      None,
                "batch_index": i + 1,
                "batch_total": count,
            }
        job_ids.append(job_id)

    def _run_batch_sequential(ids):
        for idx, jid in enumerate(ids):
            print(f"[BATCH] {idx+1}/{len(ids)} 시작: {jid}")
            _update_job(jid, logs=[f"🎬 배치 {idx+1}/{len(ids)} 시작..."])
            _run_video_job(jid)
            if idx < len(ids) - 1:
                print(f"[BATCH] {idx+1} 완료. 30초 후 다음 시작...")
                time.sleep(30)
        print(f"[BATCH] 전체 {len(ids)}개 완료")

    t = threading.Thread(target=_run_batch_sequential, args=(job_ids,), daemon=True)
    t.start()
    print(f"[INFO] 배치 영상 {count}개 시작: {job_ids}")

    return jsonify({
        "status":    "queued",
        "batch_size": count,
        "job_ids":   job_ids,
        "message":   f"{count}개 영상 순차 제작 시작 (각 완료 후 30초 간격)",
    }), 202


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
        "version":               "v24-debug-log",
        "unsplash_key_set":      bool(os.getenv("UNSPLASH_API_KEY")),
        "pexels_key_set":        bool(os.getenv("PEXELS_API_KEY")),
        "elevenlabs_key_set":    bool(os.getenv("ELEVENLABS_API_KEY")),
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


@app.route("/last-log", methods=["GET"])
def last_log():
    """최근 FFmpeg 로그 반환 (디버그용)"""
    import glob
    logs = sorted(glob.glob(os.path.join(IMAGES_DIR, "ffmpeg_viral_*.log")), reverse=True)
    if not logs:
        return "No ffmpeg_viral logs found", 200
    with open(logs[0], encoding="utf-8", errors="replace") as f:
        content = f.read()
    return f"=== {logs[0]} ===\n\n" + content[-8000:], 200


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


@app.route("/test-unsplash", methods=["GET"])
def test_unsplash():
    """Unsplash API 연결 및 이미지 다운로드 진단"""
    api_key = os.getenv("UNSPLASH_API_KEY", "").strip()
    result  = {
        "timestamp":       datetime.now().isoformat(),
        "api_key_set":     bool(api_key),
        "api_key_preview": api_key[:8] + "..." if api_key else "(없음)",
    }

    if not api_key:
        result["status"] = "error"
        result["message"] = "UNSPLASH_API_KEY 환경변수 없음"
        return jsonify(result), 200

    # ── Unsplash 검색 테스트 ────────────────────────────────────────────────
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": "AI technology", "per_page": 3, "orientation": "portrait"},
            headers={"Authorization": f"Client-ID {api_key}"},
            timeout=15,
        )
        result["search_http_status"] = resp.status_code
        if resp.status_code != 200:
            result["status"]  = "error"
            result["message"] = f"검색 실패 HTTP {resp.status_code}"
            result["body"]    = resp.text[:300]
            return jsonify(result), 200

        photos = resp.json().get("results", [])
        result["search_total"]    = resp.json().get("total", 0)
        result["photos_returned"] = len(photos)

        if not photos:
            result["status"]  = "error"
            result["message"] = "검색 결과 0개 (orientation=portrait 필터 확인)"
            return jsonify(result), 200

        # ── 이미지 1개 실제 다운로드 테스트 ─────────────────────────────────
        img_url = photos[0].get("urls", {}).get("regular", "")
        result["sample_url"] = img_url[:80] + "..." if img_url else ""

        if img_url:
            try:
                ir = requests.get(img_url, timeout=20, stream=True)
                result["download_http_status"] = ir.status_code
                if ir.status_code == 200:
                    size = 0
                    for chunk in ir.iter_content(8192):
                        size += len(chunk)
                        if size > 50000:   # 50KB면 충분
                            break
                    result["download_size_bytes"] = size
                    result["download_ok"]         = size > 5000
                else:
                    result["download_ok"] = False
            except Exception as e:
                result["download_ok"]    = False
                result["download_error"] = str(e)

        result["status"]  = "ok"
        result["message"] = "Unsplash 정상"
    except Exception as e:
        result["status"]        = "error"
        result["message"]       = str(e)
        result["traceback_tail"] = traceback.format_exc()[-400:]

    return jsonify(result), 200


@app.route("/stats", methods=["GET"])
def stats():
    """오늘 생성된 영상 통계 (메모리 기반, 재시작 시 초기화)"""
    today = datetime.now().strftime("%Y-%m-%d")
    with _jobs_lock:
        jobs_today = [j for j in _jobs.values()
                      if j.get("created_at", "").startswith(today)]

    total   = len(jobs_today)
    done    = sum(1 for j in jobs_today if j.get("status") == "done")
    error   = sum(1 for j in jobs_today if j.get("status") == "error")
    running = sum(1 for j in jobs_today if j.get("status") == "running")
    queued  = sum(1 for j in jobs_today if j.get("status") == "queued")
    yt_ok   = sum(1 for j in jobs_today
                  if (j.get("youtube") or {}).get("status") == "success")

    return jsonify({
        "date":             today,
        "total_jobs":       total,
        "done":             done,
        "youtube_uploaded": yt_ok,
        "error":            error,
        "running":          running,
        "queued":           queued,
        "all_jobs_count":   len(_jobs),
        "timestamp":        datetime.now().isoformat(),
    }), 200


@app.route("/reset")
def reset():
    session.pop("history", None)
    return '<h2>대화기록 초기화 완료</h2><a href="/">JARVIS-X 돌아가기</a>'


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_ENV") != "production"
    app.run(debug=debug_mode, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
