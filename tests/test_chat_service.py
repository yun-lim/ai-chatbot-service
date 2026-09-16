"""chat_service.handle_chat 단위 테스트.

crud, ai_client는 실제 DB·API 호출 없이 mock으로 대체한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas import AppError, ChatResponse, ErrorCode
from app.services import chat_service


class FakeTurn:
    def __init__(self, question: str, answer: str):
        self.question = question
        self.answer = answer


@pytest.fixture
def db():
    return MagicMock()


@pytest.mark.anyio
async def test_handle_chat_success_saves_and_returns_answer(db):
    """정상 응답이면 status=success로 저장하고 ChatResponse를 반환한다."""
    with (
        patch("app.services.chat_service.crud") as mock_crud,
        patch("app.services.chat_service.ai_client") as mock_ai_client,
        patch("app.services.chat_service.get_request_id", return_value="req-1"),
    ):
        mock_crud.recent_turns.return_value = [FakeTurn("이전질문", "이전답변")]
        mock_ai_client.generate = AsyncMock(return_value="AI 답변")
        mock_crud.create_chat_log.return_value = MagicMock(id=42)

        result = await chat_service.handle_chat(db, user_id=1, question="새 질문")

        assert isinstance(result, ChatResponse)
        assert result.answer == "AI 답변"
        assert result.chat_id == 42

        mock_crud.create_chat_log.assert_called_once()
        _, kwargs = mock_crud.create_chat_log.call_args
        assert kwargs["status"] == "success"
        assert kwargs["error_code"] is None
        assert kwargs["answer"] == "AI 답변"


@pytest.mark.anyio
async def test_handle_chat_ai_failure_saves_error_and_reraises(db):
    """AI 호출이 실패하면 status=error로 저장하고 AppError를 다시 던진다."""
    with (
        patch("app.services.chat_service.crud") as mock_crud,
        patch("app.services.chat_service.ai_client") as mock_ai_client,
        patch("app.services.chat_service.get_request_id", return_value="req-2"),
    ):
        mock_crud.recent_turns.return_value = []
        mock_ai_client.generate = AsyncMock(
            side_effect=AppError(ErrorCode.AI_TIMEOUT)
        )

        with pytest.raises(AppError) as exc_info:
            await chat_service.handle_chat(db, user_id=1, question="질문")

        assert exc_info.value.code == ErrorCode.AI_TIMEOUT

        mock_crud.create_chat_log.assert_called_once()
        _, kwargs = mock_crud.create_chat_log.call_args
        assert kwargs["status"] == "error"
        assert kwargs["error_code"] == ErrorCode.AI_TIMEOUT
        assert kwargs["answer"] == ""


@pytest.mark.anyio
async def test_handle_chat_db_save_failure_does_not_hide_original_error(db):
    """로그 저장 자체가 실패해도 원래 AppError는 그대로 올라간다."""
    with (
        patch("app.services.chat_service.crud") as mock_crud,
        patch("app.services.chat_service.ai_client") as mock_ai_client,
        patch("app.services.chat_service.get_request_id", return_value="req-3"),
    ):
        mock_crud.recent_turns.return_value = []
        mock_ai_client.generate = AsyncMock(
            side_effect=AppError(ErrorCode.AI_ERROR)
        )
        mock_crud.create_chat_log.side_effect = Exception("db down")

        with pytest.raises(AppError) as exc_info:
            await chat_service.handle_chat(db, user_id=1, question="질문")

        assert exc_info.value.code == ErrorCode.AI_ERROR
