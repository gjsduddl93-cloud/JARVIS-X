from flask import Flask, render_template, request, session, jsonify
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import os
import re
import json

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jarvis_x_secret_key")

# OpenAI 클라이언트 초기화
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)

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
6. 단순 인사에는 짧게 응답
"""


def clean_filename(text):
    """파일명에 사용할 수 없는 문자 제거"""
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    return text[:30].strip()


def should_save_question(question):
    """의미 있는 질문인지 판단"""
    q = question.strip().replace(" ", "")
    short_words = ["안녕", "고마워", "감사", "좋아", "그래", "응", "네", "다음", "진행"]
    if len(q) < 10:
        return False
    if q in short_words:
        return False
    return True


def save_project(category, content):
    """생성된 콘텐츠 파일로 저장"""
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{now}_{clean_filename(category)}.txt"
    filepath = os.path.join(PROJECTS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as file:
        file.write(content)
    
    return filename


def ask_gpt(user_prompt, max_tokens=1024):
    """ChatGPT API 호출"""
    try:
        response = client.chat.completions.create(
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
        return f"❌ 오류 발생: {str(e)}"


def add_message(role, content):
    """대화 히스토리에 메시지 추가"""
    if "history" not in session:
        session["history"] = []
    
    session["history"].append({
        "role": role,
        "content": content
    })
    
    # 최근 30개만 유지
    session["history"] = session["history"][-30:]
    session.modified = True


def get_trends():
    """오늘의 트렌드"""
    return ask_gpt("""
글로벌 SNS에서 주목받을 만한 쇼츠/릴스 트렌드 5개를 추천해줘.

형식:
1. 트렌드명
- 설명
- 조회수 잠재력
- 추천 플랫폼
""", 800)


def create_shorts():
    """쇼츠 생성"""
    return ask_gpt("""
조회수가 잘 나올 쇼츠 아이디어 3개를 만들어줘.

각 항목:
- 제목
- 훅 (처음 3초)
- 30초 대본
- 해시태그
""", 900)


def content_package():
    """콘텐츠 패키지"""
    return ask_gpt("""
유튜브 쇼츠, 인스타 릴스, 틱톡에 동시에 올릴 콘텐츠 패키지 1개를 만들어줘.

형식:
- 주제
- 제목
- 30초 대본
- 릴스 캡션
- 틱톡 설명
- 해시태그
""", 900)


def money_ideas():
    """수익 아이디어"""
    return ask_gpt("""
월 10~50만원 부수입 목표로 자동화 가능한 수익 아이디어 5개를 추천해줘.

각 항목:
- 아이디어명
- 예상 월수익
- 자동화 방법
- 시작 비용
- 난이도
""", 900)


def ai_news():
    """AI 뉴스"""
    return ask_gpt("""
AI 콘텐츠 사업자가 참고할 만한 AI/테크 이슈 후보 5개를 알려줘.

각 항목:
- 이슈명
- 왜 중요한가?
- 콘텐츠로 만드는 방법
""", 800)


def global_issues():
    """해외 이슈"""
    return ask_gpt("""
해외 시청자를 노릴 수 있는 글로벌 이슈형 콘텐츠 주제 5개를 추천해줘.

각 항목:
- 주제
- 바이럴 가능성 (높음/중간/낮음)
- 쇼츠 제목 예시
""", 800)


def image_prompt():
    """이미지 프롬프트"""
    return ask_gpt("""
유튜브 쇼츠/인스타 릴스용 이미지 제작 프롬프트를 만들어줘.

형식:
1. 썸네일 이미지 프롬프트
2. 영상 배경 이미지 프롬프트
3. 인스타 릴스 커버 이미지 프롬프트
4. 권장 스타일
5. 주의할 점

프롬프트는 영어로 작성하고, 설명은 한국어로 짧게 작성해줘.
""", 900)


def video_package():
    """영상 제작 패키지"""
    return ask_gpt("""
CapCut에서 바로 영상으로 만들 수 있는 30초 쇼츠 영상 제작 패키지 1개를 만들어줘.

형식:
1. 콘텐츠 주제
2. 유튜브 쇼츠 제목
3. 첫 3초 훅 문장
4. 30초 나레이션 대본
5. 장면 구성
   - 장면 1: 화면 설명 / 자막
   - 장면 2: 화면 설명 / 자막
   - 장면 3: 화면 설명 / 자막
   - 장면 4: 화면 설명 / 자막
