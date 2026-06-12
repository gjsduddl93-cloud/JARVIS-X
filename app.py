from flask import Flask, render_template, request, session
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime
import os
import re

load_dotenv()

app = Flask(__name__)
app.secret_key = "jarvis_x_secret_key"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PROJECTS_DIR = "projects"

SYSTEM_PROMPT = """
당신은 JARVIS-X이다.

사용자의 목표:
- AI 콘텐츠 자동화로 월 10~50만원 부수입 만들기
- 유튜브 쇼츠, 인스타 릴스, 틱톡, 블로그 동시 활용
- 장기적으로 더 큰 자동화 수익 시스템 구축

답변 규칙:
1. 항상 한국어로 답변
2. 기본 답변은 3~5줄 이내
3. 사용자가 자세히 요청할 때만 길게 설명
4. 목록은 최대 5개
5. 실행 가능한 내용만 말하기
6. 단순 인사에는 짧게 응답
"""


def clean_filename(text):
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    return text[:30].strip()


def should_save_question(question):
    short_words = [
        "안녕",
        "고마워",
        "감사",
        "좋아",
        "그래",
        "ㅇㅋ",
        "오케이",
        "다음",
        "다음단계",
        "진행",
        "응",
        "네",
        "아니",
    ]

    q = question.strip().replace(" ", "")

    if len(q) < 10:
        return False

    if q in short_words:
        return False

    return True


def save_project(category, content):
    os.makedirs(PROJECTS_DIR, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{now}_{clean_filename(category)}.txt"
    filepath = os.path.join(PROJECTS_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as file:
        file.write(content)

    return filename


def ask_ai(user_prompt, max_tokens=700):
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    history = session.get("history", [])[-10:]

    for item in history:
        messages.append({
            "role": item["role"],
            "content": item["content"]
        })

    messages.append({
        "role": "user",
        "content": user_prompt
    })

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            reasoning={"effort": "low"},
            max_output_tokens=max_tokens,
            input=messages
        )

        return response.output_text

    except Exception as e:
        return f"오류 발생: {str(e)}"


def add_message(role, content):
    session["history"].append({
        "role": role,
        "content": content
    })
    session["history"] = session["history"][-30:]


def get_trends():
    return ask_ai(
        """
글로벌 SNS에서 주목받을 만한 쇼츠/릴스 트렌드 5개를 추천해줘.

형식:
1. 트렌드명
- 설명
- 조회수 잠재력
- 추천 플랫폼
""",
        700
    )


def create_shorts():
    return ask_ai(
        """
조회수가 잘 나올 쇼츠 아이디어 3개를 만들어줘.

각 항목:
- 제목
- 훅
- 30초 대본
- 해시태그
""",
        800
    )


def content_package():
    return ask_ai(
        """
유튜브 쇼츠, 인스타 릴스, 틱톡에 동시에 올릴 콘텐츠 패키지 1개를 만들어줘.

형식:
- 주제
- 제목
- 30초 대본
- 릴스 캡션
- 틱톡 설명
- 해시태그
""",
        800
    )


def money_ideas():
    return ask_ai(
        """
월 10~50만원 부수입 목표로 자동화 가능한 수익 아이디어 5개를 추천해줘.

각 항목:
- 아이디어
- 예상 수익
- 자동화 방법
- 시작 방법
""",
        800
    )


def ai_news():
    return ask_ai(
        """
AI 콘텐츠 사업자가 참고할 만한 AI/테크 이슈 후보 5개를 알려줘.

각 항목:
- 이슈명
- 콘텐츠로 만드는 방법
""",
        700
    )


def global_issues():
    return ask_ai(
        """
해외 시청자를 노릴 수 있는 글로벌 이슈형 콘텐츠 주제 5개를 추천해줘.

각 항목:
- 주제
- 바이럴 가능성
- 쇼츠 제목 예시
""",
        700
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

            else:
                add_message("user", question)
                answer = ask_ai(question, 700)

                if should_save_question(question):
                    saved_filename = save_project(
                        "일반_질문",
                        f"질문:\n{question}\n\n답변:\n{answer}"
                    )

            if saved_filename:
                answer = f"{answer}\n\n---\n📁 저장 완료: projects/{saved_filename}"

            add_message("assistant", answer)

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