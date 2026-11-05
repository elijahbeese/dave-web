// Cameras page: MJPEG streams just work as plain <img src="..."> tags (no
// JS needed for that part -- the browser keeps the multipart connection
// open on its own). This file handles the two interactive bits: pan/tilt
// buttons on the window cam, and the "ask D.A.V.E. what it sees" box on
// each camera panel.

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

// --- Ask D.A.V.E. what it sees, per panel ---
document.querySelectorAll('.camera-panel').forEach(panel => {
  const camera = panel.dataset.camera;
  const logEl = panel.querySelector('.camera-ask-log');
  const formEl = panel.querySelector('.camera-ask-form');
  const inputEl = panel.querySelector('.camera-ask-input');
  const sendBtn = panel.querySelector('.camera-ask-send');

  async function ask(question) {
    inputEl.value = '';
    inputEl.disabled = true;
    sendBtn.disabled = true;

    const turnEl = document.createElement('div');
    turnEl.className = 'chat-turn';
    turnEl.innerHTML = `<div class="chat-q">${escapeHtml(question || '(describe what you see)')}</div><div class="chat-a chat-pending">D.A.V.E. is looking...</div>`;
    logEl.appendChild(turnEl);
    logEl.scrollTop = logEl.scrollHeight;

    try {
      const resp = await fetch('/api/camera/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera, question }),
      });
      const data = await resp.json();
      const answerEl = turnEl.querySelector('.chat-a');
      answerEl.textContent = resp.ok ? (data.answer || '(no response)') : (data.error || 'Something went wrong.');
      answerEl.classList.remove('chat-pending');
    } catch (err) {
      const answerEl = turnEl.querySelector('.chat-a');
      answerEl.textContent = 'Request failed -- is daveweb still running?';
      answerEl.classList.remove('chat-pending');
    } finally {
      inputEl.disabled = false;
      sendBtn.disabled = false;
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  formEl.addEventListener('submit', e => {
    e.preventDefault();
    ask(inputEl.value.trim());
  });
});

// --- Pan/tilt controls, window cam only ---
const pantiltStatus = document.querySelector('.pantilt-status');
let currentPan = 0;
let currentTilt = 0;

function updateStatus() {
  if (pantiltStatus) pantiltStatus.textContent = `pan ${currentPan}° / tilt ${currentTilt}°`;
}

async function movePanTilt(pan, tilt) {
  try {
    const resp = await fetch('/api/camera/pantilt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pan, tilt }),
    });
    const data = await resp.json();
    if (resp.ok) {
      currentPan = data.pan;
      currentTilt = data.tilt;
      updateStatus();
    }
  } catch (err) {
    if (pantiltStatus) pantiltStatus.textContent = 'pan/tilt request failed';
  }
}

document.querySelectorAll('.pantilt-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.center) {
      movePanTilt(0, 0);
      return;
    }
    const dPan = parseInt(btn.dataset.pan || '0', 10);
    const dTilt = parseInt(btn.dataset.tilt || '0', 10);
    movePanTilt(currentPan + dPan, currentTilt + dTilt);
  });
});

// Pull the real current position on page load, rather than assuming 0/0
// (the HAT may already be pointed somewhere from a previous session).
if (document.querySelector('.pantilt-controls')) {
  fetch('/api/camera/pantilt')
    .then(r => r.json())
    .then(data => {
      if (typeof data.pan === 'number') {
        currentPan = data.pan;
        currentTilt = data.tilt;
        updateStatus();
      }
    })
    .catch(() => {});
}

// --- Recent clips, per panel ---
document.querySelectorAll('.camera-panel').forEach(panel => {
  const camera = panel.dataset.camera;
  const clipsListEl = panel.querySelector('.camera-clips-list');
  const playerEl = panel.querySelector('.camera-clip-player');
  if (!clipsListEl) return;

  function formatSize(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function formatTime(filename) {
    return filename.replace('.mp4', '').replace(/-/g, ':');
  }

  async function loadClips() {
    try {
      const resp = await fetch(`/api/camera/clips/${camera}`);
      const data = await resp.json();
      if (!resp.ok) {
        clipsListEl.textContent = data.error || 'Failed to load clips.';
        return;
      }
      if (!data.clips.length) {
        clipsListEl.textContent = 'No motion clips yet.';
        return;
      }
      clipsListEl.innerHTML = '';
      data.clips.slice(0, 20).forEach(clip => {
        const row = document.createElement('div');
        row.className = 'camera-clip-row';
        const label = document.createElement('span');
        label.textContent = `${clip.date} ${formatTime(clip.filename)} -- ${formatSize(clip.size_bytes)}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chip-btn';
        btn.textContent = '\u25b6 play';
        btn.addEventListener('click', () => {
          playerEl.src = `/api/camera/clips/${camera}/${clip.date}/${clip.filename}`;
          playerEl.style.display = 'block';
          playerEl.play();
        });
        row.appendChild(label);
        row.appendChild(btn);
        clipsListEl.appendChild(row);
      });
    } catch (err) {
      clipsListEl.textContent = 'Request failed.';
    }
  }

  loadClips();
  setInterval(loadClips, 30000);
});
