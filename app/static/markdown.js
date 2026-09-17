/* AI 답변의 마크다운을 화면에 그린다 — marked 로 바꾸고 DOMPurify 로 정제한다 (#172).
 *
 * AI 출력은 신뢰할 수 없는 입력이다. marked 는 원문 HTML(<script> · onerror …)을 그대로 통과시키므로
 * DOMPurify 는 선택이 아니라 필수다. 그래서 돌려주는 값은 둘뿐이다 —
 *   정제한 HTML, 또는 (정제기를 쓸 수 없으면) 이스케이프한 원문.
 * 정제하지 않은 HTML 이 나가는 경로는 없다.
 *
 * 처음(#142)에는 부분집합 렌더러를 직접 짰다. 인증에서 세운 원칙 — 직접 구현하지 않고 검증된 라이브러리에
 * 위임한다(#65) — 과 어긋나고, 표·링크·중첩 목록도 그리지 못해 바꿨다.
 *
 * 브라우저(window.renderMarkdown)와 Node(module.exports) 둘 다에서 돈다 — pytest 가 Node 로 검증한다.
 * Node 에는 DOM 이 없어 DOMPurify 가 돌지 않으므로, Node 에서의 renderMarkdown 은 곧 폴백 경로다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./vendor/marked.umd.js"), null, true);
  } else {
    root.renderMarkdown = factory(root.marked, root.DOMPurify, false).renderMarkdown;
  }
})(this, function (markedLib, DOMPurify, forNode) {
  "use strict";

  // 허용 목록 방식. 마크다운이 만들 수 있는 태그만 연다.
  // <img> 는 넣지 않았다 — AI 가 만든 URL 을 브라우저가 자동으로 불러오게 하지 않는다.
  var ALLOWED_TAGS = [
    "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
    "h3", "h4", "ul", "ol", "li", "a",
    "table", "thead", "tbody", "tr", "th", "td"
  ];
  // class 는 코드 블록의 language-xxx 하나를 위해서다. DOMPurify 는 속성 값의 스크립트도 걸러 낸다.
  var ALLOWED_ATTR = ["href", "align", "class"];
  // 링크는 웹 주소와 메일만. javascript: · data: 는 여기서 떨어진다.
  var ALLOWED_URI = /^(?:https?:|mailto:)/i;

  var MARKED_OPTIONS = {
    gfm: true,
    // 답변은 문단 안에서도 줄을 바꿔 쓴다. 줄바꿈을 <br> 로 살린다.
    breaks: true,
    // 답변 속 제목이 페이지 제목보다 커지지 않게 낮춘다: # · ## → h3, 그 아래 → h4.
    walkTokens: function (token) {
      if (token.type === "heading") token.depth = token.depth <= 2 ? 3 : 4;
    }
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
      .replace(/\r?\n/g, "<br>");
  }

  // marked 변환만 — 아직 정제하지 않은 HTML 이다. 이 값을 그대로 innerHTML 에 넣으면 안 된다.
  function toHtml(text) {
    return markedLib.marked.parse(String(text == null ? "" : text), MARKED_OPTIONS);
  }

  // isSupported 가 false 인 DOMPurify 는 입력을 그대로 돌려준다. 반드시 확인하고 쓴다.
  var canSanitize = !!(markedLib && markedLib.marked && DOMPurify &&
                       DOMPurify.isSupported && typeof DOMPurify.sanitize === "function");

  if (canSanitize) {
    // 링크는 새 탭으로, 이 페이지를 조작하지 못하게(noopener), 출처를 넘기지 않게(noreferrer).
    DOMPurify.addHook("afterSanitizeAttributes", function (node) {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer nofollow");
      }
    });
    // class 는 marked 가 코드 블록에 붙이는 language-xxx 만 남긴다. 원문 HTML 이 우리 CSS 클래스를
    // 빌려 화면을 흉내 내지 못하게 한다.
    DOMPurify.addHook("uponSanitizeAttribute", function (node, data) {
      if (data.attrName === "class" && !/^language-[\w+-]+$/.test(data.attrValue)) data.keepAttr = false;
      // align 은 아래에서 URI 검사를 면제받는다. 그 대신 값은 정렬 셋 중 하나만 남긴다.
      if (data.attrName === "align" && !/^(?:left|center|right)$/i.test(data.attrValue)) data.keepAttr = false;
    });
  }

  function renderMarkdown(text) {
    if (canSanitize) {
      return DOMPurify.sanitize(toHtml(text), {
        ALLOWED_TAGS: ALLOWED_TAGS,
        ALLOWED_ATTR: ALLOWED_ATTR,
        ALLOWED_URI_REGEXP: ALLOWED_URI,
        // DOMPurify 는 허용된 속성의 값도 위 URI 규칙으로 검사한다. 표 정렬(align="center")은 주소가 아니므로
        // 검사에서 뺀다 — 링크 규칙을 느슨하게 푸는 대신 이 속성 하나만 예외로 둔다.
        ADD_URI_SAFE_ATTR: ["align"]
      });
    }
    // 라이브러리를 못 불러왔거나 DOM 이 없다. 서식은 포기하고 원문을 글자로 보여 준다.
    return escapeHtml(text == null ? "" : text);
  }

  // toHtml 은 Node 테스트에만 내보낸다. 브라우저 전역에 있으면 누군가 innerHTML 에 바로 넣는다.
  return forNode ? { renderMarkdown: renderMarkdown, toHtml: toHtml } : { renderMarkdown: renderMarkdown };
});
