from flask import Flask, render_template, request, session, jsonify, redirect
from anthropic import Anthropic
from openai import OpenAI
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    GOOGLE_AVAILABLE = True
except ImportError:
    Credentials = None
    Flow = None
    build = None
    HttpError = None
    MediaFileUpload = None
    GOOGLE_AVAILABLE = False
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import os
import re
import json
import requests
from pathlib import Path
import subprocess

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jarvis_x_secret_key")

# API 초기화
claude_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# tts_client = texttospeech.TextToSpeechClient()

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
6. JSON 형식 요청시 정확한 JSON만 반환
"""

YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
TOKEN_FILE = 'youtube_token.json'


def ask_claude(user_prompt, max_tokens=1024):
    """Claude API 호출 (주요)"""
    try:
        message = claude_client.messages.create(
            model="claude-opus-4-8",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        return message.content[0].text
    except Exception as e:
        return None


def ask_chatgpt(user_prompt, max_tokens=1024):
    """ChatGPT API 호출 (백업)"""
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
        return None


def ask_ai(user_prompt, max_tokens=1024, prefer_claude=True):
    """Claude 우선, 실패시 ChatGPT 자동 전환"""
    if prefer_claude:
        result = ask_claude(user_prompt, max_tokens)
        if result:
            return result
        # Claude 실패시 ChatGPT로 자동 전환
        return ask_chatgpt(user_prompt, max_tokens)
    else:
        result = ask_chatgpt(user_prompt, max_tokens)
        if result:
            return result
        return ask_claude(user_prompt, max_tokens)


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
        img_data = requests.get(image_url).content
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = os.path.join(IMAGES_DIR, f"image_{timestamp}.png")
        
        with open(image_path, 'wb') as f:
            f.write(img_data)
        
        return image_path
    except Exception as e:
        return None


# def generate_audio_tts(text, output_path):
#     """Google Cloud TTS로 음성 생성"""
#     try:
#         synthesis_input = texttospeech.SynthesisInput(text=text)
#
#         voice = texttospeech.VoiceSelectionParams(
#             language_code="ko-KR",
#             name="ko-KR-Neural2-A",
#             ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
#         )
#
#         audio_config = texttospeech.AudioConfig(
#             audio_encoding=texttospeech.AudioEncoding.MP3,
#             speaking_rate=1.0
#         )
#
#         response = tts_client.synthesize_speech(
#             input=synthesis_input,
#             voice=voice,
#             audio_config=audio_config
#         )
#
#         with open(output_path, 'wb') as out:
#             out.write(response.audio_content)
#
#         return output_path
#     except Exception as e:
#         return None


def create_simple_video(content_data):
    """간단한 영상 생성 (텍스트 카드형)"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 이미지 생성
        image_prompt = content_data.get('image_prompt', 'Professional video thumbnail')
        image_path = generate_image_dalle(image_prompt)
        
        if not image_path:
            return None
        
        # 음성 생성
        narration = content_data.get('narration', '')
        if narration:
            audio_path = os.path.join(AUDIO_DIR, f"audio_{timestamp}.mp3")
            # audio_path = generate_audio_tts(narration, audio_path)
            audio_path = None
        else:
            audio_path = None
        
        # FFmpeg로 간단한 영상 생성 (이미지 + 음성)
        video_path = os.path.join(VIDEOS_DIR, f"video_{timestamp}.mp4")
        
        if audio_path:
            # 오디오 길이 확인
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1', audio_path],
                    capture_output=True,
                    text=True
                )
                duration = float(result.stdout.strip()) if result.stdout else 10
            except:
                duration = 10
            
            # FFmpeg로 영상 생성
            cmd = [
                'ffmpeg', '-y',
                '-loop', '1',
                '-i', image_path,
                '-i', audio_path,
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-shortest',
                '-pix_fmt', 'yuv420p',
                '-vf', f'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
                video_path
            ]
        else:
            # 오디오 없이 이미지만
            cmd = [
                'ffmpeg', '-y',
                '-loop', '1',
                '-i', image_path,
                '-c:v', 'libx264',
                '-t', '10',
                '-pix_fmt', 'yuv420p',
                '-vf', f'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
                video_path
            ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(video_path):
            return video_path
        else:
            return None
    
    except Exception as e:
        return None


def get_youtube_service():
    """YouTube 서비스 객체 생성"""
    try:
        creds = None
        
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, YOUTUBE_SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return None, "auth_required"
        
        return build('youtube', 'v3', credentials=creds), None
    
    except Exception as e:
        return None, str(e)


def upload_to_youtube(video_path, title, description, tags):
    """YouTube에 자동 업로드"""
    try:
        youtube_service, error = get_youtube_service()
        
        if not youtube_service:
            return {"status": "auth_required", "message": error}
        
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22'
            },
            'status': {
                'privacyStatus': 'public',
                'madeForKids': False
            }
        }
        
        media = MediaFileUpload(video_path, mimetype='video/mp4', resumable=True)
        
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


