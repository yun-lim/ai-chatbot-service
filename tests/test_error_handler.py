"""전역 예외 핸들러 — 모든 오류가 {error_code, message} 형식으로 나가는지 검증.

static/chat.js 가 이 형식만 파싱하므로, 하나라도 다른 모양이면
사용자에게 아무 안내도 못 띄운다.  → 평가항목 15
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ERROR_MESSAGES, ERROR_STATUS, AppError, ErrorCode

# 아래 라우트는 실제 app 에 붙는다. 핸들러를 복제한 가짜 앱이 아니라
# 배포되는 그 앱의 동작을 검사하기 위해서다.
# 이 모듈은 pytest 에서만 import 되므로 프로덕션 라우트에는 나타나지 않는다.


@app.post("/api/_t/validate")
def _t_validate(payload: dict):
    return payload


@app.get("/api/_t/app-error")
def _t_app_error():
    raise AppError(ErrorCode.AI_TIMEOUT)


@app.get("/api/_t/http-401")
def _t_http_401():
    raise HTTPException(status_code=401)


@app.get("/_t/page-401")
def _t_page_401():
    raise AppError(ErrorCode.NOT_AUTHENTICATED)


@app.get("/api/_t/boom")
def _t_boom():
    raise RuntimeError("내부 사정")


client = TestClient(app, raise_server_exceptions=False)


def test_모든_코드에_상태와_문구가_있다():
    missing = [c.value for c in ErrorCode if c not in ERROR_STATUS or c not in ERROR_MESSAGES]
    assert missing == []


def test_검증_실패는_VALIDATION_ERROR_로_나간다():
    res = client.post("/api/_t/validate", content="not json")
    assert res.status_code == 422
    assert res.json()["error_code"] == "VALIDATION_ERROR"
    assert res.json()["message"] == ERROR_MESSAGES[ErrorCode.VALIDATION_ERROR]


def test_AppError_는_코드의_상태와_문구를_따른다():
    res = client.get("/api/_t/app-error")
    assert res.status_code == 503
    assert res.json() == {
        "error_code": "AI_TIMEOUT",
        "message": ERROR_MESSAGES[ErrorCode.AI_TIMEOUT],
    }


def test_API_의_401_은_JSON_이다():
    res = client.get("/api/_t/http-401")
    assert res.status_code == 401
    assert res.json()["error_code"] == "NOT_AUTHENTICATED"


def test_화면의_401_은_로그인으로_보낸다():
    res = client.get("/_t/page-401", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_예상못한_예외는_내부정보를_흘리지_않는다():
    res = client.get("/api/_t/boom")
    assert res.status_code == 500
    assert res.json()["error_code"] == "INTERNAL_ERROR"
    assert "내부 사정" not in res.text


def test_없는_경로도_같은_형식으로_나간다():
    res = client.get("/api/_t/nope")
    assert res.status_code == 404
    assert "error_code" in res.json()


# ── 없는 주소 · 잘못된 메서드 (#159) ────────────────────────────
# 404 는 가장 예상된 오류다. "예상 못한 예외"용 INTERNAL_ERROR 와 프레임워크의 영어 문구로 나가면 안 된다.


def test_없는_API_경로는_NOT_FOUND_와_우리_문구로_나간다():
    res = client.get("/api/_t/nope")
    assert res.status_code == 404
    assert res.json() == {
        "error_code": "NOT_FOUND",
        "message": ERROR_MESSAGES[ErrorCode.NOT_FOUND],
    }


def test_잘못된_메서드는_METHOD_NOT_ALLOWED_로_나간다():
    res = client.get("/api/_t/validate")  # POST 전용 라우트
    assert res.status_code == 405
    assert res.json() == {
        "error_code": "METHOD_NOT_ALLOWED",
        "message": ERROR_MESSAGES[ErrorCode.METHOD_NOT_ALLOWED],
    }


def test_화면의_없는_주소는_JSON_이_아니라_안내_화면이다():
    res = client.get("/chatt")
    assert res.status_code == 404
    assert "text/html" in res.headers["content-type"]
    assert ERROR_MESSAGES[ErrorCode.NOT_FOUND] in res.text
    assert 'href="/chat"' in res.text


def test_없는_정적파일은_화면이_아니라_JSON_이다():
    # 브라우저가 <script>·<link> 로 받는 자리에 HTML 을 주지 않는다.
    res = client.get("/static/nope.js")
    assert res.status_code == 404
    assert res.json()["error_code"] == "NOT_FOUND"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_문구가_비어있지_않다(code):
    assert ERROR_MESSAGES[code].strip()
