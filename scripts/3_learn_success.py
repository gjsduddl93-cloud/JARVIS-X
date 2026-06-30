"""
자체 채널 영상 성과 추적 → data/success_metrics.json 업데이트
실행: python scripts/3_learn_success.py
"""

import os
import json
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY")
YOUTUBE_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "youtube_token.json")
METRICS_FILE      = os.path.join(os.path.dirname(__file__), "..", "data", "success_metrics.json")
PATTERNS_FILE     = os.path.join(os.path.dirname(__file__), "..", "data", "viral_patterns.json")

TODAY = datetime.now().strftime("%Y%m%d")


def get_channel_videos() -> list:
    """자체 채널 업로드 영상 목록 + 통계 조회."""
    if not YOUTUBE_API_KEY:
        print("[WARN] YOUTUBE_API_KEY 없음 - 저장된 metrics 유지")
        return []

    try:
        import googleapiclient.discovery as gd
        from google.oauth2.credentials import Credentials

        creds = None
        if os.path.exists(YOUTUBE_TOKEN_FILE):
            import json as _json
            token_data = _json.loads(open(YOUTUBE_TOKEN_FILE).read())
            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.getenv("GOOGLE_CLIENT_ID"),
                client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            )

        if creds:
            yt = gd.build("youtube", "v3", credentials=creds)
        else:
            yt = gd.build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

        # 채널 ID 조회
        ch_resp = yt.channels().list(part="id,statistics", mine=True).execute()
        if not ch_resp.get("items"):
            print("[WARN] 채널 정보 없음")
            return []

        channel = ch_resp["items"][0]
        ch_id   = channel["id"]
        print(f"[INFO] 채널 ID: {ch_id}")

        # 업로드 재생목록 ID
        ch_detail = yt.channels().list(part="contentDetails", id=ch_id).execute()
        uploads_id = ch_detail["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # 재생목록에서 영상 ID 수집 (최대 50개)
        pl_resp = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads_id, maxResults=50
        ).execute()
        video_ids = [item["contentDetails"]["videoId"] for item in pl_resp.get("items", [])]

        if not video_ids:
            return []

        # 통계 조회
        stats_resp = yt.videos().list(
            part="snippet,statistics", id=",".join(video_ids)
        ).execute()

        results = []
        for item in stats_resp.get("items", []):
            snip  = item["snippet"]
            stats = item.get("statistics", {})
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            results.append({
                "video_id":    item["id"],
                "title":       snip.get("title", ""),
                "published_at": snip.get("publishedAt", ""),
                "views":       views,
                "likes":       likes,
                "comments":    int(stats.get("commentCount", 0)),
                "like_ratio":  round(likes / views, 4) if views > 0 else 0,
                "url":         f"https://youtube.com/watch?v={item['id']}",
            })

        print(f"[INFO] {len(results)}개 자체 영상 조회 완료")
        return results

    except Exception as e:
        print(f"[ERROR] 채널 영상 조회 실패: {e}")
        return []


def classify_success(videos: list, threshold: int = 10000) -> tuple[list, list]:
    """조회수 기준으로 성공/실패 분류."""
    success = [v for v in videos if v["views"] >= threshold]
    failure = [v for v in videos if v["views"] < threshold]
    return success, failure


def extract_success_patterns(success: list, failure: list) -> dict:
    """성공/실패 영상의 제목 특징 비교."""
    def _has(titles, keywords):
        return sum(1 for t in titles if any(k in t for k in keywords))

    s_titles = [v["title"] for v in success]
    f_titles = [v["title"] for v in failure]
    total_s  = len(s_titles) or 1
    total_f  = len(f_titles) or 1

    checks = [
        ("❌/금지형", ["❌", "하지마", "금지", "절대"]),
        ("숫자 포함", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]),
        ("시간형",    ["분 안에", "초 만에", "분만에"]),
        ("수익/돈",   ["수익", "돈", "월급", "부업", "투자", "벌"]),
        ("AI/기술",   ["AI", "ChatGPT", "인공지능", "자동화"]),
        ("비법/비밀", ["비법", "비밀", "대공개", "공개"]),
    ]

    patterns = {}
    for name, kws in checks:
        s_rate = _has(s_titles, kws) / total_s
        f_rate = _has(f_titles, kws) / total_f
        patterns[name] = {
            "success_rate": round(s_rate, 2),
            "failure_rate": round(f_rate, 2),
            "lift":         round(s_rate / (f_rate + 0.01), 2),
        }

    return dict(sorted(patterns.items(), key=lambda x: x[1]["lift"], reverse=True))


def update_success_metrics(videos: list) -> None:
    """success_metrics.json 업데이트."""
    try:
        existing = json.loads(open(METRICS_FILE, encoding="utf-8").read())
    except Exception:
        existing = {"videos": {}}

    stored = existing.get("videos", {})
    for v in videos:
        vid = v["video_id"]
        stored[vid] = {
            "title":        v["title"],
            "published_at": v["published_at"],
            "views":        v["views"],
            "likes":        v["likes"],
            "like_ratio":   v["like_ratio"],
            "url":          v["url"],
            "tracked_at":   TODAY,
        }

    all_views = [v["views"] for v in stored.values()]
    threshold = 10000

    updated = {
        "updated_at":        TODAY,
        "total_videos":      len(stored),
        "avg_views":         int(sum(all_views) / len(all_views)) if all_views else 0,
        "max_views":         max(all_views) if all_views else 0,
        "success_threshold": threshold,
        "success_count":     sum(1 for v in stored.values() if v["views"] >= threshold),
        "videos":            stored,
    }

    with open(METRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)

    print(f"[INFO] success_metrics.json 업데이트: {len(stored)}개 영상")


def update_viral_patterns_with_success(patterns: dict) -> None:
    """성공 패턴을 viral_patterns.json에 반영."""
    try:
        vp = json.loads(open(PATTERNS_FILE, encoding="utf-8").read())
    except Exception:
        vp = {}

    # 성공 lift가 높은 패턴을 prompt_boost에 반영
    top_patterns = [name for name, data in patterns.items() if data["lift"] >= 1.5]
    if top_patterns:
        boost = f"성공 패턴: {', '.join(top_patterns[:3])} → 우선 사용"
        existing = vp.get("prompt_boost", "")
        vp["prompt_boost"] = f"{existing} | {boost}" if existing else boost
        vp["own_success_patterns"] = patterns
        vp["updated_at"] = TODAY

        with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
            json.dump(vp, f, ensure_ascii=False, indent=2)

        print(f"[INFO] 성공 패턴 반영: {top_patterns}")


def main():
    print(f"[START] 자체 성과 학습 - {TODAY}")

    videos = get_channel_videos()

    if videos:
        update_success_metrics(videos)
        success, failure = classify_success(videos, 10000)
        print(f"[INFO] 성공: {len(success)}개 / 실패: {len(failure)}개 (기준: 10,000회)")

        if success or failure:
            patterns = extract_success_patterns(success, failure)
            print("[INFO] 성공 패턴 분석:")
            for name, data in list(patterns.items())[:5]:
                print(f"  {name}: 성공율 {data['success_rate']:.0%} / lift {data['lift']:.1f}x")
            update_viral_patterns_with_success(patterns)
    else:
        print("[INFO] 영상 데이터 없음 - 기존 metrics 유지")

    print("[DONE] 성과 학습 완료")


if __name__ == "__main__":
    main()