def video_package_json():
    """완전 자동화 영상 패키지 (JSON 형식)"""
    prompt = """
유튜브 쇼츠/릴스용 30초 영상 데이터를 JSON 형식으로 정확하게 생성해줘.

반드시 이 JSON 형식으로만 응답:
{
  "title": "영상 제목 (30자 이내)",
  "description": "YouTube 설명 (100자 이내)",
  "tags": ["태그1", "태그2", "태그3"],
  "narration": "30초 분량의 나레이션 (약 150자)",
  "image_prompt": "DALL-E 이미지 프롬프트 (영어로 자세하게)"
}

JSON 외에 다른 텍스트는 절대 포함하지 말것!
"""
    
    response = ask_ai(prompt, 800)
    
    if response:
        try:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > 0:
                json_str = response[json_start:json_end]
                return json.loads(json_str)
        except:
            pass
    
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
                answer += "1️⃣ Claude가 콘텐츠 생성\n"
                answer += "2️⃣ DALL-E가 이미지 생성\n"
                answer += "3️⃣ Google TTS가 음성 생성\n"
                answer += "4️⃣ FFmpeg가 영상 편집\n"
                answer += "5️⃣ YouTube 업로드 준비\n\n"
                
                try:
                    content_data = video_package_json()
                    
                    if content_data:
                        video_path = create_simple_video(content_data)
                        
                        if video_path:
                            answer += f"✅ 영상 생성 완료!\n📁 {video_path}\n\n"
                            answer += f"📊 정보:\n"
                            answer += f"제목: {content_data.get('title')}\n"
                            answer += f"설명: {content_data.get('description')}\n"
                            answer += f"태그: {', '.join(content_data.get('tags', []))}\n"
                            
                            # YouTube 업로드 시도
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
                            else:
                                answer += f"\n💾 영상 생성됨 (YouTube 업로드 준비)\n"
                        else:
                            answer += "\n❌ 영상 생성 실패"
                    else:
                        answer += "\n❌ 콘텐츠 생성 실패"
                except Exception as e:
                    answer += f"\n❌ 오류: {str(e)}"
            
            # 8개 빠른 버튼
            elif question == "GET_TRENDS":
                prompt = "글로벌 SNS에서 주목받을 쇼츠/릴스 트렌드 5개를 추천해줘."
                answer = ask_ai(prompt, 800)
            
            elif question == "MAKE_SHORTS":
                prompt = "조회수가 잘 나올 쇼츠 아이디어 3개를 만들어줘. 형식: 제목\\n- 설명\\n- 조회수 잠재력"
                answer = ask_ai(prompt, 900)
            
            elif question == "CONTENT_PACKAGE":
                prompt = "유튜브 쇼츠, 인스타 릴스, 틱톡 동시 업로드 콘텐츠 1개를 만들어줘."
                answer = ask_ai(prompt, 900)
            
            elif question == "MONEY_IDEAS":
                prompt = "월 10~50만원 부수입 목표로 자동화 가능한 아이디어 5개를 추천해줘."
                answer = ask_ai(prompt, 900)
            
            elif question == "AI_NEWS":
                prompt = "AI 콘텐츠 사업자가 참고할 만한 AI/테크 이슈 후보 5개를 알려줘."
                answer = ask_ai(prompt, 800)
            
            elif question == "GLOBAL_ISSUES":
                prompt = "해외 시청자를 노릴 글로벌 이슈형 콘텐츠 주제 5개를 추천해줘."
                answer = ask_ai(prompt, 800)
            
            elif question == "IMAGE_PROMPT":
                prompt = "유튜브 쇼츠용 이미지 생성 프롬프트를 영어로 5개 만들어줘."
                answer = ask_ai(prompt, 900)
            
            # 일반 질문
            else:
                answer = ask_ai(question, 800)
            
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
            content_data = video_package_json()
            if content_data:
                video_path = create_simple_video(content_data)
                if video_path:
                    results.append("✅ 영상 생성 완료")
                    
                    upload_result = upload_to_youtube(
                        video_path,
                        content_data.get('title'),
                        content_data.get('description'),
                        content_data.get('tags', [])
                    )
                    
                    if upload_result.get('status') == 'success':
                        results.append(f"✅ YouTube 업로드: {upload_result.get('url')}")
                    else:
                        results.append("✅ 영상 준비 완료 (YouTube 업로드 대기)")
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