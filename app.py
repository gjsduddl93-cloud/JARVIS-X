from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from openai import OpenAI
from google.cloud import texttospeech
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from moviepy.editor import TextClip, ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import os
import re
import json
import requests
from pathlib import Path
import tempfile
import subprocess

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jarvis_x_secret_key")

# API 초기화
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
tts_client = texttospeech.TextToSpeechClient()

PROJECTS_DIR = "projects"
VIDEOS_DIR = os.path.join(PROJECTS_DIR, "videos")
AUDIO_DIR = os.path.join(PROJECTS_DIR, "audio")
IMAGES_DIR = os.path.join(PROJECTS_DIR, "images")

for dir_path in [PROJECTS_DIR, VIDEOS_DIR, AUDIO_DIR, IMAGES_DIR]:
    os.makedirs(dir_path, exist_ok=True)

SYSTEM_PROMPT = """
당신은 JARVIS-X이다.

사용자의 목표:
- AI 콘텐츠 자동화로 월 10~50만원 부수입 만들기
- 유튜브 쇼츠, 인스타 릴스, 틱톡, 블로그 동시 활용
- 장기적으로 더 큰 자동화 수익 시스템 구축

콘텐츠 방향:
- 40% AI 활용법
- 30% 돈 버는 방법/부업/AI 수익화
- 30% 해외 신기한 사실/미스터리/흥미로운 정보

답변 규칙:
1. 항상 한국어로 답변
2. 기본 답변은 3~5줄 이내
3. 사용자가 자세히 요청할 때만 길게 설명
4. 목록은 최대 5개
5. 실행 가능한 내용만 말하기
"""

# YouTube OAuth 설정
YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
CREDENTIALS_FILE = 'youtube_credentials.json'
TOKEN_FILE = 'youtube_token.json'


def get_youtube_service():
    """YouTube API 서비스 객체 생성"""
    creds = None
    
    # 저장된 토큰 있으면 사용
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)
    
    # 유효한 토큰 없으면 새로 생성
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_FILE,
                scopes=YOUTUBE_SCOPES,
                redirect_uri='https://jarvis-x-61rf.onrender.com/auth/youtube/callback'
            )
            flow.client_id = os.getenv("YOUTUBE_CLIENT_ID")
            flow.client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
            
            auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
            session['state'] = state
            return None, auth_url
        
        # 토큰 저장
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    
    return build('youtube', 'v3', credentials=creds), None


def generate_image_dalle(prompt):
    """DALL-E로 이미지 생성"""
    try:
        response = openai_client.images.generate(
            prompt=prompt,
            model="dall-e-3",
            size="1024x1024",
            quality="standard",
            n=1
        )
        
        image_url = response.data[0].url
        
        # 이미지 다운로드
        img_data = requests.get(image_url).content
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = os.path.join(IMAGES_DIR, f"image_{timestamp}.png")
        
        with open(image_path, 'wb') as f:
            f.write(img_data)
        
        return image_path
    except Exception as e:
        return None


def generate_audio_tts(text, output_path):
    """Google Cloud TTS로 음성 생성"""
    try:
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        voice = texttospeech.VoiceSelectionParams(
            language_code="ko-KR",
            name="ko-KR-Neural2-A",
            ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
        )
        
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0
        )
        
        response = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        with open(output_path, 'wb') as out:
            out.write(response.audio_content)
        
        return output_path
    except Exception as e:
        return None


