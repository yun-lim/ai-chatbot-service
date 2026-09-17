"""ai_client.generate 단위 테스트 — 재시도 정책과 SDK 예외 → 오류 코드 매핑.  → 평가항목 14, 28

test_chat_service.py 는 ai_client 모듈을 통째로 mock 하므로 이 파일의 함수는 거기서 검증되지 않는다.
그래서 SDK 의 기본 재시도(2회)가 우리 재시도(1회)와 곱해져 질문 한 번에 HTTP 시도가 6회 나가는 것을
테스트가 잡지 못했다 (#154). 재시도 정책은 이 파일의 for 루프 한 곳에만 있어야 한다.
"""

from unittest.mock import AsyncMock, Mock

import openai
import pytest

from app.schemas import AppError, ErrorCode
from app.services import ai_client

MESSAGES = [{"role": "user", "content": "IndexError 는 왜 나요?"}]


@pytest.fixture(autouse=True)
def fresh_client(monkeypatch):
    """모듈 전역의 클라이언트 캐시를 비우고, 재시도 사이의 대기를 없앤다."""
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setattr(ai_client, "_client", None)
    monkeypatch.setattr(ai_client.asyncio, "sleep", AsyncMock())


def _fake_client(monkeypatch, create: AsyncMock) -> AsyncMock:
    client = Mock()
    client.chat.completions.create = create
    monkeypatch.setattr(ai_client, "_get_client", Mock(return_value=client))
    return create


def _completion(content):
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message.content = content
    return response


def _status_error(cls, status: int):
    return cls("upstream", response=Mock(status_code=status), body=None)


def test_SDK_자체_재시도를_끈다():
    """openai SDK 의 max_retries 기본값은 2다. 끄지 않으면 우리 재시도와 곱해진다."""
    assert ai_client._get_client().max_retries == 0


@pytest.mark.anyio
async def test_정상_응답은_그대로_돌려주고_시스템_프롬프트를_맨_앞에_붙인다(monkeypatch):
    create = _fake_client(monkeypatch, AsyncMock(return_value=_completion("범위를 벗어난 인덱스입니다.")))

    assert await ai_client.generate(MESSAGES) == "범위를 벗어난 인덱스입니다."

    sent = create.call_args.kwargs["messages"]
    assert sent[0]["role"] == "system"
    assert sent[1:] == MESSAGES


@pytest.mark.anyio
async def test_타임아웃이_계속되면_정확히_두_번_시도하고_AI_TIMEOUT(monkeypatch):
    create = _fake_client(monkeypatch, AsyncMock(side_effect=openai.APITimeoutError(request=Mock())))

    with pytest.raises(AppError) as exc:
        await ai_client.generate(MESSAGES)

    assert exc.value.code is ErrorCode.AI_TIMEOUT
    assert create.await_count == 2


@pytest.mark.anyio
async def test_타임아웃_뒤_재시도가_성공하면_답을_돌려준다(monkeypatch):
    create = _fake_client(
        monkeypatch,
        AsyncMock(side_effect=[openai.APITimeoutError(request=Mock()), _completion("두 번째에 성공")]),
    )

    assert await ai_client.generate(MESSAGES) == "두 번째에 성공"
    assert create.await_count == 2


@pytest.mark.anyio
async def test_rate_limit_은_재시도하지_않고_RATE_LIMITED(monkeypatch):
    create = _fake_client(monkeypatch, AsyncMock(side_effect=_status_error(openai.RateLimitError, 429)))

    with pytest.raises(AppError) as exc:
        await ai_client.generate(MESSAGES)

    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert create.await_count == 1


@pytest.mark.anyio
async def test_API_오류는_한_번_더_시도하고_AI_ERROR(monkeypatch):
    create = _fake_client(monkeypatch, AsyncMock(side_effect=_status_error(openai.InternalServerError, 500)))

    with pytest.raises(AppError) as exc:
        await ai_client.generate(MESSAGES)

    assert exc.value.code is ErrorCode.AI_ERROR
    assert create.await_count == 2


@pytest.mark.anyio
async def test_SDK_밖의_예외는_AI_UNKNOWN(monkeypatch):
    create = _fake_client(monkeypatch, AsyncMock(side_effect=RuntimeError("뜻밖의 일")))

    with pytest.raises(AppError) as exc:
        await ai_client.generate(MESSAGES)

    assert exc.value.code is ErrorCode.AI_UNKNOWN
    assert create.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("empty", [None, ""])
async def test_빈_응답은_AI_ERROR_다(monkeypatch, empty):
    """content 가 None 이면 chat_logs.answer(NOT NULL)로 흘러가 500 이 된다. 여기서 AI 오류로 옮긴다."""
    _fake_client(monkeypatch, AsyncMock(return_value=_completion(empty)))

    with pytest.raises(AppError) as exc:
        await ai_client.generate(MESSAGES)

    assert exc.value.code is ErrorCode.AI_ERROR
