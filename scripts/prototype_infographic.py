"""
데이터 시각화/인포그래픽 콘텐츠 프로토타입 (3단계)
소재: 국가별 R&D 투자 비율(GDP 대비) TOP 6 — World Bank API 실데이터
스타일: 다크 배경 + 네온/그라디언트 + 막대 성장 애니메이션 + 숫자 카운트업

실행: python scripts/prototype_infographic.py
출력: prototype_output/infographic_prototype_v1.mp4

메모리 안전 원칙(2단계 조사 결과 반영):
- 프레임은 루프 안에서 하나씩 그려서 즉시 저장 후 Figure를 닫는다.
  전체 프레임을 리스트로 들고 있다가 한 번에 쓰는 방식은 사용하지 않는다.
"""

import os
import sys
import json
import shutil
import subprocess
import time
import urllib.request
import urllib.error

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.font_manager import FontProperties

# ── 경로 ──────────────────────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH   = os.path.join(ROOT, "fonts", "NanumGothicBold.ttf")
OUT_DIR     = os.path.join(ROOT, "prototype_output")
FRAMES_DIR  = os.path.join(OUT_DIR, "_frames_tmp")
OUT_MP4     = os.path.join(OUT_DIR, "infographic_prototype_v1.mp4")

FONT   = FontProperties(fname=FONT_PATH)
FONT_S = lambda size: FontProperties(fname=FONT_PATH, size=size)

# ── 영상 스펙 ─────────────────────────────────────────────────────────
W, H   = 1080, 1920
DPI    = 100
FPS    = 24
DUR_S  = 15
TOTAL_FRAMES = FPS * DUR_S          # 360

PHASE_INTRO_END = int(FPS * 1.0)              # 0~23   : 타이틀 페이드인
PHASE_GROW_END  = PHASE_INTRO_END + int(FPS * 10.0)  # 24~263 : 막대 성장 + 카운트업
# 264~359 : 홀드(4초)

# ── 컬러 (다크 + 네온) ────────────────────────────────────────────────
BG          = "#0a0a16"
NEON_KR     = "#00f5d4"   # 한국 강조 색 (네온 민트)
NEON_OTHER  = "#7b5cff"   # 나머지 국가 (네온 퍼플)
TEXT_MAIN   = "#f5f5ff"
TEXT_SUB    = "#9d9db8"


