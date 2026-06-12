from flask import Flask, render_template, request, session
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime
import os
import re

load_dotenv()

app = Flask(__name__)
app.secret_key = "jarvis_x_secret_key"

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
당신은 JARVIS-X이다.

목표:
- 사용자가 월 10~50만원 이상의 부수입을 만들 수 있도록 돕는다.
- 장기적으로 더 큰 자동화 수익 시스템을 구축한다.
- 유튜브 쇼츠, 인스타 릴스, 틱톡, 블로그 등 원소스 멀티유즈 콘텐츠 전략을 우선한다.
- 추후 자동 트레이딩 보조 시스템까지 확장할 수 있도록 돕는다.

답변 규칙:
1. 항상 한국어로 답변
2. 실행 가능한 내용 위주로 답변
3. 너무 길게 설명하지 말 것
4. 목록은 최대 5개
5. 수익화 관점 포함
"""

PROJECTS_DIR = "projects"


def ask_ai(prompt, max_tokens=900):
    try:
        response = client.responses.create(
            model="gpt-5-mini",
            max_output_tokens=max_tokens,
            input=f"""
{SYSTEM_PROMPT}

요청:
{prompt}
"""
        )
        return response.output_text
    except Exception as e:
        return f"오류 발생: {str(e)}"


def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = text.replace("\n", " ")
    return text[:30].strip()


def save_project(category, content):
    try:
        os.makedirs(PROJECTS_DIR, exist_ok=True)

        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_category = clean_filename(category)

        filename = f"{now}_{safe_category}.txt"
        filepath = os.path.join(PROJECTS_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as file:
            file.write(content)

        return filename

    except Exception as e:
        return f"저장 실패: {str(e)}"


def add_assistant_message(content):
    session["history"].append({
        "role": "assistant",
        "content": content
    })
    session["history"] = session["history"][-30:]


def add_user_message(content):
    session["history"].append({
        "role": "user",
        "content": content
    })
    session["history"] = session["history"][-30:]


def get_trends():
    return ask_ai(
        """
현재 글로벌 SNS(유튜브 쇼츠, 틱톡, 인스타 릴스)에서 주목받을 만한 트렌드 10개를 알려줘.

형식:
1. 트렌드명
- 설명
- 조회수 잠재력(1~10)
- 추천 플랫폼
""",
        900
    )


def create_shorts():
    return ask_ai(
        """
조회수가 잘 나올 쇼츠 아이디어 5개를 생성해줘.

각 항목 형식:
1. 제목
- 훅
- 30초 대본
- 썸네일 문구
- 추천 해시태그
""",
        1000
    )


def money_ideas():
    return ask_ai(
        """
월 10~50만원 부수입을 목표로 오늘부터 시작 가능한 자동화 수익 아이디어 5개를 알려줘.

각 항목 형식:
1. 아이디어명
- 예상 수익
- 자동화 가능성
- 시작 방법
- 주의점
""",
        1000
    )


def ai_news():
    return ask_ai(
        """
AI 콘텐츠 사업자가 알아야 할 오늘의 AI/테크 뉴스 주제 5개를 알려줘.
실제 최신 뉴스라고 단정하지 말고, 최근 주목해야 할 이슈 후보로 정리해줘.

각 항목 형식:
1. 이슈명
- 왜 중요한지
- 콘텐츠로 만들 방법
""",
        900
    )


def global_issues():
    return ask_ai(
        """
해외 시청자를 노릴 수 있는 글로벌 이슈형 콘텐츠 주제 5개를 추천해줘.

각 항목 형식:
1. 주제
- 왜 바이럴 가능성이 있는지
- 쇼츠 제목 예시
- 추천 플랫폼
""",
        900
    )


def content_package():
    return ask_ai(
        """
유튜브 쇼츠, 인스타 릴스, 틱톡에 동시에 올릴 수 있는 콘텐츠 패키지 1개를 만들어줘.

형식:
- 주제
- 유튜브 쇼츠 제목
- 인스타 릴스 캡션
- 틱톡 설명
- 30초 대본
- 썸네일 문구
- 해시태그
""",
        1000
    )


@app.route("/", methods=["GET", "POST"])
def home():
    if "history" not in session:
        session["history"] = []

    if request.method == "POST":
        question = request.form.get("question")

        if question:
            saved_filename = None

            if question == "GET_TRENDS":
                answer = get_trends()
                saved_filename = save_project("오늘의_트렌드", answer)

            elif question == "MAKE_SHORTS":
                answer = create_shorts()
                saved_filename = save_project("쇼츠_생성", answer)

            elif question == "MONEY_IDEAS":
                answer = money_ideas()
                saved_filename = save_project("수익_아이디어", answer)

            elif question == "AI_NEWS":
                answer = ai_news()
                saved_filename = save_project("AI_뉴스", answer)

            elif question == "GLOBAL_ISSUES":
                answer = global_issues()
                saved_filename = save_project("해외_이슈", answer)

            elif question == "CONTENT_PACKAGE":
                answer = content_package()
                saved_filename = save_project("콘텐츠_패키지", answer)

            else:
                add_user_message(question)
                answer = ask_ai(question, 900)
                saved_filename = save_project("일반_질문", f"질문:\n{question}\n\n답변:\n{answer}")

            if saved_filename:
                answer = f"{answer}\n\n---\n📁 저장 완료: projects/{saved_filename}"

            add_assistant_message(answer)

    return render_template(
        "index.html",
        history=session.get("history", [])
    )


@app.route("/reset")
def reset():
    session.pop("history", None)
    return """
    <h2>대화기록 초기화 완료</h2>
    <a href="/">JARVIS-X 돌아가기</a>
    """


if __name__ == "__main__":
    app.run(debug=True)