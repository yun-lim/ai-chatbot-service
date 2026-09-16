"""화면 라우터 — 접근 제어와 렌더링을 검증한다.

#68 이전엔 /chat·/logs 가 누구에게나 열려 있었다(인증 dependency 가 없어서).
지금은 쿠키가 없으면 /login 으로 보내고, 있으면 그 사용자의 화면을 그린다.
인증 판단은 deps.py 한 곳이 하고, 여기서는 그 결과가 화면에 어떻게 보이는지만 본다.
"""

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import crud, deps
from app.config import STATIC_DIR, TEMPLATES_DIR
from app.main import app
from app.models import ChatLog, User
from app.security import create_access_token

TEST_SECRET = "test-secret-key-with-more-than-thirty-two-bytes"


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """화면 테스트는 DB 에 닿지 않는다. 세션 팩토리와 crud 조회를 가짜로 바꾼다."""
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(deps, "SessionLocal", Mock(return_value=Mock(spec=Session)))
    monkeypatch.setattr(crud, "list_chat_logs", Mock(return_value=[]))


def _chat_log(**overrides) -> ChatLog:
    values = {
        "id": 1,
        "user_id": 7,
        "request_id": "req-1",
        "question": "IndexError 는 왜 나요?",
        "answer": "리스트 범위를 벗어난 인덱스를 읽어서요.",
        "status": "success",
        "error_code": None,
        "provider": "openai",
        "model": "test-model",
        "latency_ms": 321,
        "created_at": datetime(2026, 9, 13, 10, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return ChatLog(**values)


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def user(monkeypatch):
    """crud.get_user_by_id 가 이 사용자를 돌려준다 — 유효한 쿠키면 로그인 상태다."""
    alice = User(id=7, username="alice", password_hash="already-hashed")
    monkeypatch.setattr(crud, "get_user_by_id", Mock(return_value=alice))
    return alice


@pytest.fixture
def logged_in(client, user):
    client.cookies.set(deps.ACCESS_TOKEN_COOKIE_NAME, create_access_token(user.id))
    return client


# ── 접근 제어 ──────────────────────────────────────────────────


def test_루트는_채팅으로_보낸다(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["location"] == "/chat"


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_비로그인_화면은_로그인으로_보낸다(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_쿠키가_깨져도_로그인으로_보낸다(client, path):
    client.cookies.set(deps.ACCESS_TOKEN_COOKIE_NAME, "not-a-jwt")

    response = client.get(path)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_비로그인_API는_JSON_401을_유지한다(client):
    """화면은 redirect, API 는 JSON. chat.js 가 401 을 보고 /login 으로 옮긴다."""
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == "NOT_AUTHENTICATED"


# ── 로그인 상태의 화면 ──────────────────────────────────────────


def test_채팅_화면이_뜬다(logged_in):
    response = logged_in.get("/chat")

    assert response.status_code == 200
    # 입력창과 스크립트가 함께 와야 화면이 동작한다.
    assert '<textarea id="message"' in response.text
    assert 'src="/static/chat.js"' in response.text


def test_채팅_화면은_지난_대화를_오래된_순으로_미리_그린다(logged_in, user):
    """새로고침해도 대화가 이어져야 한다. crud 는 최신순으로 주고, 화면은 오래된 것부터 아래로 쌓는다.  → #145"""
    crud.list_chat_logs.return_value = [
        _chat_log(id=2, question="두 번째 질문", answer="두 번째 답"),
        _chat_log(id=1, question="첫 질문", answer="첫 답"),
    ]

    html = logged_in.get("/chat").text

    assert crud.list_chat_logs.call_args.args[1] == user.id
    assert html.index("첫 질문") < html.index("두 번째 질문")
    assert html.count('class="ask bubble is-user"') == 2
    assert 'id="empty"' not in html


def test_지난_대화가_없으면_안내_문구만_보인다(logged_in):
    html = logged_in.get("/chat").text

    assert 'id="empty"' in html and "막힌 곳을 물어보세요" in html
    assert 'class="ask bubble is-user"' not in html


def test_두_화면은_같은_항목_템플릿을_쓴다():
    """chat.html 과 logs.html 이 각자 마크업을 들고 있으면 한쪽만 고쳐 두 화면이 어긋난다."""
    chat = (TEMPLATES_DIR / "chat.html").read_text(encoding="utf-8")
    logs = (TEMPLATES_DIR / "logs.html").read_text(encoding="utf-8")

    assert (TEMPLATES_DIR / "_entry.html").exists()
    assert '{% include "_entry.html" %}' in chat
    assert '{% include "_entry.html" %}' in logs


def test_미리_그린_답변도_같은_마크다운_렌더러로_그린다(client):
    """서버가 그려 둔 답변은 원문이다. chat.js 가 로드 직후 같은 렌더러로 바꾼다."""
    js = client.get("/static/chat.js").text

    assert '".answer.is-bot:not(.is-error)"' in js


def test_화면에_누가_로그인했는지_보인다(logged_in):
    response = logged_in.get("/chat")

    assert "alice" in response.text


def test_로그_화면이_뜬다(logged_in):
    response = logged_in.get("/logs")

    assert response.status_code == 200


def test_기록이_없으면_없다고_말한다(logged_in):
    """#36 이후 "없음"은 진짜 없음이다. 대기 안내 문구는 사라졌다."""
    response = logged_in.get("/logs")

    assert "아직 기록이 없습니다" in response.text
    assert "아직 연결되지 않았습니다" not in response.text


def test_로그_화면은_본인_기록을_crud_한_곳에서_읽는다(logged_in, user):
    """쿼리는 crud.list_chat_logs 가 한다. 화면은 user.id 를 넘기고 결과를 그릴 뿐이다."""
    crud.list_chat_logs.return_value = [
        _chat_log(id=2, question="두 번째 질문", answer="두 번째 답", latency_ms=45),
        _chat_log(
            id=1,
            question="첫 질문",
            answer="",
            status="error",
            error_code="AI_TIMEOUT",
            latency_ms=30000,
        ),
    ]

    response = logged_in.get("/logs")

    crud.list_chat_logs.assert_called_once()
    assert crud.list_chat_logs.call_args.args[1] == user.id
    html = response.text
    assert "두 번째 질문" in html and "두 번째 답" in html and "45ms" in html
    assert "첫 질문" in html and "AI_TIMEOUT" in html
    assert "아직 기록이 없습니다" not in html


def test_시각은_KST_로_보인다(logged_in):
    """DB 는 UTC. 화면은 /chat 의 브라우저 시각과 같은 한국 시간이어야 한다 (#99)."""
    crud.list_chat_logs.return_value = [_chat_log()]  # created_at = 2026-09-13 10:30 UTC

    html = logged_in.get("/logs").text

    assert "09/13 19:30" in html
    assert "09/13 10:30" not in html


def test_기록_화면은_질문과_답변을_말풍선으로_구분한다(logged_in):
    """질문은 내 말풍선(is-user), 답변은 봇 말풍선(is-bot). 오류 답변은 is-error 를 함께 단다.

    CSS 가 이 클래스로 좌우 정렬과 배경을 가르므로, 이름은 chat.js 가 그리는 것과 같아야 한다.
    """
    crud.list_chat_logs.return_value = [
        _chat_log(id=2, question="두 번째 질문", answer="두 번째 답"),
        _chat_log(id=1, question="첫 질문", answer="", status="error", error_code="AI_TIMEOUT"),
    ]

    html = logged_in.get("/logs").text

    assert html.count('class="ask bubble is-user"') == 2
    assert 'class="answer bubble is-bot"' in html
    assert 'class="answer bubble is-bot is-error"' in html


def test_채팅_화면의_말풍선은_기록_화면과_같은_이름을_쓴다(client):
    """chat.js 가 그리는 항목과 logs.html 이 그리는 항목이 같은 CSS 를 타야 두 화면이 같아 보인다."""
    js = client.get("/static/chat.js").text

    assert '"ask bubble is-user"' in js
    assert '"answer bubble is-bot"' in js


def test_말풍선은_좌우_정렬과_배경으로_갈린다(client):
    """내 질문은 오른쪽·강조색 배경, 답변은 왼쪽·surface 배경. 토큰을 쓰므로 다크 테마도 같이 갈린다."""
    css = client.get("/static/style.css").text

    user = re.search(r"\.bubble\.is-user\s*\{[^}]*\}", css)
    bot = re.search(r"\.bubble\.is-bot\s*\{[^}]*\}", css)
    assert user and "flex-end" in user.group(0) and "var(--accent-soft)" in user.group(0)
    assert bot and "flex-start" in bot.group(0) and "var(--surface)" in bot.group(0)


def test_한글_조합_중_Enter는_전송하지_않는다(client):
    """IME 가 마지막 글자를 조합하는 중의 Enter(isComposing)는 조합 확정이지 전송이 아니다.

    이때 보내면 조합 중이던 글자가 빈 입력창에 다시 들어가 한 글자짜리 질문이 한 번 더 나간다.
    """
    js = client.get("/static/chat.js").text
    keydown = js[js.index('addEventListener("keydown"') : js.index('addEventListener("submit"')]

    assert "isComposing" in keydown


def test_응답을_기다리는_동안은_다시_전송하지_않는다(client):
    """보내기 버튼만 비활성화하면 Enter(requestSubmit)로는 여전히 보낼 수 있다. submit 자체가 막아야 한다."""
    js = client.get("/static/chat.js").text
    submit = js[js.index('addEventListener("submit"') : js.index("var slot = addEntry(question)")]

    assert "send.disabled" in submit


def test_답변은_두_화면이_같은_마크다운_렌더러로_그린다(logged_in):
    """chat.js 가 그리는 답변과 logs.html 의 답변이 같은 renderMarkdown 을 타야 같아 보인다."""
    chat_html = logged_in.get("/chat").text
    logs_html = logged_in.get("/logs").text
    js = logged_in.get("/static/chat.js").text

    assert 'src="/static/markdown.js"' in chat_html
    assert 'src="/static/markdown.js"' in logs_html
    assert "renderMarkdown(" in js
    assert "renderMarkdown(" in logs_html


def test_오류_답변은_마크다운으로_그리지_않는다(client):
    """오류 문구는 서버가 정한 평문이다. settle 이 isError 일 때는 textContent 로 둔다."""
    js = client.get("/static/chat.js").text
    settle = js[js.index("function settle(") : js.index("input.addEventListener")]

    assert "renderMarkdown(" in settle and "isError" in settle and "textContent" in settle


def test_비로그인_기록_API는_JSON_401(client):
    response = client.get("/api/me/chats")

    assert response.status_code == 401
    assert response.json()["error_code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_로그아웃은_링크가_아니라_POST_버튼이다(logged_in, path):
    """GET /logout 은 존재하지 않는다. 쿠키 삭제는 A 의 POST /api/auth/logout 만 한다."""
    html = logged_in.get(path).text

    assert 'href="/logout"' not in html
    assert 'id="logout"' in html
    assert "/api/auth/logout" in html


# ── 로그인·회원가입 화면 (인증 불필요) ──────────────────────────


def test_로그인_화면이_뜬다(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert 'id="login-form"' in response.text
    # 폼이 가리키는 API 는 A 의 계약(#67)이다. 템플릿이 {% set %} 으로 정한다.
    assert "/api/auth/login" in response.text


def test_회원가입_화면이_뜬다(client):
    response = client.get("/register")

    assert response.status_code == 200
    assert 'id="register-form"' in response.text
    assert "/api/auth/register" in response.text


# ── 배선 ────────────────────────────────────────────────────────


def test_정적_파일이_서빙된다(client):
    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert "--muted" in response.text


def test_API_문서에는_API만_있다(client):
    """/docs 는 B·C 가 계약을 보는 곳이다. 인증 API 는 있어야 하고 HTML 라우트는 없어야 한다."""
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/auth/login" in paths
    assert "/api/me" in paths
    assert "/api/me/chats" in paths
    for page in ("/chat", "/logs", "/login", "/register"):
        assert page not in paths


def test_경로_상수가_절대경로다():
    """Vercel 함수의 cwd 는 프로젝트 루트(/var/task)이고 앱은 그 아래 app/ 이다.

    상대경로로 쓰면 /var/task/templates 를 찾다 실패한다.  → 배포에서만 깨진다.
    """
    assert TEMPLATES_DIR.is_absolute()
    assert STATIC_DIR.is_absolute()


def test_작업_디렉터리가_달라도_렌더링된다(logged_in, tmp_path):
    """cwd 를 옮겨 Vercel 의 경로 불일치를 로컬에서 재현한다.

    이 테스트가 없으면 상대경로 회귀가 CI 를 통과하고 배포에서만 터진다.
    """
    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert logged_in.get("/chat").status_code == 200
        assert logged_in.get("/logs").status_code == 200
    finally:
        os.chdir(original)
