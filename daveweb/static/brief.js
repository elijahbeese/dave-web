// Shared handler for any ".brief-panel" on the page -- the dashboard's
// asset-relevant threat brief, the news page's industry-wide brief, and the
// pulse page's brief all use this same markup/behavior (Listen via the
// browser's built-in text-to-speech, Generate-on-demand fallback, and a
// chat box for asking follow-up questions about that specific brief),
// scoped by data-kind so each panel hits the right API calls.
document.querySelectorAll('.brief-panel').forEach(panel => {
  const kind = panel.dataset.kind || 'threat';
  const listenBtn = panel.querySelector('.brief-listen-btn');
  const stopBtn = panel.querySelector('.brief-stop-btn');
  const textEl = panel.querySelector('.brief-text');
  const genBtn = panel.querySelector('.brief-generate-btn');

  if (listenBtn && stopBtn && textEl) {
    listenBtn.addEventListener('click', () => {
      const text = textEl.textContent || '';
      if (!text || !window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = 1.0;
      utter.onend = () => { listenBtn.classList.remove('hidden'); stopBtn.classList.add('hidden'); };
      window.speechSynthesis.speak(utter);
      listenBtn.classList.add('hidden');
      stopBtn.classList.remove('hidden');
    });
    stopBtn.addEventListener('click', () => {
      window.speechSynthesis.cancel();
      stopBtn.classList.add('hidden');
      listenBtn.classList.remove('hidden');
    });
  }

  // Skip panels with no .brief-text at all (e.g. pulse.html's plain
  // "Pull now" panel isn't a brief and has nothing to chat about).
  if (!textEl) return;

  // --- Brief-chat: suggested-question chips + free-text follow-up box ---
  // Built here in JS, not duplicated per-template, so threat/news/pulse all
  // get it for free from one implementation.
  const chatWrap = document.createElement('div');
  chatWrap.className = 'brief-chat hidden';
  chatWrap.innerHTML = `
    <div class="brief-chat-chips"></div>
    <div class="brief-chat-log"></div>
    <form class="brief-chat-form">
      <input type="text" class="brief-chat-input" placeholder="Ask about something in this brief..." autocomplete="off" />
      <button type="submit" class="chip-btn brief-chat-send">Ask</button>
    </form>
  `;
  panel.appendChild(chatWrap);

  const chipsEl = chatWrap.querySelector('.brief-chat-chips');
  const logEl = chatWrap.querySelector('.brief-chat-log');
  const formEl = chatWrap.querySelector('.brief-chat-form');
  const inputEl = chatWrap.querySelector('.brief-chat-input');
  const sendBtn = chatWrap.querySelector('.brief-chat-send');

  // Capped so a long back-and-forth doesn't make every follow-up prompt
  // balloon forever -- the brief text itself is re-sent every turn anyway,
  // so this is just the running Q/A on top of it.
  const history = [];
  const HISTORY_CAP = 6;

  function renderChips(suggestions) {
    chipsEl.innerHTML = '';
    (suggestions || []).forEach(q => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip-btn chat-chip';
      chip.textContent = q;
      chip.addEventListener('click', () => askQuestion(q));
      chipsEl.appendChild(chip);
    });
  }

  function showChat() {
    chatWrap.classList.remove('hidden');
  }

  async function askQuestion(question) {
    if (!question || !question.trim()) return;
    inputEl.value = '';
    inputEl.disabled = true;
    sendBtn.disabled = true;

    const turnEl = document.createElement('div');
    turnEl.className = 'chat-turn';
    turnEl.innerHTML = `<div class="chat-q">${escapeHtml(question)}</div><div class="chat-a chat-pending">D.A.V.E. is thinking...</div>`;
    logEl.appendChild(turnEl);
    logEl.scrollTop = logEl.scrollHeight;

    try {
      const resp = await fetch('/api/brief-chat/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, question, history }),
      });
      const data = await resp.json();
      const answerEl = turnEl.querySelector('.chat-a');
      if (!resp.ok) {
        answerEl.textContent = data.error || 'Something went wrong asking that.';
      } else {
        answerEl.textContent = data.answer || '(no response)';
        history.push({ question, answer: data.answer || '' });
        if (history.length > HISTORY_CAP) history.shift();
      }
      answerEl.classList.remove('chat-pending');
    } catch (err) {
      const answerEl = turnEl.querySelector('.chat-a');
      answerEl.textContent = 'Request failed -- is daveweb still running?';
      answerEl.classList.remove('chat-pending');
    } finally {
      inputEl.disabled = false;
      sendBtn.disabled = false;
      inputEl.focus();
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  formEl.addEventListener('submit', e => {
    e.preventDefault();
    askQuestion(inputEl.value);
  });

  // If a brief is already on the page (server-rendered, cached from
  // earlier today), its suggestions ride along as a data attribute --
  // show the chat box immediately rather than waiting for a Generate click.
  let initialSuggestions = [];
  try { initialSuggestions = JSON.parse(panel.dataset.suggestions || '[]'); } catch (err) { /* ignore */ }
  if (!textEl.classList.contains('empty-note')) {
    renderChips(initialSuggestions);
    showChat();
  }

  if (genBtn) {
    genBtn.addEventListener('click', async () => {
      genBtn.disabled = true;
      genBtn.textContent = 'Asking D.A.V.E. (this can take a minute)...';
      try {
        const resp = await fetch(`/api/daily-brief/generate?kind=${encodeURIComponent(kind)}`, { method: 'POST' });
        const data = await resp.json();
        if (textEl) {
          textEl.textContent = data.brief || '(no response)';
          textEl.classList.remove('empty-note');
        }
        genBtn.remove();
        if (listenBtn) listenBtn.disabled = false;
        renderChips(data.suggestions);
        showChat();
      } catch (err) {
        genBtn.disabled = false;
        genBtn.textContent = "Generate today's brief";
      }
    });
  }
});
