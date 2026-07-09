import urllib.request, json, time, sys, os

base    = os.environ["RENDER_URL"]
job_id  = os.environ["JOB_ID"]
start   = time.time()
MAX_SEC = 3300   # 55분

print(f"🔄 job 폴링 시작: {job_id}")

while True:
    elapsed = time.time() - start
    if elapsed > MAX_SEC:
        print(f"⏱ 타임아웃 ({MAX_SEC}초 초과)")
        sys.exit(1)
    try:
        resp = urllib.request.urlopen(f"{base}/status/{job_id}", timeout=15)
        d    = json.loads(resp.read())
        st   = d.get("status", "?")
        logs = d.get("logs", [])
        last = logs[-1] if logs else ""
        print(f"  [{int(elapsed)}s] {st} — {last}")

        if st == "done":
            yt  = (d.get("youtube") or {}).get("url", "")
            ttl = (d.get("content") or {}).get("title", "")
            print(f"✅ 완료! 제목: {ttl}")
            if yt:
                print(f"🔗 {yt}")
            sys.exit(0)
        elif st == "quota_exceeded":
            print(f"⏸ YouTube 업로드 한도 초과 (콘텐츠는 생성됨): {d.get('error', '')}")
            sys.exit(0)
        elif st == "error":
            print(f"❌ 실패: {d.get('error', '알 수 없음')}")
            sys.exit(1)
    except Exception as e:
        print(f"  폴링 오류: {e}")

    time.sleep(30)
