"""
YouTube OAuth 인증 공용 헬퍼.
app.py가 Render에서 하는 것과 동일한 방식: YOUTUBE_TOKEN_JSON 환경변수를
로컬 youtube_token.json 파일로 부트스트랩하고, Credentials.from_authorized_user_file()로
토큰 파일에 내장된 client_id/secret을 그대로 사용한다(별도 CLIENT_ID/SECRET 불필요).
OAuth 토큰이 없으면 developer_key(API 키)로 폴백한다.
"""
import os
import json

from google.oauth2.credentials import Credentials

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "youtube_token.json")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]


def bootstrap_token_file() -> bool:
    """YOUTUBE_TOKEN_JSON 환경변수가 있으면 로컬 토큰 파일로 기록. 사용 가능 여부 반환."""
    token_env = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not token_env:
        return os.path.exists(TOKEN_FILE)

    try:
        token_data = json.loads(token_env)
    except json.JSONDecodeError as e:
        print(f"[ERROR] YOUTUBE_TOKEN_JSON 환경변수가 유효하지 않은 JSON: {e}")
        return os.path.exists(TOKEN_FILE)

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f)
    return True


def get_youtube_client(developer_key: str | None = None):
    """OAuth 토큰이 있으면 토큰에 내장된 자격증명으로, 없으면 developer_key로 클라이언트 생성. 둘 다 없으면 None."""
    import googleapiclient.discovery as gd

    if bootstrap_token_file():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)
        return gd.build("youtube", "v3", credentials=creds)

    if developer_key:
        return gd.build("youtube", "v3", developerKey=developer_key)

    return None
