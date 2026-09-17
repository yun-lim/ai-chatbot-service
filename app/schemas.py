"""요청/응답 스키마 — 계약 ①.

바깥 경계(HTTP)의 입출력 형식을 여기 한 곳에서 정의한다.
라우터에서 dict 를 즉석 조립하지 않는다.  → 평가항목 19

DB 테이블 정의는 여기가 아니라 `models.py` 다. 같은 "스키마"라는 말을 쓰지만
이 파일은 JSON 형식을, models.py 는 테이블 구조를 다룬다.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorCode(StrEnum):
    """오류 코드 8종 — 계획서 7장 표와 1:1 대응.

    B가 AI 실패를 이 코드로 옮기고, D가 화면 문구로 옮긴다.
    문자열을 직접 쓰지 말고 이 enum 을 import 해서 쓴다.
    """

    VALIDATION_ERROR = "VALIDATION_ERROR"        # 422
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"      # 401
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"  # 401
    DUPLICATE_USERNAME = "DUPLICATE_USERNAME"    # 409
    AI_TIMEOUT = "AI_TIMEOUT"                    # 503
    RATE_LIMITED = "RATE_LIMITED"                # 503
    AI_ERROR = "AI_ERROR"                        # 503
    AI_UNKNOWN = "AI_UNKNOWN"                    # 503

    # 계획서 7장 표에는 없는 9번째 코드다. 위 8종은 "예상한 실패"를 다루는데,
    # 우리 코드의 버그처럼 예상 못한 예외에는 붙일 코드가 없었다.
    # AI_UNKNOWN(503)을 재사용하면 DB 오류를 AI 문제로 잘못 알리게 된다.
    INTERNAL_ERROR = "INTERNAL_ERROR"            # 500

    # 없는 주소와 잘못된 메서드. 가장 "예상한" 오류인데 매핑이 없어 INTERNAL_ERROR 와
    # 프레임워크의 영어 문구("Not Found")로 나가고 있었다 (#159).
    NOT_FOUND = "NOT_FOUND"                      # 404
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"    # 405


# 코드별 HTTP 상태. 라우터가 상태코드를 직접 고르지 않고 여기를 따른다.
ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_AUTHENTICATED: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.DUPLICATE_USERNAME: 409,
    ErrorCode.AI_TIMEOUT: 503,
    ErrorCode.RATE_LIMITED: 503,
    ErrorCode.AI_ERROR: 503,
    ErrorCode.AI_UNKNOWN: 503,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
}

# 사용자에게 보이는 확정 문구. B가 코드를 던지고 D가 화면에 그리므로
# 문구가 흩어지면 화면에 엉뚱한 말이 뜬다. 여기가 유일한 출처다.
ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "입력값이 올바르지 않습니다.",
    ErrorCode.NOT_AUTHENTICATED: "로그인이 필요합니다.",
    ErrorCode.INVALID_CREDENTIALS: "아이디 또는 비밀번호가 올바르지 않습니다.",
    ErrorCode.DUPLICATE_USERNAME: "이미 사용 중인 아이디입니다.",
    ErrorCode.AI_TIMEOUT: "현재 응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
    ErrorCode.RATE_LIMITED: "요청이 많아 잠시 대기가 필요해요. 30초 후 다시 시도해 주세요.",
    ErrorCode.AI_ERROR: "AI 응답 생성에 실패했어요. 잠시 후 다시 시도해 주세요.",
    ErrorCode.AI_UNKNOWN: "알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
    ErrorCode.INTERNAL_ERROR: "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
    ErrorCode.NOT_FOUND: "요청한 주소를 찾을 수 없습니다.",
    ErrorCode.METHOD_NOT_ALLOWED: "허용되지 않는 요청 방식입니다.",
}


class AppError(Exception):
    """서비스가 의도적으로 올리는 오류.

    라우터·서비스는 상태코드와 문구를 직접 고르지 않고 이것만 올린다.
    main.py 의 전역 핸들러가 ERROR_STATUS·ERROR_MESSAGES 를 보고 응답으로 바꾼다.

        raise AppError(ErrorCode.DUPLICATE_USERNAME)
    """

    def __init__(self, code: ErrorCode, message: str | None = None):
        self.code = code
        self.message = message or ERROR_MESSAGES[code]
        super().__init__(f"{code.value}: {self.message}")


class ErrorResponse(BaseModel):
    """모든 오류 응답의 공통 형식."""

    error_code: ErrorCode
    message: str


# ─────────────────────────────  인증 (A가 사용)  ─────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    # ORM 객체에서 바로 만들 수 있게 한다: UserResponse.model_validate(user)
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


# ─────────────────────────────  챗봇 (B가 사용)  ─────────────────────────────



class ChatRequest(BaseModel):
    """길이 상한을 두지 않는다.

    명세 L90·L205 는 "입력 검증 로직 최소 1개"만 요구하고 길이 제한은 예시 중 하나다.
    빈 입력·공백만 차단 2겹으로 평가항목 16 은 충족된다.
    코딩 학습 챗봇이라 사용자가 코드를 통째로 붙여넣는 것이 주 사용례이므로,
    근거 없는 숫자로 그걸 막지 않는다.
    """

    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """공백만 입력된 메시지를 막는다.  → 평가항목 16

        검사만 하고 원본을 그대로 돌려준다. strip 한 값을 반환하면
        DB에 저장된 질문이 사용자가 실제로 보낸 것과 달라지는데,
        chat_logs 는 "무슨 일이 있었는지"를 남기는 테이블이므로
        입력을 손대지 않는 쪽을 택했다.
        """
        if not value.strip():
            raise ValueError("메시지를 입력해 주세요.")
        return value


class ChatResponse(BaseModel):
    answer: str
    chat_id: int


# ─────────────────────────  대화 로그 조회 (C가 사용)  ─────────────────────────

class ChatLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: int
    request_id: str
    question: str
    answer: str
    status: Literal["success", "error"]
    error_code: ErrorCode | None = None
    latency_ms: int
    created_at: datetime
