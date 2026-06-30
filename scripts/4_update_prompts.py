"""
viral_patterns.json 기반으로 오늘의 최적 프롬프트 상태 확인 및 학습 로그 기록
실행: python scripts/4_update_prompts.py
"""

import os
import json
from datetime import datetime

PATTERNS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "viral_patterns.json")
METRICS_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "success_metrics.json")
LOG_DIR       = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE      = os.path.join(LOG_DIR, "learning_log.txt")

TODAY     = datetime.now().strftime("%Y%m%d")
WEEKDAY   = datetime.now().weekday()  # 0=월, 6=일
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"][WEEKDAY]

os.makedirs(LOG_DIR, exist_ok=True)


def load_patterns() -> dict:
    try:
        return json.loads(open(PATTERNS_FILE, encoding="utf-8").read())
    except Exception:
        return {}


def load_metrics() -> dict:
    try:
        return json.loads(open(METRICS_FILE, encoding="utf-8").read())
    except Exception:
        return {}


def get_todays_best_pattern(patterns: dict) -> dict:
    """오늘 요일에 맞는 최적 제목 패턴 선택."""
    title_patterns = patterns.get("title_patterns", [])
    if not title_patterns:
        return {
            "pattern": "AI가 알려주는 [주제] | [숫자]분 가이드",
            "reason":  "기본 패턴 (학습 데이터 없음)",
        }

    # 요일별 최적 패턴 필터
    for p in title_patterns:
        if WEEKDAY in p.get("weekday_best", list(range(7))):
            return p

    return title_patterns[0]


def build_dynamic_prompt(patterns: dict) -> str:
    """viral_patterns.json 기반 동적 프롬프트 조각 생성."""
    best_pattern  = get_todays_best_pattern(patterns)
    hot_keywords  = patterns.get("trending_keywords", [])[:5]
    prompt_boost  = patterns.get("prompt_boost", "")
    hot_cats      = patterns.get("hot_categories", [])
    own_patterns  = patterns.get("own_success_patterns", {})

    # 키워드 가중치 상위 3개
    top_kws = [k["keyword"] for k in hot_keywords[:3]]

    # 자체 성공 패턴 중 lift >= 1.5
    own_boost = [
        name for name, data in own_patterns.items()
        if data.get("lift", 0) >= 1.5
    ]

    lines = [
        f"오늘({WEEKDAY_KO}) 최적 제목 패턴: {best_pattern.get('pattern', '')}",
    ]
    if top_kws:
        lines.append(f"우선 키워드: {', '.join(top_kws)}")
    if hot_cats:
        lines.append(f"트렌딩 카테고리: {', '.join(hot_cats[:2])}")
    if own_boost:
        lines.append(f"자체 성공 형식: {', '.join(own_boost[:2])}")
    if prompt_boost:
        lines.append(prompt_boost)

    return " | ".join(lines)


def save_learning_log(patterns: dict, metrics: dict, dynamic_prompt: str) -> None:
    """학습 기록 로그 파일에 추가."""
    total_v  = metrics.get("total_videos", 0)
    avg_v    = metrics.get("avg_views", 0)
    max_v    = metrics.get("max_views", 0)
    success  = metrics.get("success_count", 0)
    version  = patterns.get("version", 1)
    top_kws  = [k["keyword"] for k in patterns.get("trending_keywords", [])[:5]]

    entry = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 {TODAY} ({WEEKDAY_KO}) - viral_patterns v{version}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 자체 채널:
   총 영상: {total_v}개 | 평균 조회수: {avg_v:,} | 최고: {max_v:,}
   성공(10k+): {success}개 ({round(success/total_v*100) if total_v else 0}%)

🔥 트렌딩 키워드: {', '.join(top_kws)}

🤖 오늘의 동적 프롬프트:
   {dynamic_prompt}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

    print(entry.strip())


def update_patterns_prompt_field(patterns: dict, dynamic_prompt: str) -> None:
    """viral_patterns.json의 today_prompt 필드 업데이트."""
    patterns["today_prompt"]  = dynamic_prompt
    patterns["today_weekday"] = WEEKDAY_KO
    patterns["updated_at"]    = TODAY

    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(patterns, f, ensure_ascii=False, indent=2)

    print(f"[INFO] viral_patterns.json today_prompt 업데이트 완료")


def main():
    print(f"[START] 프롬프트 업데이트 - {TODAY} ({WEEKDAY_KO})")

    patterns = load_patterns()
    metrics  = load_metrics()

    if not patterns:
        print("[WARN] viral_patterns.json 없음 - 스크립트 2번 먼저 실행 필요")

    dynamic_prompt = build_dynamic_prompt(patterns)
    print(f"[INFO] 동적 프롬프트: {dynamic_prompt[:80]}...")

    update_patterns_prompt_field(patterns, dynamic_prompt)
    save_learning_log(patterns, metrics, dynamic_prompt)

    print(f"[DONE] 프롬프트 업데이트 완료 → {LOG_FILE}")


if __name__ == "__main__":
    main()
