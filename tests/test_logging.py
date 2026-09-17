"""request_id 전파 — 한 요청의 여러 로그 줄을 하나로 묶는다.  → 평가항목 30"""

import asyncio
import logging

from fastapi.testclient import TestClient

from app.logging_config import (
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.main import app

logger = logging.getLogger(__name__)


@app.get("/api/_t/logs-twice")
def _t_logs_twice():
    """한 요청 안에서 두 번 로그를 남긴다. 둘이 같은 id 를 가져야 한다."""
    logger.info("ai_call_start provider=test")
    logger.info("db_save_success chat_id=1")
    return {"request_id": get_request_id()}


@app.get("/api/_t/logs-boom")
def _t_logs_boom():
    """예상 못 한 예외. 이 줄이 가장 추적이 필요하다 (#157)."""
    raise RuntimeError("일부러 낸 예외")


client = TestClient(app)
# 500 을 응답으로 받아 보려면 서버 예외를 테스트로 다시 던지지 않게 해야 한다.
boom_client = TestClient(app, raise_server_exceptions=False)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _capture():
    """루트에 캡처 핸들러를 붙인다. 기존 핸들러의 필터를 그대로 빌려 쓴다."""
    handler = _Capture()
    root = logging.getLogger()
    for f in root.handlers[0].filters:
        handler.addFilter(f)
    root.addHandler(handler)
    return handler


def test_요청마다_다른_id_가_발급된다():
    a = client.get("/healthz").headers["X-Request-ID"]
    b = client.get("/healthz").headers["X-Request-ID"]
    assert a != b
    assert len(a) == 8


def test_한_요청의_모든_로그가_같은_id_를_갖는다():
    handler = _capture()
    try:
        res = client.get("/api/_t/logs-twice")
        rid = res.headers["X-Request-ID"]
        assert res.json()["request_id"] == rid

        mine = [r for r in handler.records if r.name == __name__]
        assert len(mine) == 2
        assert {r.request_id for r in mine} == {rid}
    finally:
        logging.getLogger().removeHandler(handler)


def test_정적파일은_로그를_남기지_않는다():
    handler = _capture()
    try:
        client.get("/static/style.css")
        received = [r for r in handler.records if "request_received" in r.getMessage()]
        assert received == []
    finally:
        logging.getLogger().removeHandler(handler)


def test_예상못한_예외의_응답에도_request_id_가_실린다():
    res = boom_client.get("/api/_t/logs-boom")
    assert res.status_code == 500
    assert res.json()["error_code"] == "INTERNAL_ERROR"
    assert len(res.headers.get("X-Request-ID", "")) == 8


def test_예상못한_예외의_로그가_그_요청의_id_를_갖는다():
    handler = _capture()
    try:
        res = boom_client.get("/api/_t/logs-boom")
        unhandled = [r for r in handler.records if "unhandled_exception" in r.getMessage()]
        assert len(unhandled) == 1
        assert unhandled[0].request_id != "-"
        assert unhandled[0].request_id == res.headers.get("X-Request-ID")
    finally:
        logging.getLogger().removeHandler(handler)


def test_요청_밖에서는_하이픈이_붙는다():
    assert get_request_id() == "-"


def test_동시_요청이_서로_섞이지_않는다():
    """ContextVar 를 쓰는 이유. 전역 변수였다면 나중 요청이 앞 요청의 id 를 덮어쓴다."""

    async def one(delay: float) -> tuple[str, str]:
        rid = new_request_id()
        token = set_request_id(rid)
        try:
            await asyncio.sleep(delay)   # 다른 요청에 실행을 넘긴다
            return rid, get_request_id()
        finally:
            reset_request_id(token)

    async def run():
        return await asyncio.gather(one(0.02), one(0.01), one(0.03))

    for expected, actual in asyncio.run(run()):
        assert expected == actual