def create_video_from_content(content_data):
    """콘텐츠에서 영상 자동 생성"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 1. 이미지 생성 (DALL-E)
        image_prompt = content_data.get('image_prompt', 'Professional video thumbnail')
        image_path = generate_image_dalle(image_prompt)
        
        if not image_path:
            return None
        
        # 2. 음성 생성 (Google TTS)
        narration = content_data.get('narration', '')
        audio_path = os.path.join(AUDIO_DIR, f"audio_{timestamp}.mp3")
        audio_path = generate_audio_tts(narration, audio_path)
        
        if not audio_path:
            return None
        
        # 3. 자막 준비
        subtitle_text = content_data.get('title', '')
        
        # 4. MoviePy로 영상 편집
        try:
            # 이미지 클립 (10초)
            img_clip = ImageClip(image_path).set_duration(10)
            
            # 오디오 클립
            audio_clip = AudioFileClip(audio_path)
            video_duration = audio_clip.duration + 2  # 여유 2초
            
            img_clip = img_clip.set_duration(video_duration)
            
            # 자막 추가
            if subtitle_text:
                txt_clip = TextClip(
                    subtitle_text,
                    fontsize=40,
                    color='white',
                    font='Arial-Bold',
                    method='caption',
                    size=(img_clip.w - 40, None)
                ).set_duration(video_duration).set_position('bottom')
                
                video_clip = CompositeVideoClip([img_clip, txt_clip])
            else:
                video_clip = img_clip
            
            # 오디오 추가
            video_clip = video_clip.set_audio(audio_clip)
            
            # 영상 저장 (MP4, 1080x1920 세로 포맷 - 쇼츠용)
            video_path = os.path.join(VIDEOS_DIR, f"video_{timestamp}.mp4")
            video_clip.write_videofile(
                video_path,
                fps=24,
                codec='libx264',
                audio_codec='aac',
                verbose=False,
                logger=None
            )
            
            # 메모리 정리
            video_clip.close()
            audio_clip.close()
            
            return video_path
        
        except Exception as e:
            return None
    
    except Exception as e:
        return None


def upload_to_youtube(video_path, title, description, tags):
    """YouTube에 영상 자동 업로드"""
    try:
        youtube_service, auth_url = get_youtube_service()
        
        if auth_url:
            return {"status": "auth_required", "auth_url": auth_url}
        
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22'  # People & Blogs
            },
            'status': {
                'privacyStatus': 'public',
                'madeForKids': False
            }
        }
        
        media = MediaFileUpload(
            video_path,
            mimetype='video/mp4',
            resumable=True
        )
        
        request = youtube_service.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )
        
        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
            except HttpError as e:
                return {"status": "error", "message": str(e)}
        
        return {
            "status": "success",
            "video_id": response['id'],
            "url": f"https://www.youtube.com/watch?v={response['id']}"
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}


def clean_filename(text):
    """파일명 정제"""
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    return text[:30].strip()


def save_project(category, content):
    """콘텐츠 저장"""
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{now}_{clean_filename(category)}.txt"
    filepath = os.path.join(PROJECTS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as file:
        file.write(content)
    
    return filename


def ask_gpt(user_prompt, max_tokens=1024):
    """ChatGPT 호출"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=max_tokens,
            temperature=0.7
        )
        
        return response.choices[0].message.content
    
    except Exception as e:
        return f"❌ 오류: {str(e)}"


def video_package():
    """완전 자동화 영상 패키지"""
    content_prompt = """
유튜브 쇼츠/릴스용 30초 영상을 자동으로 만들 수 있는 패키지를 생성해줘.

형식 (JSON으로 정확하게):
{
  "title": "영상 제목 (30자 이내)",
  "description": "YouTube 설명 (100자 이내)",
  "tags": ["태그1", "태그2", "태그3"],
  "narration": "30초 분량의 나레이션 (약 150자)",
  "image_prompt": "DALL-E 이미지 프롬프트 (영어)",
  "thumbnail_text": "썸네일 텍스트"
}
"""
    
    response = ask_gpt(content_prompt, 800)
    
    try:
        # JSON 추출
        json_start = response.find('{')
        json_end = response.rfind('}') + 1
        json_str = response[json_start:json_end]
        content_data = json.loads(json_str)
        return content_data
    except:
        return None


