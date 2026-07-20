import urllib.request, urllib.error, json, time, sys, os

base    = os.environ["RENDER_URL"]
job_ids = os.environ["JOB_IDS"].split()
done    = set()
errors  = set()
quota   = set()
unknown = set()   # 서버 재시작으로 상태 유실(404) — 아래서 YouTube 업로드 이력으로 재확인
total   = len(job_ids)
start   = time.time()
MAX_SEC = 840   # 14분

print(f"🔄 {total}개 job 폴링 시작: {job_ids}")


def resolved():
    return len(done) + len(errors) + len(quota) + len(unknown)


while resolved() < total:
    elapsed = time.time() - start
    if elapsed > MAX_SEC:
        print(f"⏱ 타임아웃 ({MAX_SEC}초 초과)")
        for jid in job_ids:
            if jid not in done and jid not in errors and jid not in quota:
                unknown.add(jid)
        break

    for jid in job_ids:
        if jid in done or jid in errors or jid in quota or jid in unknown:
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
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  ⚠️ {jid[:8]} 서버 재시작으로 상태 유실 (404) — 실제 업로드 여부는 이후 확인")
                unknown.add(jid)
            else:
                print(f"  [{jid[:8]}] 폴링 오류: HTTP {e.code}")
        except Exception as e:
            print(f"  [{jid[:8]}] 폴링 오류: {e}")

    remaining = total - resolved()
    if remaining > 0:
        print(f"  ⏳ {remaining}개 진행 중... ({int(elapsed)}s 경과)")
        time.sleep(20)

print(f"\n{'='*40}")
print(f"🏁 배치 완료: 성공 {len(done)}개 / 실패 {len(errors)}개 / 쿼터초과 {len(quota)}개 / 상태유실 {len(unknown)}개 / 합계 {total}개")

# ── 상태 유실 job이 있으면 YouTube 업로드 이력으로 실제 성공 여부 교차검증 ──────────
# 개수만 비교하는 근사치 검증(어떤 job_id가 어떤 영상인지 1:1 매칭은 안 됨)이지만,
# 판단이 안 서는 상황에서 무조건 실패 처리하는 것보다는 실제 근거를 남기는 편이 낫다.
if unknown:
    lookback_min = max(int((time.time() - start) / 60) + 5, 10)
    print(f"\n🔍 상태 유실 {len(unknown)}건 재확인: 최근 {lookback_min}분 내 실제 업로드 조회 중...")
    unknown_confirmed = False
    try:
        resp = urllib.request.urlopen(f"{base}/recent-uploads?minutes={lookback_min}", timeout=20)
        d = json.loads(resp.read())
        uploads = d.get("uploads", [])
        print(f"  최근 {lookback_min}분 내 실제 업로드: {len(uploads)}건")
        for u in uploads:
            print(f"   - {u.get('title', '')} ({u.get('video_id', '')}, {u.get('published_at', '')})")
        if len(uploads) >= len(unknown):
            print(f"  ✅ 유실된 {len(unknown)}건 모두 실제로는 업로드된 것으로 추정 (개수 일치)")
            unknown_confirmed = True
        else:
            print(f"  ⚠️ 업로드 확인된 건수({len(uploads)})가 유실 건수({len(unknown)})보다 적음 — 일부 실제 실패 가능성")
    except Exception as e:
        print(f"  ❌ 재확인 조회 실패: {e} — 유실 상태를 실패로 간주")

    if not unknown_confirmed:
        errors |= unknown

if errors:
    sys.exit(1)
