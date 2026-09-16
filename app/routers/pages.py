"""화면 라우터 — 템플릿을 렌더링하는 곳은 여기 하나다.

인증은 deps.py 의 CurrentUser 한 곳이 판단한다 (#23). 쿠키가 없거나 깨지면
거기서 NOT_AUTHENTICATED 가 올라오고, main.py 의 핸들러가 화면 요청이면
/login 으로 돌린다. 여기서 쿠키를 읽지 않는다.  → 평가항목 20

DB 는 crud 한 곳을 통해서만 읽는다 (#36). 여기서 쿼리를 짜지 않는다.  → 평가항목 21
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import crud
from app.config import TEMPLATES_DIR
from app.deps import CurrentUser, DbSession

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# DB 는 UTC 로 저장한다. 화면은 한국 시간으로 — /chat 의 브라우저 시각과 같은 기준이어야
# 같은 대화가 두 화면에서 9시간 다르게 보이지 않는다. KST 는 DST 가 없어 고정 오프셋이면
# 충분하고, 서버리스 런타임의 tz 데이터베이스에 기대지 않는다.
KST = timezone(timedelta(hours=9), name="KST")


def _kst(value: datetime) -> str:
    return value.astimezone(KST).strftime("%m/%d %H:%M")


templates.env.filters["kst"] = _kst

# 화면 라우트는 OpenAPI 문서에 넣지 않는다. /docs 는 B·C 가 계약을 보는 곳이라
# HTML 라우트가 섞이면 읽어야 할 것이 묻힌다.
router = APIRouter(tags=["pages"], include_in_schema=False)

# /chat 에 미리 그리는 지난 대화 수. 서버 문맥(최근 5턴)보다 넉넉히 보여 주되 화면을 끝없이 키우진 않는다.
CHAT_HISTORY_LIMIT = 50


@router.get("/")
def index() -> RedirectResponse:
    """진입점. 미인증이면 /chat 이 다시 /login 으로 보낸다 — 판단은 한 곳에서."""
    return RedirectResponse("/chat", status_code=302)


@router.get("/login")
def login_page(request: Request):
    """로그인 화면. 템플릿(#73, A)이 제출 대상 API 를 자기 안에서 정한다."""
    return templates.TemplateResponse(request, "login.html")


@router.get("/register")
def register_page(request: Request):
    """회원가입 화면."""
    return templates.TemplateResponse(request, "register.html")


@router.get("/chat")
def chat_page(request: Request, user: CurrentUser, db: DbSession):
    """질문 화면. 로그인한 사용자만. 지난 대화를 미리 그려 새로고침해도 대화가 이어진다 (#145)."""
    # crud 는 최신순으로 준다. 화면은 대화처럼 오래된 것부터 아래로 쌓는다.
    items = list(reversed(crud.list_chat_logs(db, user.id, limit=CHAT_HISTORY_LIMIT)))
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"user": user, "items": items, "history_limit": CHAT_HISTORY_LIMIT},
    )


@router.get("/logs")
def logs_page(request: Request, user: CurrentUser, db: DbSession):
    """지난 대화 화면. 본인 기록만, 최신순."""
    items = crud.list_chat_logs(db, user.id)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"user": user, "items": items, "show_latency": True},
    )
