"""AI 답변 마크다운 렌더러(static/markdown.js) — Node 로 직접 실행해 검증한다.

브라우저 코드지만 순수 함수라 Node 에서도 돈다. 모든 텍스트를 먼저 이스케이프하고
우리가 만든 태그만 내보내는 것이 안전의 핵심이다. AI 출력은 신뢰할 수 없는 입력이다.  → #142
"""

import json
import shutil
import subprocess

import pytest

from app.config import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 없음 — CI 러너와 로컬엔 있다")


def render(markdown: str) -> str:
    script = (
        f"const m = require({json.dumps(str(STATIC_DIR / 'markdown.js'))});"
        f"process.stdout.write(m.renderMarkdown({json.dumps(markdown)}));"
    )
    return subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout


def test_HTML_태그는_실행되지_않고_글자로_보인다():
    html = render('<script>alert(1)</script> <img src=x onerror="alert(1)">')

    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html


def test_굵게와_인라인_코드():
    html = render("가장 큰 차이는 **수정 가능 여부**예요. `a[0] = 10` 처럼.")

    assert "<strong>수정 가능 여부</strong>" in html
    assert "<code>a[0] = 10</code>" in html
    assert "**" not in html and "`" not in html


def test_펜스_코드_블록은_pre_code_로_원문_그대로():
    html = render("```python\na = [1, 2]\nprint(a[2])  # IndexError\n```")

    assert html.startswith("<pre><code>")
    assert "print(a[2])  # IndexError" in html
    assert "```" not in html and "python" not in html


def test_코드_블록_안의_서식_기호는_서식이_아니다():
    html = render("```\n**not bold** and <b>tag</b>\n```")

    assert "**not bold**" in html and "<strong>" not in html
    assert "&lt;b&gt;tag&lt;/b&gt;" in html


def test_제목과_목록():
    html = render("## 왜 생기냐면\n- 하나\n- 둘\n\n### 흔한 경우\n1. 셋\n2. 넷")

    assert "<h3>왜 생기냐면</h3>" in html
    assert "<ul><li>하나</li><li>둘</li></ul>" in html
    assert "<h4>흔한 경우</h4>" in html
    assert "<ol><li>셋</li><li>넷</li></ol>" in html


def test_문단_안의_줄바꿈은_유지되고_빈_줄이_문단을_나눈다():
    html = render("안녕!\n코드 오류를 도와줄 수 있어.\n\n편하게 물어봐!")

    assert "<p>안녕!<br>코드 오류를 도와줄 수 있어.</p>" in html
    assert "<p>편하게 물어봐!</p>" in html
