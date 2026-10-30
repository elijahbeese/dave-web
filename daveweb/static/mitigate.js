// Shared "Generate D.A.V.E. mitigation guidance" button handler --
// used on both the dashboard's finding cards and the per-CVE detail page.
// Pulled into its own file instead of duplicated inline once a second page
// needed it.
document.querySelectorAll('.mitigate-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const box = btn.closest('.mitigation');
    const resultEl = box.querySelector('.mitigation-result');
    const cve = box.dataset.cve;

    btn.disabled = true;
    btn.textContent = 'Asking D.A.V.E. (this can take a minute)...';
    resultEl.classList.remove('hidden');
    resultEl.textContent = '';

    try {
      const resp = await fetch(`/api/mitigate/${encodeURIComponent(cve)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          description: box.dataset.description,
          matched_asset: box.dataset.matchedAsset,
          cvss_score: box.dataset.cvssScore ? parseFloat(box.dataset.cvssScore) : null,
          required_action: box.dataset.requiredAction,
        }),
      });
      const data = await resp.json();
      btn.remove();
      const label = document.createElement('div');
      label.className = 'mitigation-label';
      label.textContent = 'D.A.V.E.-WRITTEN GUIDANCE (AI-GENERATED)';
      resultEl.classList.add('mitigation-text');
      resultEl.before(label);
      resultEl.textContent = data.guidance || data.error || '(no response)';
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Generate D.A.V.E. mitigation guidance';
      resultEl.textContent = 'Request failed: ' + err;
    }
  });
});
