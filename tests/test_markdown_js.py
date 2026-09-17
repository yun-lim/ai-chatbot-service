"""AI 답변 마크다운 렌더링(static/markdown.js) — marked 로 바꾸고 DOMPurify 로 정제한다.  → #172

AI 출력은 신뢰할 수 없는 입력이다. marked 는 원문 HTML 을 그대로 통과시키므로 DOMPurify 는 선택이 아니라
필수다. 여기서는 **우리 코드가 맡은 부분**을 Node 로 직접 실행해 검증한다:

  1. 정제기를 쓸 수 없으면 원문을 이스케이프해 글자로 돌려준다 — 정제하지 않은 HTML 이 나가는 경로가 없다
     (Node 에는 DOM 이 없어 DOMPurify 가 돌지 않는다. 그래서 Node 에서의 renderMarkdown 이 곧 그 폴백이다)
  2. marked 설정 — 제목 단계 · 줄바꿈 · 코드 블록 · 표
  3. vendor 파일이 기록해 둔 해시 그대로다

DOMPurify 의 실제 정제(<script> · onerror · javascript: …)는 브라우저에서 확인하고 이슈 #172 에 기록했다.
"""

import hashlib
import json
import re
import shutil
import subprocess

import pytest

from app.config import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음 — CI 러너와 로컬엔 있다")

VENDOR = STATIC_DIR / "vendor"
SOURCE = (STATIC_DIR / "markdown.js").read_text(encoding="utf-8")


def _node(expr: str, markdown: str) -> str:
    script = (
        f"const m = require({json.dumps(str(STATIC_DIR / 'markdown.js'))});"
        f"process.stdout.write(m.{expr}({json.dumps(markdown)}));"
    )
    return subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout


def render(markdown: str) -> str:
    """브라우저가 부르는 함수. Node 에는 DOM 이 없으므로 폴백 경로를 탄다."""
    return _node("renderMarkdown", markdown)


def to_html(markdown: str) -> str:
    """marked 변환만 — 정제 전 단계. Node 테스트용으로만 내보내고 window 에는 싣지 않는다."""
    return _node("toHtml", markdown)


# ── 1. 정제기가 없으면 이스케이프 ───────────────────────────────


@needs_node
def test_정제기를_쓸_수_없으면_원문을_이스케이프해_글자로_돌려준다():
    html = render('<script>alert(1)</script> <img src=x onerror="alert(1)"> [x](javascript:alert(1))')

    assert "<script" not in html and "<img" not in html and "<a" not in html
    assert "&lt;script&gt;" in html


@needs_node
def test_폴백도_줄바꿈은_유지한다():
    assert render("첫 줄\n둘째 줄") == "첫 줄<br>둘째 줄"


def test_정제하지_않은_HTML을_돌려주는_경로가_없다():
    """renderMarkdown 의 return 은 둘뿐이다 — 정제한 것, 아니면 이스케이프한 것."""
    body = SOURCE[SOURCE.index("function renderMarkdown("):]
    body = body[: body.index("\n  }\n") + 1]

    returns = re.findall(r"return ([^;]+);", body)
    assert len(returns) == 2
    assert any(r.startswith("DOMPurify.sanitize(") for r in returns)
    assert any(r.startswith("escapeHtml(") for r in returns)
    # isSupported 가 false 인 DOMPurify 는 입력을 그대로 돌려준다. 반드시 확인하고 써야 한다.
    assert "DOMPurify.isSupported" in SOURCE


def test_허용_목록에_이미지와_스크립트는_없다():
    """허용 목록 방식이다. AI 가 만든 URL 을 자동으로 불러오는 <img> 는 넣지 않는다."""
    tags = re.search(r"ALLOWED_TAGS\s*=\s*\[(.*?)\]", SOURCE, re.DOTALL).group(1)
    allowed = set(re.findall(r'"([a-z0-9]+)"', tags))

    assert {"p", "pre", "code", "table", "a", "strong"} <= allowed
    assert not allowed & {"img", "script", "style", "iframe", "svg", "form", "input", "object", "embed"}
    attrs = re.search(r"ALLOWED_ATTR\s*=\s*\[(.*?)\]", SOURCE, re.DOTALL).group(1)
    assert set(re.findall(r'"([a-z-]+)"', attrs)) == {"href", "align", "class"}


def test_toHtml_은_브라우저_전역에_싣지_않는다():
    """정제 전 HTML 을 돌려주는 함수다. window 에 있으면 누군가 innerHTML 에 바로 넣는다."""
    assert "root.renderMarkdown = " in SOURCE
    assert "root.toHtml" not in SOURCE


# ── 2. marked 설정 ──────────────────────────────────────────────


@needs_node
def test_제목은_페이지_제목보다_커지지_않게_h3_h4_로_낮춘다():
    html = to_html("# 큰 제목\n\n## 왜 생기냐면\n\n### 흔한 경우")

    assert "<h3>큰 제목</h3>" in html and "<h3>왜 생기냐면</h3>" in html
    assert "<h4>흔한 경우</h4>" in html
    assert "<h1" not in html and "<h2" not in html


@needs_node
def test_문단_안의_줄바꿈은_유지되고_빈_줄이_문단을_나눈다():
    html = to_html("안녕!\n코드 오류를 도와줄 수 있어.\n\n편하게 물어봐!")

    assert "<p>안녕!<br>코드 오류를 도와줄 수 있어.</p>" in html
    assert "<p>편하게 물어봐!</p>" in html


@needs_node
def test_펜스_코드_블록은_pre_code_이고_안의_기호는_서식이_아니다():
    html = to_html("```python\n**not bold** and <b>tag</b>\nprint(a[2])  # IndexError\n```")

    assert '<pre><code class="language-python">' in html
    assert "**not bold**" in html and "<strong>" not in html
    assert "&lt;b&gt;tag&lt;/b&gt;" in html
    assert "print(a[2])  # IndexError" in html


@needs_node
def test_굵게_인라인_코드_목록():
    html = to_html("가장 큰 차이는 **수정 가능 여부**예요. `a[0] = 10` 처럼.\n\n- 하나\n- 둘\n\n1. 셋\n2. 넷")

    assert "<strong>수정 가능 여부</strong>" in html and "<code>a[0] = 10</code>" in html
    assert re.search(r"<ul>\s*<li>하나</li>\s*<li>둘</li>\s*</ul>", html)
    assert re.search(r"<ol>\s*<li>셋</li>\s*<li>넷</li>\s*</ol>", html)


@needs_node
def test_표를_그린다():
    """직접 짠 렌더러가 못 하던 것. 비교 질문("리스트 vs 튜플")의 답에 표가 자주 온다."""
    html = to_html("| 구분 | 리스트 | 튜플 |\n|---|:---:|---|\n| 수정 | 가능 | 불가 |")

    assert "<table>" in html and "<th>구분</th>" in html
    assert re.search(r'<td align="center">가능</td>', html)


# ── 3. vendor 파일 ──────────────────────────────────────────────


@pytest.mark.parametrize("name", ["marked.umd.js", "purify.min.js"])
def test_vendor_파일은_기록해_둔_해시_그대로다(name):
    """누가 vendor 파일을 손대면 여기서 드러난다. 버전을 올릴 땐 README 의 해시도 함께 바꾼다."""
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    recorded = re.search(rf"`{re.escape(name)}`.*?`([0-9a-f]{{64}})`", readme, re.DOTALL)

    assert recorded, f"vendor/README.md 에 {name} 의 SHA-256 이 없다"
    assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == recorded.group(1)
