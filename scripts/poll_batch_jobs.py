import urllib.request, json, time, sys, os

base    = os.environ["RENDER_URL"]
job_ids = os.environ["JOB_IDS"].split()
done    = set()
errors  = set()
quota   = set()
total   = len(job_ids)
start   = time.time()
MAX_SEC = 840   # 14분

print(f"🔄 {total}개 job 폴링 시작: {job_ids}")

while len(done) + len(errors) + len(quota) < total:
    elapsed = time.time() - start
    if elapsed > MAX_SEC:
        print(f"⏱ 타임아웃 ({MAX_SEC}초 초과)")
        sys.exit(1)

    for jid in job_ids:
        if jid in done or jid in errors:
            continue
        try:
            resp = urllib.request.urlopen(f"{base}/status/{jid}", timeout=15)
            d    = json.loads(resp.read())
            st   = d.get("status", "?")
            logs = d.get("logs", [])
            last = logs[-1] if logs else ""
            print(f"  [{jid[:8]}] {st} — {last}")

            if st == "done":
                yt  = (d.get("youtube") or {}).get("url", "")
                ttl = (d.get("content") or {}).get("title", "")
                print(f"  ✅ {jid[:8]} 완료! 제목: {ttl}")
                if yt:
                    print(f"  🔗 {yt}")
                done.add(jid)
            elif st == "quota_exceeded":
                err = d.get("error", "일일 업로드 한도 초과")
                print(f"  ⏸ {jid[:8]} 쿼터 초과 (정상 한도, 실패 아님): {err}")
                quota.add(jid)
            elif st == "error":
                err = d.get("error", "알 수 없음")
                print(f"  ❌ {jid[:8]} 실패: {err}")
                errors.add(jid)
        except Exception as e:
            print(f"  [{jid[:8]}] 폴링 오류: {e}")

    remaining = total - len(done) - len(errors) - len(quota)
    if remaining > 0:
        print(f"  ⏳ {remaining}개 진행 중... ({int(elapsed)}s 경과)")
        time.sleep(20)

print(f"\n{'='*40}")
print(f"🏁 배치 완료: 성공 {len(done)}개 / 실패 {len(errors)}개 / 쿼터초과 {len(quota)}개 / 합계 {total}개")
if errors:
    sys.exit(1)