6. CapCut 편집 지시
   - 화면 비율
   - 자막 스타일
   - 전환 효과
   - BGM 분위기
7. 썸네일 문구
8. 유튜브 설명
9. 인스타 릴스 캡션
10. 해시태그
""", 1200)


@app.route("/", methods=["GET", "POST"])
def home():
    """메인 페이지"""
    if "history" not in session:
        session["history"] = []
    
    if request.method == "POST":
        question = request.form.get("question", "").strip()
        
        if question:
            saved_filename = None
            answer = None
            
            # 빠른 버튼 처리
            if question == "GET_TRENDS":
                answer = get_trends()
                saved_filename = save_project("오늘의_트렌드", answer)
            
            elif question == "MAKE_SHORTS":
                answer = create_shorts()
                saved_filename = save_project("쇼츠_생성", answer)
            
            elif question == "CONTENT_PACKAGE":
                answer = content_package()
                saved_filename = save_project("콘텐츠_패키지", answer)
            
            elif question == "MONEY_IDEAS":
                answer = money_ideas()
                saved_filename = save_project("수익_아이디어", answer)
            
            elif question == "AI_NEWS":
                answer = ai_news()
                saved_filename = save_project("AI_뉴스", answer)
            
            elif question == "GLOBAL_ISSUES":
                answer = global_issues()
                saved_filename = save_project("해외_이슈", answer)
            
            elif question == "IMAGE_PROMPT":
                answer = image_prompt()
                saved_filename = save_project("이미지_프롬프트", answer)
            
            elif question == "VIDEO_PACKAGE":
                answer = video_package()
                saved_filename = save_project("영상_제작_패키지", answer)
            
            # 일반 질문
            else:
                add_message("user", question)
                answer = ask_gpt(question, 800)
                
                if should_save_question(question):
                    saved_filename = save_project(
                        "일반_질문",
                        f"질문:\n{question}\n\n답변:\n{answer}"
                    )
            
            # 답변에 저장 정보 추가
            if saved_filename and answer:
                answer = f"{answer}\n\n---\n📁 저장 완료: projects/{saved_filename}"
            
            # 히스토리에 추가
            if answer:
                add_message("assistant", answer)
    
    return render_template(
        "index.html",
        history=session.get("history", [])
    )


@app.route("/reset")
def reset():
    """대화 히스토리 초기화"""
    session.pop("history", None)
    return """
    <h2>대화기록 초기화 완료</h2>
    <a href="/">JARVIS-X 돌아가기</a>
    """


@app.route("/auto-create", methods=["GET"])
def auto_create():
    """자동 콘텐츠 생성 (외부 트리거용)"""
    try:
        # 보안: API 키 확인
        auth_key = request.args.get("key")
        expected_key = os.getenv("AUTO_KEY", "secret123")
        
        if auth_key != expected_key:
            return {"status": "error", "message": "Unauthorized"}, 401
        
        results = []
        
        # 트렌드 생성
        try:
            trend_content = get_trends()
            save_project("자동_트렌드", trend_content)
            results.append("✅ 트렌드 생성 완료")
        except Exception as e:
            results.append(f"❌ 트렌드 생성 실패: {str(e)}")
        
        # 쇼츠 생성
        try:
            shorts_content = create_shorts()
            save_project("자동_쇼츠", shorts_content)
            results.append("✅ 쇼츠 생성 완료")
        except Exception as e:
            results.append(f"❌ 쇼츠 생성 실패: {str(e)}")
        
        # 이미지 프롬프트 생성
        try:
            image_content = image_prompt()
            save_project("자동_이미지프롬프트", image_content)
            results.append("✅ 이미지 프롬프트 생성 완료")
        except Exception as e:
            results.append(f"❌ 이미지 프롬프트 생성 실패: {str(e)}")
        
        return {
            "status": "success",
            "message": "\n".join(results),
            "timestamp": datetime.now().isoformat(),
            "count": len([r for r in results if r.startswith("✅")])
        }, 200
    
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }, 500


@app.route("/health", methods=["GET"])
def health():
    """헬스 체크 (Render용)"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}, 200


if __name__ == "__main__":
    # Render 환경에서는 debug=False
    debug_mode = os.getenv("FLASK_ENV") != "production"
    app.run(debug=debug_mode, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))