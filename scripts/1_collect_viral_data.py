"""
YouTube API로 트렌딩 Shorts 50개 수집 → data/trending_data/trending_data_YYYYMMDD.json 저장
실행: python scripts/1_collect_viral_data.py
"""

import os
import json
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "trending_data")
os.makedirs(DATA_DIR, exist_ok=True)

TODAY = datetime.now().strftime("%Y%m%d")
OUT_FILE = os.path.join(DATA_DIR, f"trending_data_{TODAY}.json")


def fetch_trending_shorts(max_results: int = 50) -> list:
    """YouTube Data API v3로 한국 트렌딩 Shorts 수집."""
    if not YOUTUBE_API_KEY:
        print("[WARN] YOUTUBE_API_KEY 없음 - 더미 데이터 사용")
        return _dummy_data()

    try:
        import googleapiclient.discovery as gd
        yt = gd.build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

        # 트렌딩 영상 검색 (Shorts = 60초 이하)
        req = yt.search().list(
            part="snippet",
            type="video",
            videoDuration="short",
            regionCode="KR",
            relevanceLanguage="ko",
            order="viewCount",
            maxResults=max_results,
            q="#shorts",
        )
        resp = req.execute()

        video_ids = [item["id"]["videoId"] for item in resp.get("items", [])]
        if not video_ids:
            return []

        # 상세 통계 조회
        stats_req = yt.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        )
        stats_resp = stats_req.execute()

        results = []
        for item in stats_resp.get("items", []):
            snip  = item.get("snippet", {})
            stats = item.get("statistics", {})
            results.append({
                "video_id":    item["id"],
                "title":       snip.get("title", ""),
                "description": snip.get("description", "")[:200],
                "tags":        snip.get("tags", [])[:10],
                "published_at": snip.get("publishedAt", ""),
                "channel":     snip.get("channelTitle", ""),
                "views":       int(stats.get("viewCount", 0)),
                "likes":       int(stats.get("likeCount", 0)),
                "comments":    int(stats.get("commentCount", 0)),
                "url":         f"https://youtube.com/shorts/{item['id']}",
            })

        # 조회수 내림차순
        results.sort(key=lambda x: x["views"], reverse=True)
        print(f"[INFO] {len(results)}개 트렌딩 Shorts 수집 완료")
        return results

    except Exception as e:
        print(f"[ERROR] YouTube API 실패: {e}")
        return _dummy_data()


def _dummy_data() -> list:
    """API 키 없을 때 테스트용 더미 데이터."""
    return [
        {
            "video_id": "dummy_001",
            "title": "❌ 이 투자 실수하지 마세요 | 수익 3배 비법",
            "description": "투자 실수 예방 가이드",
            "tags": ["투자", "수익", "부업"],
            "published_at": f"{TODAY[:4]}-{TODAY[4:6]}-{TODAY[6:]}T09:00:00Z",
            "channel": "테스트채널",
            "views": 500000,
            "likes": 15000,
            "comments": 800,
            "url": "https://youtube.com/shorts/dummy_001",
        },
        {
            "video_id": "dummy_002",
            "title": "5분 안에 AI로 월 100만원 버는 법",
            "description": "AI 수익화 방법",
            "tags": ["AI", "수익", "자동화"],
            "published_at": f"{TODAY[:4]}-{TODAY[4:6]}-{TODAY[6:]}T12:00:00Z",
            "channel": "테스트채널",
            "views": 350000,
            "likes": 9000,
            "comments": 450,
            "url": "https://youtube.com/shorts/dummy_002",
        },
    ]


def extract_title_patterns(videos: list) -> list:
    """제목에서 공통 패턴 추출."""
    patterns = {}

    pattern_rules = [
        ("❌_금지형",   lambda t: "❌" in t or "하지마" in t or "금지" in t),
        ("숫자_시간형", lambda t: any(w in t for w in ["분 안에", "초 만에", "분만에"])),
        ("비법_공개형", lambda t: any(w in t for w in ["비법", "비밀", "대공개", "공개"])),
        ("충격_의외형", lambda t: any(w in t for w in ["충격", "의외로", "사실", "진실"])),
        ("숫자_나열형", lambda t: any(c.isdigit() for c in t) and "가지" in t),
        ("AI_기술형",   lambda t: any(w in t for w in ["AI", "ChatGPT", "인공지능"])),
        ("수익_돈형",   lambda t: any(w in t for w in ["수익", "돈", "월급", "부업", "투자"])),
    ]

    for pname, rule in pattern_rules:
        matched = [v for v in videos if rule(v["title"])]
        if matched:
            avg_views = sum(v["views"] for v in matched) // len(matched)
            patterns[pname] = {
                "count":     len(matched),
                "avg_views": avg_views,
                "examples":  [v["title"] for v in matched[:3]],
            }

    return sorted(patterns.items(), key=lambda x: x[1]["avg_views"], reverse=True)


def main():
    print(f"[START] 트렌딩 Shorts 수집 - {TODAY}")

    videos = fetch_trending_shorts(50)
    if not videos:
        print("[ERROR] 수집 실패")
        sys.exit(1)

    patterns = extract_title_patterns(videos)

    output = {
        "date":      TODAY,
        "collected": len(videos),
        "top_videos": videos[:10],
        "all_videos": videos,
        "title_patterns": dict(patterns),
        "top_keywords": _extract_keywords(videos),
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] 저장 완료: {OUT_FILE}")
    print(f"[INFO] 상위 패턴:")
    for name, data in patterns[:3]:
        print(f"  {name}: 평균 {data['avg_views']:,}회 ({data['count']}개)")


def _extract_keywords(videos: list) -> dict:
    """제목에서 고빈도 키워드 추출."""
    from collections import Counter
    keywords = []
    for v in videos:
        words = v["title"].replace("❌", "").replace("|", "").split()
        keywords.extend([w for w in words if len(w) >= 2])
    return dict(Counter(keywords).most_common(20))


if __name__ == "__main__":
    main()
