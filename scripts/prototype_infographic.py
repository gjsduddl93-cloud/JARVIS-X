"""
데이터 시각화/인포그래픽 콘텐츠 (3단계 프로토타입 → 4단계 배치 통합 → 5단계 소재 풀 확장)
스타일: 다크 배경 + 네온/그라디언트 + 막대 성장 애니메이션 + 숫자 카운트업
렌더링/애니메이션 로직은 공통, 소재(TOPICS)별로 데이터 fetch + 타이틀/문구만 다르다.

실행: python scripts/prototype_infographic.py [topic_id]
출력: prototype_output/infographic_prototype_v1.mp4

메모리 안전 원칙(2단계 조사 결과 반영):
- 프레임은 루프 안에서 하나씩 그려서 즉시 저장 후 Figure를 닫는다.
  전체 프레임을 리스트로 들고 있다가 한 번에 쓰는 방식은 사용하지 않는다.

팩트체크 원칙(4단계에서 "세계 2위" 오표기를 겪은 뒤 확립):
- "세계 몇 위"는 반드시 전체 국가(지역 제외) 기준으로 재확인한 값만 쓴다.
- 전세계 기준으로 안 나오면 OECD 등 더 좁은 비교군으로 재확인하고, 그 비교군을
  문구에 명시한다(예: "OECD 국가 중 2위" ≠ "세계 2위").
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


def _fetch_worldbank(indicator, countries, name_ko, fallback, round_digits=2, year=None):
    """World Bank API 공통 fetch 헬퍼. 실패 시 직전 확인된 실측값(fallback)으로 폴백.
    year 지정 시 date=year로 명시 조회 — 일부 지표는 mrnev=1 조합에서 서버가
    400을 반환하는 게 확인되어(SL.UEM.TOTL.ZS, SE.TER.ENRR), 팩트체크로 이미
    확인된 정확한 연도를 명시하는 쪽이 더 안정적."""
    query = f"date={year}" if year else "mrnev=1"
    url = (
        "https://api.worldbank.org/v2/country/" + ";".join(countries)
        + f"/indicator/{indicator}?format=json&per_page=200&{query}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        rows = data[1]
        result = []
        for r in rows:
            iso3 = r["countryiso3code"]
            if r["value"] is None:
                continue
            result.append({
                "code":  iso3,
                "name":  name_ko.get(iso3, r["country"]["value"]),
                "value": round(float(r["value"]), round_digits),
                "year":  r["date"],
            })
        if not result:
            raise ValueError("빈 결과")
        print(f"[INFO] World Bank API({indicator}) 실데이터 {len(result)}개국 확보")
        return result
    except Exception as e:
        print(f"[WARN] World Bank API({indicator}) 실패({e}) — 직전 확인된 실측값으로 폴백")
        return fallback


# ── 소재 1: R&D 투자 비율 (GDP 대비) ─────────────────────────────────────
def fetch_rnd_gdp():
    countries = ["ISR", "KOR", "USA", "JPN", "DEU", "CHN"]
    name_ko = {"ISR": "이스라엘", "KOR": "대한민국", "USA": "미국",
               "JPN": "일본", "DEU": "독일", "CHN": "중국"}
    fallback = [
        {"code": "ISR", "name": "이스라엘", "value": 6.35, "year": "2023"},
        {"code": "KOR", "name": "대한민국", "value": 4.94, "year": "2023"},
        {"code": "USA", "name": "미국",     "value": 3.45, "year": "2023"},
        {"code": "JPN", "name": "일본",     "value": 3.44, "year": "2023"},
        {"code": "DEU", "name": "독일",     "value": 3.15, "year": "2023"},
        {"code": "CHN", "name": "중국",     "value": 2.58, "year": "2023"},
    ]
    result = _fetch_worldbank("GB.XPD.RSDV.GD.ZS", countries, name_ko, fallback)
    result.sort(key=lambda x: x["value"], reverse=True)
    return result


# ── 소재 2: 첨단기술 수출 비중 (제조업 수출 대비) ─────────────────────────
def fetch_tech_exports():
    countries = ["ISL", "IRL", "ISR", "KOR", "CHE", "GBR"]
    name_ko = {"ISL": "아이슬란드", "IRL": "아일랜드", "ISR": "이스라엘",
               "KOR": "대한민국", "CHE": "스위스", "GBR": "영국"}
    fallback = [
        {"code": "ISL", "name": "아이슬란드", "value": 53.97, "year": "2024"},
        {"code": "IRL", "name": "아일랜드",   "value": 53.52, "year": "2024"},
        {"code": "ISR", "name": "이스라엘",   "value": 37.23, "year": "2024"},
        {"code": "KOR", "name": "대한민국",   "value": 36.26, "year": "2024"},
        {"code": "CHE", "name": "스위스",     "value": 29.62, "year": "2024"},
        {"code": "GBR", "name": "영국",       "value": 29.23, "year": "2024"},
    ]
    result = _fetch_worldbank("TX.VAL.TECH.MF.ZS", countries, name_ko, fallback)
    result.sort(key=lambda x: x["value"], reverse=True)
    return result


# ── 소재 3: 실업률 (낮을수록 좋음 — 오름차순) ─────────────────────────────
def fetch_unemployment():
    countries = ["JPN", "MEX", "KOR", "CZE", "POL", "SVN"]
    name_ko = {"JPN": "일본", "MEX": "멕시코", "KOR": "대한민국",
               "CZE": "체코", "POL": "폴란드", "SVN": "슬로베니아"}
    fallback = [
        {"code": "JPN", "name": "일본",     "value": 2.45, "year": "2025"},
        {"code": "MEX", "name": "멕시코",   "value": 2.67, "year": "2025"},
        {"code": "KOR", "name": "대한민국", "value": 2.68, "year": "2025"},
        {"code": "CZE", "name": "체코",     "value": 2.83, "year": "2025"},
        {"code": "POL", "name": "폴란드",   "value": 2.98, "year": "2025"},
        {"code": "SVN", "name": "슬로베니아", "value": 3.18, "year": "2025"},
    ]
    result = _fetch_worldbank("SL.UEM.TOTL.ZS", countries, name_ko, fallback, year=2025)
    result.sort(key=lambda x: x["value"], reverse=False)   # 낮을수록 상위
    return result


# ── 소재 4: 고등교육 총 등록률 (세계 4위, OECD 한정 아님) ─────────────────
def fetch_tertiary_enrollment():
    countries = ["MAC", "CYP", "HKG", "KOR", "FIN", "AUS"]
    name_ko = {"MAC": "마카오", "CYP": "키프로스", "HKG": "홍콩",
               "KOR": "대한민국", "FIN": "핀란드", "AUS": "호주"}
    fallback = [
        {"code": "MAC", "name": "마카오",   "value": 141.87, "year": "2024"},
        {"code": "CYP", "name": "키프로스", "value": 120.88, "year": "2024"},
        {"code": "HKG", "name": "홍콩",     "value": 120.09, "year": "2024"},
        {"code": "KOR", "name": "대한민국", "value": 111.85, "year": "2024"},
        {"code": "FIN", "name": "핀란드",   "value": 110.30, "year": "2024"},
        {"code": "AUS", "name": "호주",     "value": 108.42, "year": "2024"},
    ]
    result = _fetch_worldbank("SE.TER.ENRR", countries, name_ko, fallback, year=2024)
    result.sort(key=lambda x: x["value"], reverse=True)
    return result


# ── 소재 레지스트리 ───────────────────────────────────────────────────────
# 렌더링(draw_frame)과 업로드 메타데이터(build_metadata)가 여기서 텍스트를 가져온다.
# 팩트체크 완료(2026-07-07): "세계 N위"는 전체 국가 기준, "OECD N위"는 OECD 38개국
# 기준으로 각각 재확인한 값만 사용 — 지표별 비교군을 문구에 명확히 구분해 표기한다.
TOPICS = {
    "worldbank_rnd_gdp": {
        "fetch": fetch_rnd_gdp,
        "title1": "국가별 R&D 투자 비율",
        "title2": "(GDP 대비, World Bank · 2023년 기준)",
        "highlight": "대한민국, OECD 국가 중 2위",
        "source_footer": "출처: World Bank Open Data · GB.XPD.RSDV.GD.ZS",
        "indicator": "GB.XPD.RSDV.GD.ZS",
        "yt_title": "대한민국, OECD 국가 중 R&D 투자 2위 (2023년 기준)",
        "yt_desc_header": "국가별 GDP 대비 R&D(연구개발) 투자 비율 비교\n출처: World Bank Open Data (GB.XPD.RSDV.GD.ZS, 2023년 기준)",
        "yt_tags": ["인포그래픽", "데이터시각화", "OECD", "R&D", "대한민국", "통계", "AI"],
        "yt_hashtags": "#인포그래픽 #데이터시각화 #OECD #R&D #통계 #대한민국",
    },
    "worldbank_tech_exports": {
        "fetch": fetch_tech_exports,
        "title1": "국가별 첨단기술 수출 비중",
        "title2": "(제조업 수출 대비 %, World Bank · 2024년 기준)",
        "highlight": "대한민국, OECD 국가 중 4위",
        "source_footer": "출처: World Bank Open Data · TX.VAL.TECH.MF.ZS",
        "indicator": "TX.VAL.TECH.MF.ZS",
        "yt_title": "대한민국, OECD 국가 중 첨단기술 수출 비중 4위 (2024년 기준)",
        "yt_desc_header": "국가별 제조업 수출 대비 첨단기술 제품 비중 비교\n출처: World Bank Open Data (TX.VAL.TECH.MF.ZS, 2024년 기준)\n※ 전세계 기준으로는 12위(홍콩 등 소규모 무역허브 포함) — OECD 38개국 기준 4위",
        "yt_tags": ["인포그래픽", "데이터시각화", "OECD", "첨단기술", "수출", "대한민국", "AI"],
        "yt_hashtags": "#인포그래픽 #데이터시각화 #OECD #첨단기술 #수출 #대한민국",
    },
    "worldbank_unemployment": {
        "fetch": fetch_unemployment,
        "title1": "국가별 실업률 비교",
        "title2": "(World Bank ILO 추정치 · 2025년 기준)",
        "highlight": "대한민국, OECD 국가 중 최저 3위",
        "source_footer": "출처: World Bank Open Data · SL.UEM.TOTL.ZS",
        "indicator": "SL.UEM.TOTL.ZS",
        "yt_title": "대한민국, OECD 국가 중 실업률 최저 3위 (2025년 기준)",
        "yt_desc_header": "국가별 실업률(ILO 추정치) 비교 — 낮을수록 좋음\n출처: World Bank Open Data (SL.UEM.TOTL.ZS, 2025년 기준)\n※ 전세계 기준으로는 29위 — OECD 38개국 기준 3위",
        "yt_tags": ["인포그래픽", "데이터시각화", "OECD", "실업률", "고용", "대한민국", "경제"],
        "yt_hashtags": "#인포그래픽 #데이터시각화 #OECD #실업률 #고용 #대한민국",
    },
    "worldbank_tertiary_enrollment": {
        "fetch": fetch_tertiary_enrollment,
        "title1": "국가별 고등교육 진학률",
        "title2": "(총 등록률 %, World Bank · 2024년 기준)",
        "highlight": "대한민국, 세계 4위",
        "source_footer": "※ 재수생·만학도 포함 총 등록률 방식이라 100% 초과 가능 (World Bank)",
        "indicator": "SE.TER.ENRR",
        "yt_title": "대한민국, 고등교육 진학률 세계 4위 (2024년 기준)",
        "yt_desc_header": "국가별 고등교육 총 등록률(gross enrollment ratio) 비교\n출처: World Bank Open Data (SE.TER.ENRR, 2024년 기준)\n※ 전체 국가(94개국) 기준 4위\n※ 총 등록률은 나이·재학 여부와 무관하게 등록생 수를 해당 연령대 인구로 나눈 값이라, 재수생·만학도 등이 포함돼 100%를 넘을 수 있는 정상적인 통계 방식입니다.",
        "yt_tags": ["인포그래픽", "데이터시각화", "교육", "대학진학률", "대한민국", "통계"],
        "yt_hashtags": "#인포그래픽 #데이터시각화 #교육 #대학진학률 #대한민국",
    },
}

# 기본 토픽(로컬 단독 실행 시 사용) — 기존 프로토타입과 동일한 소재
TOPIC_ID = "worldbank_rnd_gdp"


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def draw_frame(frame_idx, data, max_val, topic):
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
    ax.text(0.5, 0.90, topic["title1"], ha="center", va="center",
             fontproperties=FONT_S(52), color=TEXT_MAIN, alpha=title_alpha)
    ax.text(0.5, 0.865, topic["title2"], ha="center", va="center",
             fontproperties=FONT_S(24), color=TEXT_SUB, alpha=title_alpha)

    if grow_t > 0.05:
        sub_alpha = min(1.0, grow_t * 2)
        ax.text(0.5, 0.815, topic["highlight"], ha="center", va="center",
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

    ax.text(0.5, 0.06, topic["source_footer"],
             ha="center", va="center", fontproperties=FONT_S(18), color=TEXT_SUB, alpha=0.7)

    return fig


def render_frames(data, topic, frames_dir=None):
    frames_dir = frames_dir or FRAMES_DIR
    os.makedirs(frames_dir, exist_ok=True)
    max_val = max(d["value"] for d in data) * 1.08

    for i in range(TOTAL_FRAMES):
        fig = draw_frame(i, data, max_val, topic)
        frame_path = os.path.join(frames_dir, f"frame_{i:04d}.png")
        fig.savefig(frame_path, dpi=DPI)
        plt.close(fig)   # 프레임마다 즉시 해제 — 리스트에 누적 금지

        if i % 60 == 0:
            print(f"[RENDER] {i}/{TOTAL_FRAMES} 프레임 완료")

    print(f"[RENDER] 전체 {TOTAL_FRAMES} 프레임 완료")


def encode_video(frames_dir=None, out_path=None):
    frames_dir = frames_dir or FRAMES_DIR
    out_path   = out_path or OUT_MP4

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[INFO] 시스템 ffmpeg 없음 — imageio-ffmpeg 번들 바이너리 사용: {ffmpeg_bin}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-framerate", str(FPS),
        "-i", os.path.join(frames_dir, "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] ffmpeg 인코딩 실패")
        print(result.stderr[-3000:])
        raise RuntimeError("ffmpeg encode failed")
    print(f"[INFO] 인코딩 완료: {out_path}")


def cleanup_frames(frames_dir=None):
    frames_dir = frames_dir or FRAMES_DIR
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir)
        print(f"[CLEANUP] 임시 프레임 디렉터리 삭제: {frames_dir}")


def build_metadata(data, topic_id):
    """YouTube 업로드용 title/description/tags 생성 (Claude API 호출 없이 결정론적으로 생성)."""
    topic = TOPICS[topic_id]
    lines = "\n".join(f"{d['name']}: {d['value']}%" for d in data)
    desc = f"{topic['yt_desc_header']}\n\n{lines}\n\n{topic['yt_hashtags']}"
    tags = topic["yt_tags"] + [f"topic:{topic_id}"]
    return topic["yt_title"], desc, tags


def main():
    topic_id = sys.argv[1] if len(sys.argv) > 1 else TOPIC_ID
    topic = TOPICS[topic_id]
    t_start = time.time()

    data = topic["fetch"]()
    for d in data:
        print(f"  {d['name']:6s} {d['value']:.2f}%  ({d['year']})")

    t_render_start = time.time()
    render_frames(data, topic)
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
