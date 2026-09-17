"""화면 라우터 — 접근 제어와 렌더링을 검증한다.

#68 이전엔 /chat·/logs 가 누구에게나 열려 있었다(인증 dependency 가 없어서).
지금은 쿠키가 없으면 /login 으로 보내고, 있으면 그 사용자의 화면을 그린다.
인증 판단은 deps.py 한 곳이 하고, 여기서는 그 결과가 화면에 어떻게 보이는지만 본다.
"""

import html as html_lib
import json
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


def test_채팅_화면은_더_불러올_수_있는지_알려준다(logged_in):
    """서버가 그린 수와 한도를 마크업에 남겨야 JS 가 다음 offset 을 안다.  → #148"""
    from app.routers.pages import CHAT_HISTORY_LIMIT

    crud.list_chat_logs.return_value = [_chat_log(id=2), _chat_log(id=1)]

    html = logged_in.get("/chat").text

    assert f'data-history-limit="{CHAT_HISTORY_LIMIT}"' in html
    assert 'data-history-loaded="2"' in html
    assert 'id="history-top"' in html


def test_맨_위에_닿으면_다음_묶음을_API_로_불러온다(client):
    """더 오래된 대화는 C 의 조회 API(limit · offset)로 받아 위에 끼운다. 서버 렌더링과 같은 마크업이어야 한다."""
    js = client.get("/static/chat.js").text

    assert "IntersectionObserver" in js
    assert "/api/me/chats?limit=" in js and "offset=" in js
    assert '"ask bubble is-user"' in js and '"answer bubble is-bot"' in js


def test_더_불러오기_offset_은_이번_접속에서_저장된_질문_수를_더한다(client):
    """조회 API 는 최신순이라 새 행이 쌓이면 이미 그린 항목이 그만큼 뒤로 밀린다.

    offset 에 그 수를 더하지 않으면 새로 보낸 질문 수만큼 중복으로 끼워진다.  → #161
    """
    js = client.get("/static/chat.js").text

    assert "&offset=\" + (historyLoaded + savedThisSession)" in js


def test_저장되는_응답에서만_센다(client):
    """서버가 chat_logs 에 남기는 것은 성공(200)과 AI 실패(503)다. 422·401·네트워크 오류는 저장되지 않는다."""
    js = client.get("/static/chat.js").text
    submit = js[js.index("var slot = addEntry(question)"):]

    assert "if (r.ok || r.status === 503) savedThisSession += 1;" in submit
    # 네트워크 오류에서는 세지 않는다 — 서버에 닿지 못했다.
    # (첫 .catch 는 res.json() 의 것이라, 네트워크 오류 처리는 그 주석으로 찾는다.)
    network_failure = submit[submit.index("// 네트워크 자체가 끊긴 경우"):submit.index(".finally(")]
    assert "savedThisSession" not in network_failure


def test_채팅_화면의_지난_대화에는_시각만_있고_응답_시간은_없다(logged_in):
    """ms 는 운영 추적값이다. 학습자가 채팅하며 볼 값이 아니라 /logs 에만 둔다.  → #151"""
    crud.list_chat_logs.return_value = [_chat_log(latency_ms=321)]

    html = logged_in.get("/chat").text

    assert "09/13 19:30" in html
    assert "321ms" not in html


def test_기록_화면에는_응답_시간이_보인다(logged_in):
    crud.list_chat_logs.return_value = [_chat_log(latency_ms=321)]

    html = logged_in.get("/logs").text

    assert "09/13 19:30" in html and "321ms" in html


def test_채팅_화면의_실시간_답변에도_응답_시간을_붙이지_않는다(client):
    js = client.get("/static/chat.js").text

    assert '"ms"' not in js


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


def test_기록이_한도를_넘으면_더_있다고_알린다(logged_in):
    """조용히 잘라내면 21번째부터는 "없는 것"처럼 보인다 — "아직 안 보임"은 "없음"과 다르게 낸다.  → #162"""
    from app.routers.pages import LOGS_PAGE_LIMIT

    # 화면은 한 건 더 읽어 "더 있는지"를 알아낸다. crud 는 최신순으로 준다.
    crud.list_chat_logs.return_value = [
        _chat_log(id=i, question=f"질문 {i}") for i in range(LOGS_PAGE_LIMIT + 1, 0, -1)
    ]

    html = logged_in.get("/logs").text

    assert crud.list_chat_logs.call_args.kwargs["limit"] == LOGS_PAGE_LIMIT + 1
    assert html.count('class="entry"') == LOGS_PAGE_LIMIT
    assert "질문 1<" not in html                      # 가장 오래된 한 건은 그리지 않는다
    more = html[html.index('id="logs-more"'):][:400]  # 안내가 없으면 여기서 ValueError
    assert f"{LOGS_PAGE_LIMIT}건" in more
    assert 'href="/chat"' in more                     # 전체를 보는 길


def test_기록이_한도_이하면_안내가_없다(logged_in):
    from app.routers.pages import LOGS_PAGE_LIMIT

    crud.list_chat_logs.return_value = [_chat_log(id=i) for i in range(LOGS_PAGE_LIMIT, 0, -1)]

    html = logged_in.get("/logs").text

    assert html.count('class="entry"') == LOGS_PAGE_LIMIT
    assert 'id="logs-more"' not in html


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


