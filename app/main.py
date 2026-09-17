"""B7-1 코딩 학습 Q&A 챗봇 — 앱 진입점.

Vercel은 이 파일의 `app` 변수를 함수 진입점으로 인식하며,
FastAPI 앱 전체가 단일 서버리스 함수로 배포된다.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import STATIC_DIR
from app.logging_config import (
    configure_logging,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.routers import auth, chat, logs, pages
from app.schemas import ERROR_MESSAGES, ERROR_STATUS, AppError, ErrorCode

# 서버리스는 요청마다 기동될 수 있어 시작 훅이 아니라 import 시점에 설정한다.
configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="코딩 학습 Q&A 챗봇")


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """요청마다 id 를 발급해 그 요청이 남기는 모든 로그 줄에 붙인다.

    B·C 는 이 값을 인자로 넘겨받지 않는다. logging_config.get_request_id() 로
    언제든 읽을 수 있고, 로그에는 자동으로 붙는다.  → 평가항목 30
    """
    request_id = new_request_id()
    token = set_request_id(request_id)
    try:
        # 정적 파일은 요청 수가 많고 추적 가치가 없어 남기지 않는다.
        if not request.url.path.startswith("/static"):
            logger.info(
                "request_received method=%s path=%s", request.method, request.url.path
            )
        try:
            response = await call_next(request)
        except Exception:
            # 예상 못한 예외는 여기서 잡는다. 아래 전역 핸들러(Exception)는 이 미들웨어보다
            # 바깥(ServerErrorMiddleware)에서 돌아서, 거기까지 가면 request_id 가 이미 "-" 로
            # 돌아가 있다 — 가장 추적이 필요한 줄이 추적이 안 된다 (#157).
            logger.exception("unhandled_exception path=%s", request.url.path)
            response = _error(ErrorCode.INTERNAL_ERROR)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 화면 라우트는 전부 routers/pages.py 에 있다. 템플릿 인스턴스도 그쪽이 갖는다
# — 여기 두면 렌더링하지 않는 파일이 렌더링 설정을 들고 있게 된다.
app.include_router(pages.router)
app.include_router(auth.router)  # /api/auth/* · /api/me  (A, #67)
app.include_router(logs.router)  # /api/me/chats  (C, #36)
app.include_router(chat.router)  # /api/chat  (B, #49)


@app.get("/healthz")
def healthz():
    """배포 확인용 헬스체크. CI 스모크 테스트가 이 응답을 검사한다."""
    return {"status": "ok"}


# ── 전역 예외 처리 ──────────────────────────────────────────────
#
# 오류가 세 갈래(검증 실패 / HTTPException / 예상 못한 예외)로 나가면
# 화면이 읽을 수 없다. static/chat.js 는 {error_code, message} 하나만 파싱한다.
# 여기서 전부 그 형식으로 모은다.  → 평가항목 15


def _wants_html(request: Request) -> bool:
    """화면 요청인가. /api/* 는 JSON, 그 외는 사람이 보는 HTML이다."""
    return not request.url.path.startswith("/api/")


def _error(code: ErrorCode, message: str | None = None, status: int | None = None):
    return JSONResponse(
        status_code=status or ERROR_STATUS[code],
        content={"error_code": code.value, "message": message or ERROR_MESSAGES[code]},
    )


# 라우터가 HTTPException 을 올리는 경우를 위한 최소 매핑.
# 새 코드는 AppError 를 쓰는 편이 낫다 — 상태코드를 직접 고르지 않아도 된다.
_STATUS_TO_CODE = {
    401: ErrorCode.NOT_AUTHENTICATED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.DUPLICATE_USERNAME,
    422: ErrorCode.VALIDATION_ERROR,
}

# 사람이 주소창에서 마주치는 오류. 화면 요청이면 JSON 이 아니라 안내 화면으로 낸다 (#159).
_PAGE_ERRORS = {ErrorCode.NOT_FOUND, ErrorCode.METHOD_NOT_ALLOWED}


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=302)


def _error_page(request: Request, code: ErrorCode):
    """화면용 오류 안내. 문구는 API 와 같은 출처(ERROR_MESSAGES)를 쓴다."""
    status = ERROR_STATUS[code]
    return pages.templates.TemplateResponse(
        request,
        "error.html",
        {"status": status, "message": ERROR_MESSAGES[code]},
        status_code=status,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """Pydantic 검증 실패. 기본 응답은 loc·type 이 담긴 개발자용이라 사용자에게 못 보여준다."""
    return _error(ErrorCode.VALIDATION_ERROR)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    """서비스가 의도적으로 올린 오류."""
    if exc.code is ErrorCode.NOT_AUTHENTICATED and _wants_html(request):
        return _login_redirect()
    return _error(exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    code = _STATUS_TO_CODE.get(exc.status_code)

    if exc.status_code == 401 and _wants_html(request):
        return _login_redirect()
    if code is not None:
        # 없는 정적 파일은 <script>·<link> 가 받는 자리라 화면이 아니라 JSON 으로 둔다.
        is_static = request.url.path.startswith("/static/")
        if code in _PAGE_ERRORS and _wants_html(request) and not is_static:
            return _error_page(request, code)
        return _error(code)

    # 매핑이 없는 드문 상태는 상태코드를 유지하고 형식만 맞춘다.
    return _error(ErrorCode.INTERNAL_ERROR, str(exc.detail), status=exc.status_code)


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    """예상 못한 예외. 내부 정보를 사용자에게 흘리지 않고 서버 로그에만 남긴다.

    라우터에서 난 예외는 attach_request_id 가 먼저 잡는다 (#157). 여기는 그 미들웨어
    바깥에서 난 예외까지 받는 최후 방어선이다.
    어떤 경우에도 서버 프로세스는 죽지 않는다.  → 평가항목 14
    """
    logger.exception("unhandled_exception path=%s", request.url.path)
    return _error(ErrorCode.INTERNAL_ERROR)
