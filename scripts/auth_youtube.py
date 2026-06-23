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

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = "client_secret.json"
TOKEN_FILE = "youtube_token.json"


def main():
    # 의존성 확인
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("❌ google-auth-oauthlib 패키지가 필요합니다.")
        print("   pip install google-auth-oauthlib google-auth google-api-python-client")
        sys.exit(1)

    # client_secret.json 확인
    if not os.path.exists(CLIENT_SECRET):
        print(f"❌ '{CLIENT_SECRET}' 파일이 없습니다.")
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

    print("🔑 YouTube OAuth 인증을 시작합니다...")
    print("   잠시 후 브라우저가 열립니다. Google 계정으로 로그인 후 권한을 허용하세요.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",       # refresh token 발급 (자동 갱신 필수)
        prompt="consent"             # 항상 consent 화면 표시 (refresh token 보장)
    )

    # 토큰 저장
    token_data = json.loads(creds.to_json())
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, ensure_ascii=False)

    print(f"✅ 인증 완료! '{TOKEN_FILE}' 저장됨")
    print()
    print("━" * 60)
    print("다음 단계: Render 환경변수에 토큰 추가")
    print("━" * 60)
    print()
    print("1. Render 대시보드 → 서비스 선택 → Environment 탭")
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
    print("✨ 완료 후 YouTube 자동 업로드가 활성화됩니다!")


if __name__ == "__main__":
    main()
