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
    // 클래스 이름은 templates/logs.html 과 같아야 두 화면이 같은 CSS 를 탄다.
    var ask = document.createElement("p");
    ask.className = "ask bubble is-user";
    ask.textContent = question;
    var answer = document.createElement("p");
    answer.className = "answer bubble is-bot";
    answer.textContent = "답변을 만들고 있습니다…";
    body.appendChild(ask);
    body.appendChild(answer);

    entry.appendChild(meta);
    entry.appendChild(body);
    log.appendChild(entry);
    entry.scrollIntoView({ block: "end" });

    return { entry: entry, meta: meta, answer: answer };
  }

  function settle(slot, text, isError, ms) {
    slot.entry.classList.remove("is-pending");
    slot.answer.textContent = text;
    slot.answer.classList.toggle("is-error", isError);
    if (typeof ms === "number") {
      slot.meta.appendChild(document.createTextNode(ms + "ms"));
    }
    slot.entry.scrollIntoView({ block: "end" });
  }

  input.addEventListener("input", function () {
    updateCounter();
    autoGrow();
  });

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

    var started = performance.now();

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
        var ms = Math.round(performance.now() - started);

        if (r.ok) {
          settle(slot, r.data.answer, false, ms);
          return;
        }
        if (r.status === 401) {
          window.location.href = "/login";
          return;
        }
        settle(slot, r.data.message || FALLBACK, true, ms);
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
