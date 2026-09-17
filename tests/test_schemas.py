"""schemas.py 계약 검증 — 평가항목 16(입력 검증)의 근거."""

import pytest
from pydantic import ValidationError

from app.schemas import ChatRequest, ErrorCode, ErrorResponse, RegisterRequest


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t\n "])
def test_공백만_입력은_거부한다(blank):
    with pytest.raises(ValidationError):
        ChatRequest(message=blank)


def test_길이_상한이_없다():
    # 명세는 "검증 로직 최소 1개"만 요구한다. 빈 입력·공백만 차단 2겹으로 충분하고,
    # 코딩 학습 챗봇이라 긴 코드 붙여넣기를 근거 없는 숫자로 막지 않는다.
    long_code = "가" * 100_000
    assert ChatRequest(message=long_code).message == long_code


def test_원본을_그대로_보존한다():
    # strip 하지 않는다 — 저장된 질문이 사용자 입력과 같아야 한다
    raw = "  IndexError는 왜 나요?  "
    assert ChatRequest(message=raw).message == raw


def test_회원가입_제약():
    RegisterRequest(username="abc", password="12345678")
    with pytest.raises(ValidationError):
        RegisterRequest(username="ab", password="12345678")  # username 3자 미만
    with pytest.raises(ValidationError):
        RegisterRequest(username="abc", password="1234567")  # password 8자 미만


def test_오류코드_집합이_계약대로다():
    # 개수만 세면 코드를 바꿔치기해도 통과한다. 집합을 그대로 비교한다.
    # 앞 8종은 계획서 7장 표, INTERNAL_ERROR 는 예상 못한 예외용으로 D가 추가했다.
    # NOT_FOUND · METHOD_NOT_ALLOWED 는 없는 주소·잘못된 메서드용이다 (#159).
    assert {c.value for c in ErrorCode} == {
        "VALIDATION_ERROR",
        "NOT_AUTHENTICATED",
        "INVALID_CREDENTIALS",
        "DUPLICATE_USERNAME",
        "AI_TIMEOUT",
        "RATE_LIMITED",
        "AI_ERROR",
        "AI_UNKNOWN",
        "INTERNAL_ERROR",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
    }


def test_오류코드는_문자열로_직렬화된다():
    body = ErrorResponse(error_code=ErrorCode.AI_TIMEOUT, message="지연").model_dump()
    assert body["error_code"] == "AI_TIMEOUT"
