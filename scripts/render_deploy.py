"""
Render 수동 배포 트리거 스크립트
====================================
두 가지 방법 지원:
  A) Deploy Hook URL (가장 간단 — 인증 불필요)
  B) Render API Key + Service ID

=== 방법 A: Deploy Hook 설정 ===
1. Render 대시보드 → JARVIS-X 서비스 → Settings
2. 아래로 스크롤 → "Deploy Hooks" 섹션 → "Add Deploy Hook" 클릭
3. 이름: "manual-trigger", 브랜치: "main" → Create
4. 생성된 URL 복사 (예: https://api.render.com/deploy/srv-xxxxx?key=yyyyy)
5. 아래 DEPLOY_HOOK_URL에 붙여넣기 OR 환경변수로 설정:
   set RENDER_DEPLOY_HOOK=https://api.render.com/deploy/srv-xxxxx?key=yyyyy

=== 방법 B: API Key + Service ID 설정 ===
1. Render 대시보드 → Account Settings → API Keys → Create API Key
2. JARVIS-X 서비스 URL에서 service ID 확인
   (예: https://dashboard.render.com/web/srv-xxxxxxxxxx → srv-xxxxxxxxxx)
3. 아래 RENDER_API_KEY, RENDER_SERVICE_ID에 입력 OR 환경변수로 설정:
   set RENDER_API_KEY=rnd_xxxxxxxxxx
   set RENDER_SERVICE_ID=srv-xxxxxxxxxx

실행: python scripts/render_deploy.py
"""

import os
import sys
import json
import urllib.request
import urllib.error

# ── 설정 ──────────────────────────────────────────────────────────────────────
# 방법 A: Deploy Hook URL (환경변수 또는 직접 입력)
DEPLOY_HOOK_URL = os.getenv("RENDER_DEPLOY_HOOK", "")

# 방법 B: API Key + Service ID
RENDER_API_KEY    = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")


def deploy_via_hook(url):
    """Deploy Hook URL로 배포 트리거."""
    print(f"[HOOK] POST {url[:60]}...")
    req = urllib.request.Request(url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            print(f"[HOOK] HTTP {resp.status}")
            try:
                data = json.loads(body)
                print(f"[HOOK] 응답: {json.dumps(data, indent=2, ensure_ascii=False)}")
                deploy_id = data.get("id") or data.get("deploy", {}).get("id", "")
                if deploy_id:
                    print(f"\n[OK] 배포 시작됨! Deploy ID: {deploy_id}")
                    print(f"     확인: https://dashboard.render.com")
                else:
                    print(f"\n[OK] 배포 트리거 완료 (응답에 ID 없음)")
            except json.JSONDecodeError:
                print(f"[HOOK] 응답: {body[:200]}")
        return True
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def deploy_via_api(api_key, service_id):
    """Render REST API로 배포 트리거."""
    url = f"https://api.render.com/v1/services/{service_id}/deploys"
    print(f"[API] POST {url}")
    payload = json.dumps({"clearCache": "do_not_clear"}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            print(f"[API] HTTP {resp.status}")
            data = json.loads(body)
            deploy_id = data.get("id", "")
            status    = data.get("status", "")
            commit    = data.get("commit", {}).get("id", "")[:8]
            print(f"\n[OK] 배포 시작됨!")
            print(f"     Deploy ID : {deploy_id}")
            print(f"     Status    : {status}")
            print(f"     Commit    : {commit}")
            print(f"     확인      : https://dashboard.render.com")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[ERROR] HTTP {e.code}: {body[:300]}")
        if e.code == 401:
            print("[HINT] API Key가 잘못됐거나 만료됐습니다. Render Account Settings에서 새로 발급하세요.")
        elif e.code == 404:
            print("[HINT] Service ID가 잘못됐습니다. Render 대시보드 URL에서 srv-xxxxxxxx 부분을 확인하세요.")
        return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


def check_deploy_status(api_key, service_id, deploy_id=None):
    """최근 배포 상태 확인 (API Key 필요)."""
    if not api_key or not service_id:
        return
    url = f"https://api.render.com/v1/services/{service_id}/deploys?limit=3"
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            deploys = json.loads(resp.read().decode())
            print("\n[STATUS] 최근 배포 목록:")
            for d in deploys[:3]:
                dep = d.get("deploy", d)
                print(f"  - {dep.get('id','?')[:12]} | {dep.get('status','?'):12} | "
                      f"{dep.get('createdAt','?')[:19]} | "
                      f"commit: {dep.get('commit',{}).get('id','?')[:8]}")
    except Exception as e:
        print(f"[STATUS] 확인 실패: {e}")


def main():
    print("=" * 55)
    print("  Render 수동 배포 트리거")
    print("=" * 55)

    # 방법 A: Deploy Hook
    if DEPLOY_HOOK_URL:
        print("\n[방법 A] Deploy Hook 사용")
        success = deploy_via_hook(DEPLOY_HOOK_URL)
        if success:
            return

    # 방법 B: Render API
    if RENDER_API_KEY and RENDER_SERVICE_ID:
        print("\n[방법 B] Render API 사용")
        deploy_via_api(RENDER_API_KEY, RENDER_SERVICE_ID)
        check_deploy_status(RENDER_API_KEY, RENDER_SERVICE_ID)
        return

    # 설정 안 됨
    print("\n[ERROR] 환경변수가 설정되지 않았습니다.")
    print()
    print("방법 A (추천 — 더 간단):")
    print("  Render → 서비스 → Settings → Deploy Hooks → Add")
    print("  생성된 URL을 환경변수로 설정:")
    print()
    print("  Windows:")
    print("    set RENDER_DEPLOY_HOOK=https://api.render.com/deploy/srv-xxx?key=yyy")
    print("    python scripts/render_deploy.py")
    print()
    print("방법 B (Render API):")
    print("  1) Render → Account Settings → API Keys → Create")
    print("  2) 서비스 URL에서 srv-xxxxxxxxxx 확인")
    print()
    print("  Windows:")
    print("    set RENDER_API_KEY=rnd_xxxxxxxxxx")
    print("    set RENDER_SERVICE_ID=srv-xxxxxxxxxx")
    print("    python scripts/render_deploy.py")
    sys.exit(1)


if __name__ == "__main__":
    main()
