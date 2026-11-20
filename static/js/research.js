document.addEventListener('DOMContentLoaded', async () => {
  const selectModel = document.getElementById('select-corpus-model');
  const selectStyle = document.getElementById('select-corpus-style');
  const btnInspect = document.getElementById('btn-inspect-corpus');
  const previewBox = document.getElementById('corpus-preview-box');
  const metaRow = document.getElementById('corpus-meta-row');
  const styleTextEl = document.getElementById('corpus-style-text');
  const graphJsonEl = document.getElementById('corpus-graph-json');
  const benchmarksGrid = document.getElementById('benchmarks-grid');
  const catalogTbody = document.getElementById('catalog-tbody');
  const filterInput = document.getElementById('filter-catalog');
  const btnVerify = document.getElementById('btn-verify-standards');
  const verifyBanner = document.getElementById('standards-verify-banner');

  let allReqs = [];

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  try {
    const manifest = await API.getCorpusManifest();
    selectModel.innerHTML = manifest.map(m => `<option value="${esc(m.id)}">[${esc(m.archetype)}] ${esc(m.title)}</option>`).join('');
  } catch (e) {
    console.warn('Corpus manifest error:', e);
  }

  btnInspect.addEventListener('click', async () => {
    const id = selectModel.value;
    if (!id) return;
    const detail = await API.getCorpusModel(id);
    const style = selectStyle.value;
    previewBox.classList.remove('hidden');
    metaRow.innerHTML = `<span class="badge-soft">${esc(detail.archetype)}</span> <strong>${esc(detail.title)}</strong> &bull; ${esc(detail.reference)} &bull; Nodes: ${detail.graph.nodes.length}, Edges: ${detail.graph.edges.length}`;
    styleTextEl.textContent = detail.text_styles[style] || detail.text_styles.formal || '';
    graphJsonEl.textContent = JSON.stringify(detail.graph, null, 2);
  });

  try {
    const bms = await API.getBenchmarks();
    benchmarksGrid.innerHTML = bms.map(b => `
      <div class="bm-card-warm">
        <div class="badge-soft">${esc(b.category)}</div>
        <h4>${esc(b.title)}</h4>
        <p>${esc(b.description)}</p>
        <div class="subtext">${esc(b.reference_standard || '')}</div>
        <button type="button" class="pill-btn secondary btn-load-bm-studio" data-bmid="${esc(b.id)}">Load into New Assessment &rarr;</button>
      </div>
    `).join('');

    benchmarksGrid.querySelectorAll('.btn-load-bm-studio').forEach(btn => {
      btn.addEventListener('click', async () => {
        const res = await API.loadBenchmark(btn.dataset.bmid);
        window.location.href = `/studio?project=${encodeURIComponent(res.project_id)}`;
      });
    });
  } catch (e) {
    console.warn('Benchmarks error:', e);
  }

  function renderCatalog(rows) {
    catalogTbody.innerHTML = rows.slice(0, 140).map(r => `
      <tr>
        <td><code>${esc(r.req_id)}</code></td>
        <td>${esc(r.clause_ref)}</td>
        <td><strong>${esc(r.title)}</strong></td>
        <td>${esc(r.target_scope)}</td>
        <td><code>${esc(r.validation_rule)}</code></td>
      </tr>
    `).join('');
  }

  try {
    const res = await fetch('/api/v1/standards/requirements');
    const data = await res.json();
    allReqs = data.requirements || [];
    renderCatalog(allReqs);
  } catch (e) {
    console.warn('Catalog error:', e);
  }

  filterInput.addEventListener('input', () => {
    const q = filterInput.value.trim().toLowerCase();
    renderCatalog(allReqs.filter(r =>
      r.req_id.toLowerCase().includes(q) ||
      r.clause_ref.toLowerCase().includes(q) ||
      r.title.toLowerCase().includes(q)
    ));
  });

  btnVerify.addEventListener('click', async () => {
    const res = await fetch('/api/v1/standards/verify-mapping');
    const data = await res.json();
    verifyBanner.classList.remove('hidden');
    const passed = (data.checks || []).filter(c => c.ok).length;
    verifyBanner.textContent = data.ok
      ? `All ${passed}/${data.checks.length} cross-layer mapping checks passed (${data.requirement_count} requirements).`
      : `Verification reported issues: ${passed}/${data.checks.length} checks passed.`;
  });
});
