"""AI 호출이 모여 있는 유일한 파일.

Codyssey 공개 API(OpenAI 호환)를 호출한다. API 키는 이 파일 밖으로
나가지 않는다.  → 평가항목 27
"""

import asyncio
import logging
import os

import openai
from openai import AsyncOpenAI

from app.config import AI_BASE_URL, AI_MAX_OUTPUT_TOKENS, AI_MODEL, SYSTEM_PROMPT
from app.schemas import AppError, ErrorCode

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
MAX_RETRIES = 1


def _get_client() -> AsyncOpenAI:
    """지연 초기화. import 시점이 아니라 첫 호출 시점에 키를 읽는다."""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ["AI_API_KEY"],
            base_url=AI_BASE_URL,
            timeout=float(os.environ.get("AI_TIMEOUT_SECONDS", "10")),
            # SDK 의 기본값은 2다. 끄지 않으면 아래 MAX_RETRIES 와 곱해져 질문 한 번에 HTTP 시도가
            # 6회 나가고, 최악 약 63초로 Vercel maxDuration(60초)을 넘는다 — 그러면 AI_TIMEOUT 안내도
            # 실패 행도 남기지 못하고 함수가 끊긴다. 재시도 정책은 generate() 한 곳에만 둔다 (#154).
            max_retries=0,
        )
    return _client


async def generate(messages: list[dict]) -> str:
    """messages(사용자/이전대화만)를 받아 AI 응답 텍스트를 반환한다.

    실패 시 SDK 예외를 그대로 올리지 않고 AppError(ErrorCode)로 옮긴다.
    타임아웃 1회는 재시도하고, 그래도 실패하면 포기한다.  → 평가항목 14, 28
    """
    client = _get_client()
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=AI_MODEL,
                messages=full_messages,
                max_tokens=AI_MAX_OUTPUT_TOKENS,
            )

        except openai.APITimeoutError:
            logger.warning("ai_call_timeout attempt=%d", attempt)
            if attempt == MAX_RETRIES:
                raise AppError(ErrorCode.AI_TIMEOUT)
            await asyncio.sleep(0.5)

        except openai.RateLimitError:
            logger.warning("ai_call_rate_limited attempt=%d", attempt)
            raise AppError(ErrorCode.RATE_LIMITED)

        except openai.APIError:
            logger.warning("ai_call_api_error attempt=%d", attempt)
            if attempt == MAX_RETRIES:
                raise AppError(ErrorCode.AI_ERROR)
            await asyncio.sleep(0.5)

        except Exception:
            logger.exception("ai_call_unknown_error attempt=%d", attempt)
            raise AppError(ErrorCode.AI_UNKNOWN)

        else:
            # 호환 API 는 본문 없이 응답할 수 있다. 그대로 돌려주면 None 이 chat_logs.answer(NOT NULL)로
            # 흘러 DB 오류 → 500 이 된다. AI 쪽 문제이므로 AI 오류로 안내한다 (#154).
            content = response.choices[0].message.content
            if not content:
                logger.warning("ai_call_empty_response attempt=%d", attempt)
                raise AppError(ErrorCode.AI_ERROR)
            return content
