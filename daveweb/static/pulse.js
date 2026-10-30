// Manual pull trigger for /pulse -- deliberately the ONLY way pulse's
// collectors run (no systemd timer). Each click is a real round of
// metered X API calls, so this never fires on page load, only on click.
const btn = document.getElementById('pulse-refresh-btn');
const status = document.getElementById('pulse-refresh-status');

if (btn) {
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Pulling (this can take up to ~30s)...';
    status.textContent = '';
    try {
      const resp = await fetch('/api/pulse/refresh', { method: 'POST' });
      const data = await resp.json();
      const parts = Object.entries(data.results || {}).map(([k, v]) => `${k}: ${v}`);
      status.textContent = `Done at ${new Date(data.ran_at).toLocaleTimeString()} — ${parts.join(', ')}. Reload to see new posts.`;
      btn.textContent = 'Pull now';
      btn.disabled = false;
    } catch (err) {
      status.textContent = `Pull failed: ${err}`;
      btn.textContent = 'Pull now';
      btn.disabled = false;
    }
  });
}
