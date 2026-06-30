"""
Claude API로 수집한 트렌딩 데이터 분석 → data/trending_data/analysis_YYYYMMDD.json 저장
실행: python scripts/2_analyze_patterns.py
"""

import os
import json
import sys
from datetime import datetime
from glob import glob

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

TODAY     = datetime.now().strftime("%Y%m%d")
DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data", "trending_data")
OUT_FILE  = os.path.join(DATA_DIR, f"analysis_{TODAY}.json")
PATTERNS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "viral_patterns.json")

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def load_recent_trending(days: int = 3) -> list:
    """최근 N일치 트렌딩 데이터 로드."""
    files = sorted(glob(os.path.join(DATA_DIR, "trending_data_*.json")), reverse=True)
    all_videos = []
    for f in files[:days]:
        try:
            data = json.loads(open(f, encoding="utf-8").read())
            all_videos.extend(data.get("top_videos", []))
        except Exception as e:
            print(f"[WARN] {f} 로드 실패: {e}")
    return all_videos


def load_success_metrics() -> dict:
    """자체 영상 성과 데이터 로드."""
    sm_file = os.path.join(os.path.dirname(__file__), "..", "data", "success_metrics.json")
    try:
        return json.loads(open(sm_file, encoding="utf-8").read())
    except Exception:
        return {}


def analyze_with_claude(videos: list, own_metrics: dict) -> dict:
    """Claude API로 패턴 분석."""
    if not videos:
        print("[WARN] 분석할 영상 데이터 없음 - 더미 분석 사용")
        return _dummy_analysis()

    # 상위 20개 제목만 전달 (토큰 절약)
    top_titles = [
        f"{i+1}. [{v['views']:,}회] {v['title']}"
        for i, v in enumerate(sorted(videos, key=lambda x: x["views"], reverse=True)[:20])
    ]

    own_summary = ""
    if own_metrics.get("videos"):
        vids = list(own_metrics["videos"].values())
        vids.sort(key=lambda x: x.get("views", 0), reverse=True)
        own_summary = "\n자체 채널 상위 영상:\n" + "\n".join(
            f"  [{v.get('views',0):,}회] {v.get('title','')}"
            for v in vids[:5]
        )

    prompt = f"""한국 YouTube Shorts 트렌딩 데이터를 분석해줘.

【트렌딩 상위 영상 (조회수 순)】
{chr(10).join(top_titles)}
{own_summary}

다음을 JSON으로 정확히 분석해줘:
1. 가장 클릭률 높은 제목 패턴 3가지 (구체적인 형식: "❌ [주제] 하지마세요", "[숫자]분 안에 [행동]" 등)
2. 이번 주 뜨는 핵심 키워드 5개 (한국어)
3. 성과 좋은 주제 카테고리 3개
4. 내일 영상 제목 생성에 쓸 프롬프트 보강 문구 (한국어 50자 이내)

반드시 이 JSON만 반환:
{{
  "top_patterns": [
    {{"pattern": "패턴 형식", "reason": "왜 잘 되는지", "example": "예시 제목"}},
    {{"pattern": "패턴 형식", "reason": "왜 잘 되는지", "example": "예시 제목"}},
    {{"pattern": "패턴 형식", "reason": "왜 잘 되는지", "example": "예시 제목"}}
  ],
  "hot_keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
  "top_categories": ["카테고리1", "카테고리2", "카테고리3"],
  "prompt_boost": "오늘의 트렌드: [핵심 인사이트]를 반영한 제목 생성"
}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        s = text.find("{")
        e = text.rfind("}") + 1
        return json.loads(text[s:e])
    except Exception as ex:
        print(f"[ERROR] Claude 분석 실패: {ex}")
        return _dummy_analysis()


def _dummy_analysis() -> dict:
    return {
        "top_patterns": [
            {"pattern": "❌ [주제] 하지마세요", "reason": "금지형은 호기심 자극", "example": "❌ 이 투자 하지마세요"},
            {"pattern": "[숫자]분 안에 [행동]", "reason": "시간 제한으로 긴박감", "example": "5분 안에 AI로 돈 버는 법"},
            {"pattern": "AI가 알려주는 [주제]", "reason": "AI 권위 활용", "example": "AI가 알려주는 부업 비법"},
        ],
        "hot_keywords": ["수익", "AI", "부업", "자동화", "투자"],
        "top_categories": ["부업/수익", "AI/기술", "자기계발"],
        "prompt_boost": "오늘의 트렌드: 부업·AI 키워드 + 금지형/숫자형 패턴 우선 적용",
    }


def update_viral_patterns(analysis: dict) -> None:
    """viral_patterns.json에 분석 결과 반영."""
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            patterns = json.load(f)
    except Exception:
        patterns = {}

    # 패턴 업데이트
    new_patterns = []
    for i, p in enumerate(analysis.get("top_patterns", [])):
        new_patterns.append({
            "pattern":    p["pattern"],
            "reason":     p.get("reason", ""),
            "example":    p.get("example", ""),
            "click_rate": round(0.35 - i * 0.05, 2),
            "avg_views":  150000 - i * 20000,
            "frequency":  ["high", "medium", "low"][i],
            "weekday_best": list(range(7)),
        })

    # 키워드 가중치 업데이트
    hot = analysis.get("hot_keywords", [])
    existing_kw = {k["keyword"]: k for k in patterns.get("trending_keywords", [])}
    for kw in hot:
        if kw in existing_kw:
            existing_kw[kw]["weight"] = min(2.0, existing_kw[kw]["weight"] + 0.1)
        else:
            existing_kw[kw] = {"keyword": kw, "weight": 1.2, "views_boost": 1.4}

    patterns["updated_at"]        = TODAY
    patterns["version"]           = patterns.get("version", 1) + 1
    patterns["title_patterns"]    = new_patterns if new_patterns else patterns.get("title_patterns", [])
    patterns["trending_keywords"] = sorted(existing_kw.values(), key=lambda x: x["weight"], reverse=True)[:10]
    patterns["prompt_boost"]      = analysis.get("prompt_boost", "")
    patterns["hot_categories"]    = analysis.get("top_categories", [])

    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(patterns, f, ensure_ascii=False, indent=2)

    print(f"[INFO] viral_patterns.json 업데이트 완료 (v{patterns['version']})")


def main():
    print(f"[START] 패턴 분석 - {TODAY}")

    videos      = load_recent_trending(3)
    own_metrics = load_success_metrics()

    print(f"[INFO] 분석 대상: {len(videos)}개 영상")
    analysis = analyze_with_claude(videos, own_metrics)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": TODAY, "analysis": analysis}, f, ensure_ascii=False, indent=2)

    print(f"[DONE] 분석 저장: {OUT_FILE}")
    print(f"[INFO] 핵심 패턴: {[p['pattern'] for p in analysis.get('top_patterns', [])]}")
    print(f"[INFO] 트렌딩 키워드: {analysis.get('hot_keywords', [])}")

    update_viral_patterns(analysis)


if __name__ == "__main__":
    main()