@app.route("/", methods=["GET", "POST"])
def home():
    """메인 페이지"""
    if "history" not in session:
        session["history"] = []
    
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        
        if question:
            answer = None
            
            # 완전 자동화 영상 생성
            if question == "VIDEO_PACKAGE":
                answer = "🎬 영상 자동 생성 중...\n"
                answer += "1️⃣ 콘텐츠 생성\n"
                answer += "2️⃣ DALL-E 이미지 생성\n"
                answer += "3️⃣ Google TTS 음성 생성\n"
                answer += "4️⃣ FFmpeg 영상 편집\n"
                answer += "5️⃣ YouTube 준비 완료\n"
                
                content_data = video_package()
                
                if content_data:
                    video_path = create_video_from_content(content_data)
                    
                    if video_path:
                        answer += f"\n✅ 영상 생성 완료!\n📁 {video_path}\n\n"
                        answer += f"📊 정보:\n"
                        answer += f"제목: {content_data.get('title')}\n"
                        answer += f"설명: {content_data.get('description')}\n"
                        answer += f"태그: {', '.join(content_data.get('tags', []))}\n"
                        
                        # YouTube 업로드
                        upload_result = upload_to_youtube(
                            video_path,
                            content_data.get('title'),
                            content_data.get('description'),
                            content_data.get('tags', [])
                        )
                        
                        if upload_result.get('status') == 'success':
                            answer += f"\n🎉 YouTube 업로드 완료!\n"
                            answer += f"🔗 {upload_result.get('url')}\n"
                        elif upload_result.get('status') == 'auth_required':
                            answer += f"\n⚠️ YouTube 인증 필요\n"
                            answer += f"링크: {upload_result.get('auth_url')}\n"
                        else:
                            answer += f"\n❌ YouTube 업로드 실패\n{upload_result.get('message')}\n"
                    else:
                        answer += "\n❌ 영상 생성 실패"
                else:
                    answer += "\n❌ 콘텐츠 생성 실패"
            else:
                answer = ask_gpt(question, 800)
            
            if answer:
                session["history"].append({
                    "role": "user",
                    "content": question
                })
                session["history"].append({
                    "role": "assistant",
                    "content": answer
                })
                session.modified = True
    
    return render_template(
        "index.html",
        history=session.get("history", [])
    )


@app.route("/auth/youtube/callback")
def youtube_callback():
    """YouTube OAuth 콜백"""
    try:
        state = request.args.get('state')
        code = request.args.get('code')
        
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=YOUTUBE_SCOPES,
            state=state,
            redirect_uri='https://jarvis-x-61rf.onrender.com/auth/youtube/callback'
        )
        flow.client_id = os.getenv("YOUTUBE_CLIENT_ID")
        flow.client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
        
        flow.fetch_token(authorization_response=request.url)
        
        creds = flow.credentials
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        
        return "✅ YouTube 인증 완료! 이제 자동 업로드가 가능합니다."
    
    except Exception as e:
        return f"❌ 오류: {str(e)}"


@app.route("/auto-create", methods=["GET"])
def auto_create():
    """자동 콘텐츠 + 영상 생성"""
    try:
        auth_key = request.args.get("key")
        expected_key = os.getenv("AUTO_KEY", "secret123")
        
        if auth_key != expected_key:
            return {"status": "error", "message": "Unauthorized"}, 401
        
        results = []
        
        try:
            content_data = video_package()
            if content_data:
                video_path = create_video_from_content(content_data)
                if video_path:
                    results.append("✅ 영상 생성 완료")
                    
                    upload_result = upload_to_youtube(
                        video_path,
                        content_data.get('title'),
                        content_data.get('description'),
                        content_data.get('tags', [])
                    )
                    
                    if upload_result.get('status') == 'success':
                        results.append(f"✅ YouTube 업로드 완료: {upload_result.get('url')}")
                    else:
                        results.append("⚠️ YouTube 업로드 보류 (인증 필요)")
                else:
                    results.append("❌ 영상 생성 실패")
            else:
                results.append("❌ 콘텐츠 생성 실패")
        except Exception as e:
            results.append(f"❌ 오류: {str(e)}")
        
        return {
            "status": "success",
            "message": "\n".join(results),
            "timestamp": datetime.now().isoformat(),
            "count": len([r for r in results if r.startswith("✅")])
        }, 200
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


@app.route("/health", methods=["GET"])
def health():
    """헬스 체크"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}, 200


@app.route("/reset")
def reset():
    """대화 초기화"""
    session.pop("history", None)
    return """
    <h2>대화기록 초기화 완료</h2>
    <a href="/">JARVIS-X 돌아가기</a>
    """


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_ENV") != "production"
    app.run(debug=debug_mode, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))