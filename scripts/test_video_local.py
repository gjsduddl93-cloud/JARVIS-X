"""
로컬 FFmpeg 영상 생성 테스트 스크립트
실행: python scripts/test_video_local.py
"""
import os
import sys
import subprocess
import json

OUTPUT = "test_output.mp4"

def find_font():
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/NanumGothic.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            print(f"[FONT] {p}")
            return p
    print("[FONT] 없음 — 단색 배경만 사용")
    return None

def esc(text):
    return (text
            .replace("\\", "\\\\")
            .replace("'",  "\\'")
            .replace(":",  "\\:")
            .replace("[",  "\\[")
            .replace("]",  "\\]")
            .replace("%",  "\\%"))

def build_vf(font, title, narration):
    parts = [
        f"drawtext=fontfile='{font}':text='{esc(title[:35])}'"
        f":fontsize=72:fontcolor=white:x=(w-text_w)/2:y=280"
        f":box=1:boxcolor=black@0.6:boxborderw=12"
    ]
    lines = [narration[i:i+22] for i in range(0, min(len(narration), 110), 22)]
    for idx, line in enumerate(lines):
        y = 460 + idx * 90
        parts.append(
            f"drawtext=fontfile='{font}':text='{esc(line)}'"
            f":fontsize=48:fontcolor=0xccddff:x=(w-text_w)/2:y={y}"
            f":box=1:boxcolor=black@0.4:boxborderw=8"
        )
    return ",".join(parts)

def main():
    title     = "JARVIS-X 테스트 영상"
    narration = "AI가 자동으로 생성한 콘텐츠입니다. 유튜브 쇼츠용 영상."

    font = find_font()

    base_input  = ["ffmpeg", "-y", "-f", "lavfi",
                   "-i", "color=c=0x0d0d1a:size=1080x1920:rate=24"]
    base_output = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                   "-t", "10", "-pix_fmt", "yuv420p", "-threads", "1", OUTPUT]

    if font:
        vf  = build_vf(font, title, narration)
        cmd = base_input + ["-vf", vf] + base_output
        print("[CMD] drawtext 포함")
    else:
        cmd = base_input + base_output
        print("[CMD] 단색 배경 (폰트 없음)")

    print(f"[RUN] {' '.join(cmd[:6])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"[RC]  {result.returncode}")
    if result.returncode != 0:
        print("[STDERR TAIL]")
        print(result.stderr[-400:])
        sys.exit(1)

    size = os.path.getsize(OUTPUT)
    print(f"[OK]  {OUTPUT} ({size:,} bytes)")
    print(f"[OPEN] 파일 경로: {os.path.abspath(OUTPUT)}")

    # Windows에서 자동으로 영상 열기
    if sys.platform == "win32":
        os.startfile(os.path.abspath(OUTPUT))

if __name__ == "__main__":
    main()
