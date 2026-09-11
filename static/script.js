const chatWindow = document.getElementById("chat-window");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const suggestions = document.getElementById("suggestions");

const CONFIDENCE_LABELS = {
  high: "Sikker",
  medium: "Middels sikker",
  low: "Usikker",
};

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function scrollToBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "message user";
  el.innerHTML = `
    <div class="avatar user-avatar">Du</div>
    <div class="bubble-wrap">
      <div class="bubble">${escapeHtml(text)}</div>
    </div>
  `;
  chatWindow.appendChild(el);
  scrollToBottom();
}

function addTypingIndicator() {
  const el = document.createElement("div");
  el.className = "message bot";
  el.id = "typing-msg";
  el.innerHTML = `
    <div class="avatar bot-avatar">AI</div>
    <div class="bubble-wrap">
      <div class="bubble">
        <div class="typing-indicator"><span></span><span></span><span></span></div>
      </div>
    </div>
  `;
  chatWindow.appendChild(el);
  scrollToBottom();
}

function removeTypingIndicator() {
  const el = document.getElementById("typing-msg");
  if (el) el.remove();
}

function addBotMessage(data) {
  const el = document.createElement("div");
  el.className = "message bot";

  const label = CONFIDENCE_LABELS[data.confidence_label] || "Ukjent";
  const pct = data.confidence_pct ?? 0;

  let uncertainBannerHtml = "";
  if (data.is_uncertain) {
    uncertainBannerHtml = `
      <div class="uncertain-banner">
        ⚠️ Usikker – sjekk med HR. Jeg fant ikke god nok kontekst i
        kunnskapsbasen til å svare sikkert på dette. Spørsmålet er logget
        slik at kunnskapsbasen kan forbedres.
      </div>
    `;
  }

  let sourcesHtml = "";
  if (data.sources && data.sources.length > 0) {
    const items = data.sources
      .map(
        (s) => `
        <div class="source-item">
          <div class="source-title">${escapeHtml(s.title)} — ${escapeHtml(s.heading)}
            <span class="source-score">(relevans ${s.relevance_pct}%)</span>
          </div>
        </div>`
      )
      .join("");
    sourcesHtml = `
      <details class="sources">
        <summary>Kilder brukt i svaret (${data.sources.length})</summary>
        <div class="sources-content">${items}</div>
      </details>
    `;
  }

  el.innerHTML = `
    <div class="avatar bot-avatar">AI</div>
    <div class="bubble-wrap">
      <div class="bubble">${escapeHtml(data.answer)}</div>
      <div class="meta-row">
        <span class="confidence-badge ${data.confidence_label}">
          ${label} · ${pct}% confidence
        </span>
      </div>
      ${uncertainBannerHtml}
      ${sourcesHtml}
    </div>
  `;
  chatWindow.appendChild(el);
  scrollToBottom();
}

function addErrorMessage(text) {
  const el = document.createElement("div");
  el.className = "message bot";
  el.innerHTML = `
    <div class="avatar bot-avatar">AI</div>
    <div class="bubble-wrap">
      <div class="bubble">Beklager, noe gikk galt: ${escapeHtml(text)}</div>
    </div>
  `;
  chatWindow.appendChild(el);
  scrollToBottom();
}

async function sendMessage(text) {
  addUserMessage(text);
  chatInput.value = "";
  sendBtn.disabled = true;
  addTypingIndicator();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    const data = await res.json();
    removeTypingIndicator();

    if (!res.ok) {
      addErrorMessage(data.error || "Ukjent feil.");
      return;
    }

    addBotMessage(data);
  } catch (err) {
    removeTypingIndicator();
    addErrorMessage("Klarte ikke å kontakte serveren. Prøv igjen.");
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  sendMessage(text);
});

suggestions.addEventListener("click", (e) => {
  if (e.target.tagName === "LI") {
    sendMessage(e.target.textContent);
  }
});