def fetch_worldbank_data():
    """World Bank API: R&D 지출 비율(GDP 대비), 최신 가용치."""
    countries = ["ISR", "KOR", "USA", "JPN", "DEU", "CHN"]
    url = (
        "https://api.worldbank.org/v2/country/" + ";".join(countries)
        + "/indicator/GB.XPD.RSDV.GD.ZS?format=json&per_page=200&mrnev=1"
    )
    name_ko = {
        "ISR": "이스라엘", "KOR": "대한민국", "USA": "미국",
        "JPN": "일본", "DEU": "독일", "CHN": "중국",
    }
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        rows = data[1]
        result = []
        for r in rows:
            iso3 = r["countryiso3code"]
            result.append({
                "code": iso3,
                "name": name_ko.get(iso3, r["country"]["value"]),
                "value": round(float(r["value"]), 2),
                "year": r["date"],
            })
        result.sort(key=lambda x: x["value"], reverse=True)
        print(f"[INFO] World Bank API 실데이터 {len(result)}개국 확보")
        return result
    except (urllib.error.URLError, Exception) as e:
        print(f"[WARN] World Bank API 실패({e}) — 직전 확인된 실측값으로 폴백")
        fallback = [
            {"code": "ISR", "name": "이스라엘", "value": 6.35, "year": "2023"},
            {"code": "KOR", "name": "대한민국", "value": 4.94, "year": "2023"},
            {"code": "USA", "name": "미국",     "value": 3.45, "year": "2023"},
            {"code": "JPN", "name": "일본",     "value": 3.44, "year": "2023"},
            {"code": "DEU", "name": "독일",     "value": 3.15, "year": "2023"},
            {"code": "CHN", "name": "중국",     "value": 2.58, "year": "2023"},
        ]
        return fallback


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def draw_frame(frame_idx, data, max_val):
    """프레임 1장을 그려서 반환할 Figure 객체(호출부에서 저장 후 close)."""
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # ── 진행률 계산 ──
    if frame_idx < PHASE_INTRO_END:
        intro_t = frame_idx / PHASE_INTRO_END
        title_alpha = ease_out_cubic(intro_t)
        grow_t = 0.0
    elif frame_idx < PHASE_GROW_END:
        title_alpha = 1.0
        grow_t = ease_out_cubic(
            (frame_idx - PHASE_INTRO_END) / (PHASE_GROW_END - PHASE_INTRO_END)
        )
    else:
        title_alpha = 1.0
        grow_t = 1.0

    # ── 타이틀 ──
    ax.text(0.5, 0.90, "국가별 R&D 투자 비율", ha="center", va="center",
             fontproperties=FONT_S(52), color=TEXT_MAIN, alpha=title_alpha)
    ax.text(0.5, 0.865, "(GDP 대비, World Bank · 2023년 기준)", ha="center", va="center",
             fontproperties=FONT_S(24), color=TEXT_SUB, alpha=title_alpha)

    if grow_t > 0.05:
        sub_alpha = min(1.0, grow_t * 2)
        ax.text(0.5, 0.815, "대한민국, OECD 국가 중 2위", ha="center", va="center",
                 fontproperties=FONT_S(34), color=NEON_KR, alpha=sub_alpha)

    # ── 막대 차트 (가로 막대, 6개국) ──
    n = len(data)
    top_y, bottom_y = 0.74, 0.20
    band_h = (top_y - bottom_y) / n
    bar_max_w = 0.62
    label_x = 0.06

    for i, d in enumerate(data):
        cy = top_y - band_h * (i + 0.5)
        is_kr = d["code"] == "KOR"
        color = NEON_KR if is_kr else NEON_OTHER

        cur_val = d["value"] * grow_t
        bar_w = bar_max_w * (cur_val / max_val)

        # 국가명
        ax.text(label_x, cy + band_h * 0.20, d["name"], ha="left", va="center",
                 fontproperties=FONT_S(30), color=TEXT_MAIN)

        bar_x0 = label_x
        bar_y0 = cy - band_h * 0.16
        bar_h  = band_h * 0.30

        # 네온 글로우(반투명 큰 막대를 뒤에 겹쳐서 발광 느낌)
        if bar_w > 0.002:
            for glow_i, (pad, a) in enumerate([(0.012, 0.10), (0.006, 0.18)]):
                ax.add_patch(mpatches.FancyBboxPatch(
                    (bar_x0, bar_y0 - pad), bar_w, bar_h + pad * 2,
                    boxstyle="round,pad=0,rounding_size=0.01",
                    linewidth=0, facecolor=color, alpha=a))
            ax.add_patch(mpatches.FancyBboxPatch(
                (bar_x0, bar_y0), bar_w, bar_h,
                boxstyle="round,pad=0,rounding_size=0.008",
                linewidth=0, facecolor=color, alpha=0.95))

        # 숫자 카운트업 (막대 끝)
        ax.text(bar_x0 + bar_w + 0.02, cy, f"{cur_val:.2f}%",
                 ha="left", va="center",
                 fontproperties=FONT_S(28), color=color)

    ax.text(0.5, 0.06, "출처: World Bank Open Data · GB.XPD.RSDV.GD.ZS",
             ha="center", va="center", fontproperties=FONT_S(18), color=TEXT_SUB, alpha=0.7)

    return fig


def render_frames(data):
    os.makedirs(FRAMES_DIR, exist_ok=True)
    max_val = max(d["value"] for d in data) * 1.08

    for i in range(TOTAL_FRAMES):
        fig = draw_frame(i, data, max_val)
        frame_path = os.path.join(FRAMES_DIR, f"frame_{i:04d}.png")
        fig.savefig(frame_path, dpi=DPI)
        plt.close(fig)   # 프레임마다 즉시 해제 — 리스트에 누적 금지

        if i % 60 == 0:
            print(f"[RENDER] {i}/{TOTAL_FRAMES} 프레임 완료")

    print(f"[RENDER] 전체 {TOTAL_FRAMES} 프레임 완료")


def encode_video():
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[INFO] 시스템 ffmpeg 없음 — imageio-ffmpeg 번들 바이너리 사용: {ffmpeg_bin}")

    os.makedirs(OUT_DIR, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-framerate", str(FPS),
        "-i", os.path.join(FRAMES_DIR, "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        OUT_MP4,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] ffmpeg 인코딩 실패")
        print(result.stderr[-3000:])
        raise RuntimeError("ffmpeg encode failed")
    print(f"[INFO] 인코딩 완료: {OUT_MP4}")


def cleanup_frames():
    if os.path.isdir(FRAMES_DIR):
        shutil.rmtree(FRAMES_DIR)
        print(f"[CLEANUP] 임시 프레임 디렉터리 삭제: {FRAMES_DIR}")


def main():
    t_start = time.time()

    data = fetch_worldbank_data()
    for d in data:
        print(f"  {d['name']:6s} {d['value']:.2f}%  ({d['year']})")

    t_render_start = time.time()
    render_frames(data)
    t_render_end = time.time()

    encode_video()
    t_encode_end = time.time()

    cleanup_frames()

    total = time.time() - t_start
    print("\n=== 소요 시간 ===")
    print(f"프레임 렌더링: {t_render_end - t_render_start:.1f}초")
    print(f"ffmpeg 인코딩: {t_encode_end - t_render_end:.1f}초")
    print(f"전체:          {total:.1f}초")
    print(f"\n결과 파일: {OUT_MP4}")
    print(f"파일 크기: {os.path.getsize(OUT_MP4) / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
