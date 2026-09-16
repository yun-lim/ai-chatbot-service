/* AI 답변 마크다운 렌더러 — 필요한 부분집합만.  → #142
 *
 *   펜스 코드 블록(```), 제목(#~######), 굵게(**), 인라인 코드(`), 목록(- * 1.), 문단(줄바꿈 유지)
 *
 * 안전: 모든 텍스트를 먼저 HTML 이스케이프하고 여기서 만든 태그만 내보낸다.
 * AI 출력은 신뢰할 수 없는 입력이라 원문 HTML 은 절대 그대로 넣지 않는다.
 * 브라우저(window.renderMarkdown)와 Node(module.exports) 둘 다에서 돈다 — pytest 가 Node 로 검증한다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.renderMarkdown = factory().renderMarkdown;
  }
})(this, function () {
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // 이미 이스케이프된 한 줄에 인라인 서식을 입힌다. 인라인 코드 안은 서식이 아니다.
  function inline(escaped) {
    var parts = escaped.split(/(`[^`\n]+`)/);
    return parts
      .map(function (part, i) {
        if (i % 2 === 1) return "<code>" + part.slice(1, -1) + "</code>";
        return part.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
      })
      .join("");
  }

  function renderMarkdown(text) {
    var lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
    var out = [];
    var para = [];
    var list = null;

    function flushPara() {
      if (para.length) {
        out.push("<p>" + para.map(inline).join("<br>") + "</p>");
        para = [];
      }
    }
    function flushList() {
      if (list) {
        out.push(
          "<" + list.tag + ">" +
            list.items.map(function (it) { return "<li>" + inline(it) + "</li>"; }).join("") +
            "</" + list.tag + ">"
        );
        list = null;
      }
    }

    var i = 0;
    while (i < lines.length) {
      var raw = lines[i];

      if (/^\s*```/.test(raw)) {
        flushPara();
        flushList();
        var code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) {
          code.push(lines[i]);
          i++;
        }
        i++; // 닫는 펜스 (없으면 EOF)
        out.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
        continue;
      }

      var line = escapeHtml(raw);

      var heading = line.match(/^(#{1,6})\s+(.*\S)\s*$/);
      if (heading) {
        flushPara();
        flushList();
        var tag = heading[1].length <= 2 ? "h3" : "h4";
        out.push("<" + tag + ">" + inline(heading[2]) + "</" + tag + ">");
        i++;
        continue;
      }

      var ul = line.match(/^\s*[-*]\s+(.*)$/);
      var ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        var listTag = ul ? "ul" : "ol";
        if (!list || list.tag !== listTag) {
          flushList();
          list = { tag: listTag, items: [] };
        }
        list.items.push((ul || ol)[1]);
        i++;
        continue;
      }

      if (line.trim() === "") {
        flushPara();
        flushList();
        i++;
        continue;
      }

      flushList();
      para.push(line);
      i++;
    }
    flushPara();
    flushList();
    return out.join("");
  }

  return { renderMarkdown: renderMarkdown };
});
