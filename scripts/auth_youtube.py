"""
YouTube OAuth 최초 인증 스크립트
────────────────────────────────
사전 준비:
  1. Google Cloud Console (console.cloud.google.com) 에서:
     - 프로젝트 생성
     - YouTube Data API v3 활성화
     - OAuth 2.0 클라이언트 ID 생성 (유형: 데스크톱 앱)
     - JSON 파일 다운로드 → 이 파일과 같은 폴더에 client_secret.json 으로 저장

실행:
  cd C:\\Users\\admin\\Desktop\\JARVIS-X
  python scripts/auth_youtube.py

완료 후:
  - youtube_token.json 이 생성됩니다
  - 파일 내용 전체를 복사해서
    Render 대시보드 → Environment → YOUTUBE_TOKEN_JSON 에 붙여넣으세요
"""

import os
import json
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET = "client_secret.json"
TOKEN_FILE = "youtube_token.json"


def main():
    # 의존성 확인
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("[ERROR] google-auth-oauthlib 패키지가 필요합니다.")
        print("   pip install google-auth-oauthlib google-auth google-api-python-client")
        sys.exit(1)

    # client_secret.json 확인
    if not os.path.exists(CLIENT_SECRET):
        print(f"[ERROR] '{CLIENT_SECRET}' 파일이 없습니다.")
        print()
        print("Google Cloud Console에서 OAuth 2.0 자격증명을 만드는 방법:")
        print("  1. https://console.cloud.google.com 접속")
        print("  2. 프로젝트 선택 또는 생성")
        print("  3. API 및 서비스 → 라이브러리 → 'YouTube Data API v3' 활성화")
        print("  4. API 및 서비스 → 사용자 인증 정보 → OAuth 2.0 클라이언트 ID 만들기")
        print("     - 유형: 데스크톱 앱")
        print("     - 이름: JARVIS-X")
        print("  5. JSON 다운로드 → client_secret.json 으로 저장")
        sys.exit(1)

    print("[*] YouTube OAuth 인증을 시작합니다...")
    print()
    print("    !! 중요: 브라우저에서 반드시 'future.minute' 채널 계정으로 로그인하세요 !!")
    print("    !! 다른 계정이 자동 선택되면 '다른 계정 사용'을 클릭하세요              !!")
    print()
    print("    잠시 후 브라우저가 열립니다.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="select_account consent",  # 항상 계정 선택 화면 먼저 표시
    )

    # 토큰 저장
    token_data = json.loads(creds.to_json())
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, ensure_ascii=False)

    print(f"[OK] 인증 완료! '{TOKEN_FILE}' 저장됨")
    print()

    # 어떤 채널에 연결됐는지 즉시 확인
    print("=== 연결된 YouTube 채널 확인 ===")
    try:
        from google.auth.transport.requests import Request as GRequest
        from googleapiclient.discovery import build as yt_build

        creds.refresh(GRequest())   # 최신 토큰 확보
        svc = yt_build("youtube", "v3", credentials=creds)
        ch = svc.channels().list(part="snippet,statistics", mine=True).execute()
        for item in ch.get("items", []):
            s  = item["snippet"]
            st = item.get("statistics", {})
            print(f"  채널명   : {s['title']}")
            print(f"  채널ID   : {item['id']}")
            print(f"  URL      : https://www.youtube.com/channel/{item['id']}")
            print(f"  구독자   : {st.get('subscriberCount', 'N/A')}")
            print(f"  영상 수  : {st.get('videoCount', 'N/A')}")
            print(f"  커스텀URL: {s.get('customUrl', '없음')}")
        print()
        print("[!] 위 채널이 업로드 대상입니다. 올바른 채널인지 확인하세요!")
        print("[!] 잘못된 채널이면 Ctrl+C 로 중단하고 올바른 계정으로 다시 실행하세요.")
    except Exception as e:
        print(f"  채널 확인 실패: {e}")
    print()

    print("-" * 60)
    print("다음 단계: Render 환경변수에 토큰 추가")
    print("-" * 60)
    print()
    print("1. Render 대시보드 -> 서비스 선택 -> Environment 탭")
    print("2. 아래 내용을 환경변수로 추가:")
    print()
    print("   변수명: YOUTUBE_TOKEN_JSON")
    print("   값 (아래 전체 복사):")
    print()
    with open(TOKEN_FILE, encoding="utf-8") as f:
        print(f.read())
    print()
    print("3. Save 후 서비스 재배포")
    print()
    print("[DONE] 완료 후 YouTube 자동 업로드가 활성화됩니다!")


if __name__ == "__main__":
    main()
