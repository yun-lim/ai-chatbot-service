/* 채팅 화면 — 질문 전송과 응답 렌더링.
   서버 계약은 app/schemas.py 를 따른다.
     성공  200 {answer, chat_id}
     실패  4xx/503 {error_code, message}
*/
(function () {
  "use strict";

  var form = document.getElementById("composer");
  var input = document.getElementById("message");
  var send = document.getElementById("send");
  var counter = document.getElementById("counter");
  var notice = document.getElementById("notice");
  var log = document.getElementById("log");
  var empty = document.getElementById("empty");

  // 서버가 error_code 를 주지 못한 경우에만 쓰는 최후 문구.
  // 정상 경로에서는 서버가 보낸 message 를 그대로 보여준다.
  var FALLBACK = "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.";

  // 입력창은 고정(position: fixed)이고 높이가 변한다 — 입력칸이 자란다. 실제 높이를 CSS 변수로 넘겨
  // 본문의 아래 여백과 자동 스크롤의 기준점(scroll-padding-bottom)이 따르게 한다 (#176).
  function syncComposerHeight() {
    // 입력창이 커지는 순간 여백은 늘지만 스크롤 위치는 그대로라, 보고 있던 마지막 답변이 덮인다.
    // 맨 아래를 보고 있었다면(여유 24px) 커진 뒤에도 맨 아래를 유지한다.
    var doc = document.documentElement;
    var wasAtBottom = window.innerHeight + window.scrollY >= doc.scrollHeight - 24;
    doc.style.setProperty("--composer-h", form.getBoundingClientRect().height + "px");
    if (wasAtBottom) window.scrollTo(0, doc.scrollHeight);
  }
  syncComposerHeight();
  if ("ResizeObserver" in window) new ResizeObserver(syncComposerHeight).observe(form);

  function fmtTime(d) {
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0");
  }

  // 길이 상한은 없다. 코드를 붙여넣을 때 크기를 가늠하라고 글자 수만 보여준다.
  function updateCounter() {
    var n = input.value.length;
    counter.textContent = n ? n.toLocaleString() + "자" : "";
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = input.scrollHeight + "px";
  }

  function addEntry(question) {
    if (empty) empty.remove();

    var entry = document.createElement("article");
    entry.className = "entry is-pending";

    var meta = document.createElement("div");
    meta.className = "entry-meta";
    var time = document.createElement("b");
    time.textContent = fmtTime(new Date());
    meta.appendChild(time);

    var body = document.createElement("div");
    // 클래스 이름과 구조는 templates/_entry.html 과 같아야 두 화면이 같은 CSS 를 탄다.
    var ask = document.createElement("p");
    ask.className = "ask bubble is-user";
    ask.textContent = question;
    var answer = document.createElement("p");
    answer.className = "answer bubble is-bot";
    answer.textContent = "답변을 만들고 있습니다…";
    // 시각은 답변 말풍선의 오른쪽 아래에 붙인다 (#169).
    var row = document.createElement("div");
    row.className = "answer-row";
    row.appendChild(answer);
    row.appendChild(meta);
    body.appendChild(ask);
    body.appendChild(row);

    entry.appendChild(body);
    log.appendChild(entry);
    entry.scrollIntoView({ block: "end" });

    return { entry: entry, meta: meta, answer: answer };
  }

  function settle(slot, text, isError) {
    slot.entry.classList.remove("is-pending");
    if (isError) {
      // 오류 문구는 서버가 정한 평문이다. 서식으로 해석하지 않는다.
      slot.answer.textContent = text;
    } else {
      // 답변은 마크다운으로 온다. markdown.js 가 전부 이스케이프한 뒤 자기 태그만 만든다 (#142).
      slot.answer.innerHTML = renderMarkdown(text);
      slot.answer.classList.add("md");
    }
    slot.answer.classList.toggle("is-error", isError);
    slot.entry.scrollIntoView({ block: "end" });
  }

  input.addEventListener("input", function () {
    updateCounter();
    autoGrow();
  });

  // 서버가 미리 그려 둔 지난 대화 (#145): 답변은 원문이라 같은 렌더러로 그리고, 마지막 항목이 보이게 한다.
  document.querySelectorAll(".answer.is-bot:not(.is-error)").forEach(function (el) {
    el.innerHTML = renderMarkdown(el.textContent);
    el.classList.add("md");
  });
  var last = log.querySelector(".entry:last-child");
  if (last) last.scrollIntoView({ block: "end" });

  // ── 더 오래된 대화 불러오기 (#148) ──────────────────────────────
  // 서버는 최근 HISTORY_LIMIT 건만 그린다. 맨 위 감시 요소가 보이면 다음 묶음을 C 의 조회 API 로 받아
  // 감시 요소 바로 아래에 끼운다. 첫 화면이 한도보다 적었으면 더 없는 것이다.
  var historyTop = document.getElementById("history-top");
  // 저장된 오류 항목은 answer 가 비어 있다. 서버가 내려 준 문구 사전(schemas.ERROR_MESSAGES)으로 채운다 (#171).
  var ERROR_MESSAGES = {};
  try { ERROR_MESSAGES = JSON.parse(log.getAttribute("data-error-messages") || "{}"); } catch (e) {}
  var HISTORY_LIMIT = parseInt(log.getAttribute("data-history-limit"), 10) || 50;
  var historyLoaded = parseInt(log.getAttribute("data-history-loaded"), 10) || 0;
  var historyDone = historyLoaded < HISTORY_LIMIT;
  var historyBusy = false;
  // 이번 접속에서 서버에 저장된 질문 수. 조회 API 는 최신순이라 새 행이 쌓이면 이미 그린 항목이
  // 그만큼 뒤로 밀린다 — offset 에 더하지 않으면 그 수만큼 중복으로 끼워진다 (#161).
  var savedThisSession = 0;

  // API 의 created_at 은 UTC. 서버 렌더링(kst 필터)과 같은 "MM/DD HH:MM" 로 맞춘다.
  function fmtKst(iso) {
    var d = new Date(new Date(iso).getTime() + 9 * 60 * 60 * 1000);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return p(d.getUTCMonth() + 1) + "/" + p(d.getUTCDate()) + " " +
           p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
  }

  // templates/_entry.html 과 같은 마크업. 서버가 그린 항목과 같은 CSS 를 탄다.
  function buildEntry(item) {
    var entry = document.createElement("article");
    entry.className = "entry";

    var meta = document.createElement("div");
    meta.className = "entry-meta";
    var time = document.createElement("b");
    time.textContent = fmtKst(item.created_at);
    meta.appendChild(time);

    var body = document.createElement("div");
    var ask = document.createElement("p");
    ask.className = "ask bubble is-user";
    ask.textContent = item.question;
    var answer = document.createElement("p");
    answer.className = "answer bubble is-bot";
    if (item.status === "error") {
      answer.classList.add("is-error");
      answer.textContent = item.answer || ERROR_MESSAGES[item.error_code] || FALLBACK;
    } else {
      answer.innerHTML = renderMarkdown(item.answer);
      answer.classList.add("md");
    }
    var row = document.createElement("div");
    row.className = "answer-row";
    row.appendChild(answer);
    row.appendChild(meta);
    body.appendChild(ask);
    body.appendChild(row);
    if (item.status === "error") {
      var badge = document.createElement("span");
      badge.className = "badge is-error";
      badge.textContent = item.error_code;
      body.appendChild(badge);
    }

    entry.appendChild(body);
    return entry;
  }

  function loadOlder() {
    if (historyDone || historyBusy) return;
    historyBusy = true;
    historyTop.textContent = "지난 대화를 불러오는 중…";

    fetch("/api/me/chats?limit=" + HISTORY_LIMIT + "&offset=" + (historyLoaded + savedThisSession))
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (items) {
        var before = document.documentElement.scrollHeight;
        // API 는 최신순이다. 하나씩 감시 요소 바로 아래에 끼우면 결과적으로 오래된 것이 위로 간다.
        items.forEach(function (item) {
          historyTop.insertAdjacentElement("afterend", buildEntry(item));
        });
        historyLoaded += items.length;
        historyDone = items.length < HISTORY_LIMIT;
        // 위에 끼운 만큼 내려서 보던 자리를 유지한다 (.log 는 overflow-anchor: none).
        window.scrollBy(0, document.documentElement.scrollHeight - before);
        historyTop.textContent = "";
      })
      .catch(function () {
        // 실패는 실패로 보인다. 감시 요소가 다시 보이면 다시 시도한다.
        historyTop.textContent = "지난 대화를 불러오지 못했어요. 위로 다시 스크롤하면 재시도합니다.";
      })
      .finally(function () {
        historyBusy = false;
      });
  }

  if (!historyDone && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) loadOlder();
    }, { rootMargin: "200px 0px 0px 0px" }).observe(historyTop);
  }

  // Enter 로 보내고 Shift+Enter 로 줄바꿈. 코드를 붙여넣는 서비스라 줄바꿈이 잦다.
  input.addEventListener("keydown", function (e) {
    // 한글 IME 가 마지막 글자를 조합하는 중의 Enter 는 조합 확정이지 전송이 아니다 (#139).
    // 이때 보내면 조합 중이던 글자가 빈 입력창에 다시 들어가 한 글자짜리 질문이 한 번 더 나간다.
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    // 버튼만 비활성화하면 Enter(requestSubmit)로는 여전히 보낼 수 있다. 응답을 기다리는 동안은 여기서 막는다.
    if (send.disabled) return;
    notice.textContent = "";

    var question = input.value;
    if (!question.trim()) {
      notice.textContent = "질문을 입력해 주세요.";
      return;
    }

    var slot = addEntry(question);
    input.value = "";
    updateCounter();
    autoGrow();
    send.disabled = true;

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question })
    })
      .then(function (res) {
        return res.json()
          .catch(function () { return {}; })
          .then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (r) {
        // 서버는 성공(200)과 AI 실패(503)를 chat_logs 에 남긴다. 422·401 은 남기지 않는다.
        if (r.ok || r.status === 503) savedThisSession += 1;
        if (r.ok) {
          settle(slot, r.data.answer, false);
          return;
        }
        if (r.status === 401) {
          window.location.href = "/login";
          return;
        }
        settle(slot, r.data.message || FALLBACK, true);
      })
      .catch(function () {
        // 네트워크 자체가 끊긴 경우. 서버는 살아있을 수도 있으므로 단정하지 않는다.
        settle(slot, "연결하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.", true);
      })
      .finally(function () {
        send.disabled = false;
        input.focus();
      });
  });

  updateCounter();
  input.focus();
})();