# ── 답변 시각은 말풍선 오른쪽 아래에 (#169) ─────────────────────
# 메타가 항목 맨 아래·전체 폭의 오른쪽 끝에 있으면 왼쪽의 답변 말풍선과 떨어져 어느 말풍선의
# 시각인지 애매하다. 답변과 시각을 한 줄로 묶고 아래 끝을 맞춘다.

_ANSWER_ROW = re.compile(
    r'<div class="answer-row">\s*'
    r'<p class="answer bubble is-bot[^"]*">.*?</p>\s*'
    r'<div class="entry-meta">',
    re.DOTALL,
)


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_시각은_답변_말풍선과_같은_줄에_바로_뒤에_온다(logged_in, path):
    crud.list_chat_logs.return_value = [_chat_log()]

    html = logged_in.get(path).text

    assert _ANSWER_ROW.search(html)


def test_chat_js_가_만드는_항목도_같은_구조다(client):
    """실시간 항목(addEntry)과 더 불러온 항목(buildEntry) 둘 다 answer-row 에 답변 → 시각 순으로 넣는다."""
    js = client.get("/static/chat.js").text

    assert js.count('row.className = "answer-row"') == 2
    assert js.count("row.appendChild(answer);\n    row.appendChild(meta);") == 2
    # 메타를 항목 맨 앞에 따로 붙이던 옛 구조가 남아 있으면 안 된다.
    assert "entry.appendChild(meta)" not in js


def test_답변_줄은_시각의_아래_끝을_말풍선에_맞춘다(client):
    css = client.get("/static/style.css").text

    row = re.search(r"\.answer-row\s*\{[^}]*\}", css)
    assert row and "display: flex" in row.group(0) and "align-items: flex-end" in row.group(0)
    meta = re.search(r"\.entry-meta\s*\{[^}]*\}", css)
    assert meta and "order:" not in meta.group(0) and "text-align: right" not in meta.group(0)


# ── 저장된 오류 항목도 안내 문구를 보여 준다 (#171) ─────────────
# 실패 행은 answer="" 로 저장된다. 다시 그릴 때 말풍선이 비면 "무슨 일이 있었는지"가 화면에서 사라진다.


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_오류_항목의_말풍선에는_그_코드의_안내_문구가_보인다(logged_in, path):
    from app.schemas import ERROR_MESSAGES, ErrorCode

    crud.list_chat_logs.return_value = [_chat_log(status="error", answer="", error_code="AI_TIMEOUT")]

    html = logged_in.get(path).text

    bubble = re.search(r'<p class="answer bubble is-bot is-error">(.*?)</p>', html, re.DOTALL)
    assert bubble and bubble.group(1).strip() == ERROR_MESSAGES[ErrorCode.AI_TIMEOUT]


def test_모르는_오류_코드는_내부_오류_문구로_보인다(logged_in):
    """DB 에 옛 코드가 남아 있어도 화면이 깨지거나 비지 않는다."""
    from app.schemas import ERROR_MESSAGES, ErrorCode

    crud.list_chat_logs.return_value = [_chat_log(status="error", answer="", error_code="SOMETHING_OLD")]

    html = logged_in.get("/logs").text

    assert ERROR_MESSAGES[ErrorCode.INTERNAL_ERROR] in html


def test_더_불러온_오류_항목도_같은_문구를_쓴다(logged_in):
    """문구의 출처는 schemas 하나다. JS 에 복사해 두지 않고 마크업으로 내려 준다."""
    from app.schemas import ERROR_MESSAGES, ErrorCode

    html = logged_in.get("/chat").text
    js = logged_in.get("/static/chat.js").text

    attr = re.search(r"data-error-messages='([^']*)'", html)
    assert attr
    # tojson 은 한글을 \\uXXXX 로 내보낸다. 브라우저의 JSON.parse 처럼 풀어서 사전 전체를 비교한다.
    assert json.loads(html_lib.unescape(attr.group(1))) == {c.value: m for c, m in ERROR_MESSAGES.items()}
    assert ErrorCode.AI_TIMEOUT.value in attr.group(1)
    assert 'getAttribute("data-error-messages")' in js
    assert "item.answer || ERROR_MESSAGES[item.error_code] || FALLBACK" in js


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


@pytest.mark.parametrize("path", ["/chat", "/logs"])
def test_렌더러보다_먼저_marked_와_DOMPurify_를_불러온다(logged_in, path):
    """markdown.js 는 두 전역(marked · DOMPurify)을 쓴다. defer 는 문서 순서대로 실행되므로 순서가 곧 계약이다.  → #172"""
    html = logged_in.get(path).text

    order = [html.index(f'src="/static/{name}"') for name in ("vendor/marked.umd.js", "vendor/purify.min.js", "markdown.js")]
    assert order == sorted(order)
    # CDN 이 아니라 우리 서버가 준다 — 빌드 단계가 없는 프로젝트라 파일로 넣었다.
    assert "cdn.jsdelivr" not in html and "unpkg.com" not in html


def test_vendor_파일이_서빙된다(client):
    for name in ("marked.umd.js", "purify.min.js"):
        response = client.get(f"/static/vendor/{name}")
        assert response.status_code == 200 and len(response.content) > 10_000


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
