/**
 * PMGA Guided Municipal Workbench (v2)
 * Single source of truth driven by GET /api/v1/projects/{id}/workspace & /turn.
 */

(function () {
  'use strict';

  const STORAGE_PROJECT_KEY = 'pmga_active_project_id';

  const state = {
    projects: [],
    projectId: null,
    workspace: null,
    activeProcessId: null,
    activeView: 'view-bpmn',
    pendingFile: null,
    pendingLogoDataUri: null,
    clearLogoPending: false,
    gapFilterStatus: 'ALL',
    gdprConfirmArmed: false,
    gdprTimer: null,
    isSending: false
  };

  let studio = null;

  function esc(val) {
    return String(val ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[ch]));
  }

  function showToast(msg, kind = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast-pill ${kind}`;
    el.textContent = String(msg || '');
    container.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, 4200);
  }

  function persistActiveProjectId(pid) {
    state.projectId = pid;
    if (pid) {
      sessionStorage.setItem(STORAGE_PROJECT_KEY, pid);
      localStorage.setItem(STORAGE_PROJECT_KEY, pid);
      const url = new URL(window.location.href);
      url.searchParams.set('project', pid);
      window.history.replaceState({}, '', url.toString());
    }
  }

  function readPreferredProjectId() {
    const params = new URLSearchParams(window.location.search);
    return params.get('project') || sessionStorage.getItem(STORAGE_PROJECT_KEY) || localStorage.getItem(STORAGE_PROJECT_KEY) || null;
  }

  // ---------------------------------------------------------------------------
  // Project Loading & Switching
  // ---------------------------------------------------------------------------

  async function initProjects(preferredId = null) {
    try {
      let projects = await API.listProjects();
      if (!Array.isArray(projects)) projects = [];
      if (projects.length === 0) {
        const created = await API.createProject('Municipal Discovery Assessment', 'Guided resilient digitization workbench');
        projects = [created];
      }
      state.projects = projects;
      const targetId =
        (preferredId && projects.some((p) => p.id === preferredId) ? preferredId : null) ||
        (projects[0] ? projects[0].id : null);

      renderProjectSelect();
      if (targetId) {
        await selectProject(targetId);
      }
    } catch (err) {
      showToast(`Could not load projects: ${err.message}`, 'error');
    }
  }

  function renderProjectSelect() {
    const sel = document.getElementById('select-project');
    if (!sel) return;
    sel.innerHTML = state.projects
      .map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`)
      .join('');
    if (state.projectId) {
      sel.value = state.projectId;
    }
  }

  async function selectProject(projectId) {
    if (!projectId) return;
    persistActiveProjectId(projectId);
    renderProjectSelect();
    hideGapSlideover();
    try {
      const ws = await API.getWorkspace(projectId);
      applyWorkspace(ws);
      switchView('view-bpmn');
    } catch (err) {
      showToast(`Failed to load workspace: ${err.message}`, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Workspace Rendering
  // ---------------------------------------------------------------------------

  function applyWorkspace(ws) {
    if (!ws) return;
    const prevIds = new Set((state.workspace?.process_models || []).map((m) => m.id));
    state.workspace = ws;

    const models = ws.process_models || [];
    const added = models.find((m) => !prevIds.has(m.id));
    if (added && prevIds.size > 0) {
      state.activeProcessId = added.id;
    } else if (!state.activeProcessId || !models.some((m) => m.id === state.activeProcessId)) {
      const primary = models.find((m) => m.is_primary) || models[0] || null;
      state.activeProcessId = primary ? primary.id : null;
    }

    renderHeaderCounters();
    renderLlmStatus();
    renderMaturityStrip();
    renderSessionFeed();
    renderBpmnView();
    renderArchitectureAndEvidence();
    renderRemediationPlan();
    renderDeliverablesControls();
  }

  function renderHeaderCounters() {
    const ws = state.workspace || {};
    const pCount = (ws.process_models || []).length;
    const eCount = (ws.structure?.entities || []).length;
    const openGaps = (ws.remediation_gaps || []).filter((g) => g.status !== 'RESOLVED').length;

    const cp = document.getElementById('tab-count-processes');
    const ce = document.getElementById('tab-count-entities');
    const cg = document.getElementById('tab-count-gaps');
    if (cp) cp.textContent = String(pCount);
    if (ce) ce.textContent = String(eCount);
    if (cg) cg.textContent = String(openGaps);
  }

  function renderLlmStatus() {
    const hasKey = Boolean(API.getApiKey());
    const provider = API.getProvider();
    const badge = document.getElementById('byok-status-badge');
    const text = document.getElementById('llm-mode-text');
    const serverReady = Boolean(state.workspace?.llm_status?.configured);

    if (hasKey) {
      if (badge) badge.textContent = `${provider.toUpperCase()} Connected`;
      if (text) text.innerHTML = `Connected via session BYOK (<strong>${esc(provider.toUpperCase())}</strong> &mdash; key held in RAM/session only).`;
    } else if (serverReady) {
      if (badge) badge.textContent = 'Server AI Active';
      if (text) text.innerHTML = `Connected to configured institutional AI provider.`;
    } else {
      if (badge) badge.textContent = 'Offline Rules';
      if (text) text.innerHTML = `Running in <strong>Deterministic Civic Rules Mode</strong> (offline, zero external AI).`;
    }
  }

  function renderMaturityStrip() {
    const strip = document.getElementById('maturity-strip');
    const pctEl = document.getElementById('maturity-percent');
    if (!strip) return;
    const mp = state.workspace?.maturity_progress || {};
    const stages = mp.stages || [];
    const current = mp.current_stage || '';
    if (pctEl) pctEl.textContent = `${mp.overall_percent || 0}%`;

    strip.innerHTML = stages
      .map((st) => {
        const cls = st.done ? 'done' : (st.id === current ? 'current' : '');
        return `<div class="maturity-step ${cls}" title="${esc(st.label)}">${esc(st.label)}</div>`;
      })
      .join('');
  }

  function renderSessionFeed() {
    const ws = state.workspace || {};
    const turns = ws.session_turns || [];
    const onboarding = document.getElementById('onboarding-cards');
    const feed = document.getElementById('session-feed');
    const chipsRow = document.getElementById('coach-quick-chips');
    const scrollBox = document.getElementById('coach-scroll');

    if (onboarding) {
      onboarding.classList.toggle('hidden', turns.length > 0);
    }
    if (!feed) return;

    feed.innerHTML = turns
      .map((t) => {
        const isUser = t.role === 'user';
        const patch = t.patch_summary || {};
        const changes = Array.isArray(patch.what_changed) ? patch.what_changed : [];
        const changesHtml = changes.length
          ? `<div class="what-changed-list">${changes.map((c) => `<span class="change-pill">${esc(c)}</span>`).join('')}</div>`
          : '';
        return `
          <div class="turn-bubble ${isUser ? 'user' : 'coach'}">
            <div class="turn-meta">
              <strong>${isUser ? 'You' : 'PMGA Guide'}</strong>
              <span>${esc(t.input_kind || 'chat')}</span>
            </div>
            <div class="turn-body">${esc(t.content)}</div>
            ${changesHtml}
          </div>
        `;
      })
      .join('');

    const lastCoach = [...turns].reverse().find((t) => t.role === 'coach');
    const quickChoices = lastCoach?.patch_summary?.quick_choices || [];
    if (chipsRow) {
      chipsRow.innerHTML = quickChoices
        .map((q) => `<button type="button" class="quick-chip" data-chip-text="${esc(q)}">${esc(q)}</button>`)
        .join('');
      chipsRow.querySelectorAll('.quick-chip').forEach((btn) => {
        btn.addEventListener('click', () => {
          const msgBox = document.getElementById('input-turn-message');
          if (msgBox) {
            msgBox.value = btn.dataset.chipText || '';
            handleSendTurn();
          }
        });
      });
    }

    if (scrollBox) {
      scrollBox.scrollTop = scrollBox.scrollHeight;
    }
  }

  async function renderBpmnView() {
    const ws = state.workspace || {};
    const models = ws.process_models || [];
    const gaps = ws.remediation_gaps || [];
    const sel = document.getElementById('select-process-model');
    const banner = document.getElementById('bpmn-description-banner');

    if (sel) {
      if (models.length === 0) {
        sel.innerHTML = '<option value="">No processes yet</option>';
      } else {
        sel.innerHTML = models
          .map((m) => {
            const gCount = gaps.filter((g) => g.status !== 'RESOLVED' && (!g.process_model_id || g.process_model_id === m.id)).length;
            return `<option value="${esc(m.id)}">${esc(m.title)} (v${m.version} &bull; ${gCount} steps)</option>`;
          })
          .join('');
        if (state.activeProcessId) sel.value = state.activeProcessId;
      }
    }

    const activeModel = models.find((m) => m.id === state.activeProcessId) || models[0] || null;
    if (!activeModel) {
      if (banner) {
        banner.textContent = 'Start a conversation on the left or upload a spreadsheet / .bpmn file to generate your first Process Map.';
      }
      if (studio) {
        await studio.loadXML('');
      }
      return;
    }

    if (banner) {
      banner.innerHTML = `<strong>${esc(activeModel.title)}:</strong> ${esc(activeModel.description_text || 'Plain-English process description ready after extraction.')}`;
    }

    if (studio && activeModel.bpmn_xml) {
      try {
        await studio.loadXML(activeModel.bpmn_xml);
        const procGaps = gaps.filter((g) => !g.process_model_id || g.process_model_id === activeModel.id);
        studio.setGapBadges(procGaps, (nodeId, matchingGaps) => {
          openGapSlideover(nodeId, matchingGaps);
        });
      } catch (err) {
        showToast(`Could not render BPMN canvas: ${err.message}`, 'error');
      }
    }
  }

  function renderArchitectureAndEvidence() {
    const ws = state.workspace || {};
    const treeEl = document.getElementById('architecture-tree');
    const evEl = document.getElementById('evidence-ledger');
    const entities = ws.structure?.entities || [];
    const evidence = ws.evidence_items || [];

    if (treeEl) {
      if (entities.length === 0) {
        treeEl.innerHTML = '<p class="subtext">No systems, roles, or processes mapped yet. Chat on the left or upload an Excel asset list.</p>';
      } else {
        treeEl.innerHTML = entities
          .map((e) => {
            const decl = e.declared_protection || {};
            const inh = e.inherited_protection || {};
            const hasInh = inh && (inh.confidentiality || inh.integrity || inh.availability || inh.rto || inh.mtpd);
            const inhPill = hasInh
              ? `<div class="inherited-pill">Inherited (BSI Maximumprinzip): C=${esc(inh.confidentiality || '-')} &bull; I=${esc(inh.integrity || '-')} &bull; A=${esc(inh.availability || '-')} &bull; RTO=${esc(inh.rto || '-')} &bull; MTPD=${esc(inh.mtpd || '-')}${Array.isArray(inh.from_processes) && inh.from_processes.length ? ` (from ${esc(inh.from_processes.join(', '))})` : ''}</div>`
              : '';
            const declSummary = (decl.confidentiality || decl.integrity || decl.availability)
              ? `<div class="subtext" style="margin-top:4px;">Declared: C=${esc(decl.confidentiality || '-')} / I=${esc(decl.integrity || '-')} / A=${esc(decl.availability || '-')}</div>`
              : '';
            return `
              <div class="entity-card">
                <div class="entity-card-top">
                  <div>
                    <span class="badge-soft">${esc(e.entity_type)}</span>
                    <strong>${esc(e.name)}</strong>
                  </div>
                  <span class="subtext">${e.owner_role ? `Owner: ${esc(e.owner_role)}` : 'Owner unset'}</span>
                </div>
                ${declSummary}
                ${inhPill}
              </div>
            `;
          })
          .join('');
      }
    }

    if (evEl) {
      if (evidence.length === 0) {
        evEl.innerHTML = '<p class="subtext">Upload a handbook, policy, AGB, or SBOM to track verified vs. contradicted claims.</p>';
      } else {
        evEl.innerHTML = evidence
          .map((ev) => {
            const cls = ev.status === 'CONTRADICTED' ? 'contradicted' : '';
            const badgeCls = ev.status === 'CONTRADICTED' ? 'coral' : (ev.status === 'USER_CONFIRMED' ? 'sage' : 'amber');
            return `
              <div class="evidence-row ${cls}">
                <div style="display:flex; justify-content:space-between; gap:8px; align-items:center; flex-wrap:wrap;">
                  <span><code>${esc(ev.req_id)}</code> <strong>${esc(ev.claim_text)}</strong></span>
                  <span class="badge-soft ${badgeCls}">${esc(ev.status)}</span>
                </div>
                <div class="subtext" style="margin-top:3px;">Source: ${esc(ev.source_location || 'document')}</div>
                ${ev.source_quote ? `<blockquote class="evidence-quote">${esc(ev.source_quote)}</blockquote>` : ''}
              </div>
            `;
          })
          .join('');
      }
    }
  }

  function renderRemediationPlan() {
    const container = document.getElementById('remediation-groups');
    if (!container) return;
    const allGaps = state.workspace?.remediation_gaps || [];
    const filtered = state.gapFilterStatus === 'ALL'
      ? allGaps
      : allGaps.filter((g) => g.status === state.gapFilterStatus);

    if (filtered.length === 0) {
      container.innerHTML = '<p class="subtext">No remediation items match the selected filter.</p>';
      return;
    }

    const buckets = [
      { key: 'QUICK_WIN', title: 'Quick Wins (1–2 Days)' },
      { key: 'BEFORE_DIGITALIZATION', title: 'Before Digitalization' },
      { key: 'ONGOING', title: 'Ongoing Hygiene & Documentation' }
    ];

    container.innerHTML = buckets
      .map((b) => {
        const items = filtered.filter((g) => g.priority_bucket === b.key);
        if (items.length === 0) return '';
        return `
          <div class="gap-bucket-title">${esc(b.title)} (${items.length})</div>
          ${items.map((g) => renderGapCardHtml(g)).join('')}
        `;
      })
      .join('');

    container.querySelectorAll('[data-gap-status-btn]').forEach((btn) => {
      btn.addEventListener('click', () => {
        handleUpdateGapStatus(btn.dataset.gapId, btn.dataset.gapStatusBtn);
      });
    });

    container.querySelectorAll('[data-inspect-gap-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const gid = btn.dataset.inspectGapId;
        const gap = allGaps.find((x) => x.id === gid);
        if (!gap) return;
        if (gap.process_model_id) {
          state.activeProcessId = gap.process_model_id;
        }
        switchView('view-bpmn');
        renderBpmnView().then(() => {
          const nid = (gap.affected_node_ids || [])[0] || '';
          if (studio && nid) studio.focusNode(nid);
          openGapSlideover(nid || 'process', [gap]);
        });
      });
    });
  }

  function renderGapCardHtml(g) {
    const sevCls = `sev-${String(g.severity || 'INFO').toLowerCase()}`;
    const driftHtml = g.policy_vs_reality
      ? `<div class="policy-drift-box"><strong>Policy vs. Reality:</strong> ${esc(g.policy_vs_reality)}</div>`
      : '';
    const stepsHtml = Array.isArray(g.action_steps) && g.action_steps.length
      ? `<ol class="gap-steps-list">${g.action_steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`
      : '';
    const compHtml = Array.isArray(g.compensating_options) && g.compensating_options.length
      ? `<div class="subtext" style="margin-top:4px;"><strong>Compensating Safeguards:</strong> ${esc(g.compensating_options.join(' • '))}</div>`
      : '';

    const statuses = ['OPEN', 'IN_PROGRESS', 'ACCEPTED_RISK', 'RESOLVED'];
    return `
      <article class="gap-card ${sevCls}" data-gap-id="${esc(g.id)}">
        <div class="gap-card-header">
          <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
            <span class="badge-soft">${esc(g.standard_clause || g.req_id)}</span>
            <span class="badge-soft terracotta">${esc(g.severity)}</span>
            <span class="badge-soft sage">${esc(g.status)}</span>
          </div>
          <button type="button" class="pill-btn small secondary" data-inspect-gap-id="${esc(g.id)}">Inspect on Diagram</button>
        </div>
        <p style="margin:4px 0; font-weight:600;">${esc(g.warm_summary)}</p>
        ${driftHtml}
        ${stepsHtml}
        ${compHtml}
        <div class="status-btn-row">
          ${statuses
            .map((st) => `<button type="button" class="pill-btn small ${g.status === st ? 'primary' : ''}" data-gap-id="${esc(g.id)}" data-gap-status-btn="${st}">${esc(st.replace('_', ' '))}</button>`)
            .join('')}
        </div>
      </article>
    `;
  }

  function openGapSlideover(nodeId, matchingGaps) {
    const drawer = document.getElementById('gap-slideover');
    const titleEl = document.getElementById('slideover-title');
    const bodyEl = document.getElementById('slideover-body');
    if (!drawer || !bodyEl) return;

    if (titleEl) {
      titleEl.textContent = `Helpful Next Steps (${matchingGaps.length}) — ${nodeId}`;
    }

    bodyEl.innerHTML = matchingGaps
      .map((g) => {
        const statuses = ['OPEN', 'IN_PROGRESS', 'ACCEPTED_RISK', 'RESOLVED'];
        return `
          <div class="gap-card sev-${esc(String(g.severity || 'INFO').toLowerCase())}">
            <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:6px;">
              <span class="badge-soft">${esc(g.standard_clause || g.req_id)}</span>
              <span class="badge-soft terracotta">${esc(g.severity)}</span>
              <span class="badge-soft sage">${esc(g.status)}</span>
            </div>
            <p style="margin:0 0 6px; font-weight:600;">${esc(g.warm_summary)}</p>
            ${g.policy_vs_reality ? `<div class="policy-drift-box">${esc(g.policy_vs_reality)}</div>` : ''}
            ${Array.isArray(g.action_steps) && g.action_steps.length ? `<ol class="gap-steps-list">${g.action_steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>` : ''}
            <div class="status-btn-row">
              ${statuses
                .map((st) => `<button type="button" class="pill-btn small ${g.status === st ? 'primary' : ''}" data-slideover-gap-id="${esc(g.id)}" data-slideover-status="${st}">${esc(st.replace('_', ' '))}</button>`)
                .join('')}
            </div>
          </div>
        `;
      })
      .join('');

    bodyEl.querySelectorAll('[data-slideover-status]').forEach((btn) => {
      btn.addEventListener('click', () => {
        handleUpdateGapStatus(btn.dataset.slideoverGapId, btn.dataset.slideoverStatus, nodeId);
      });
    });

    drawer.classList.remove('hidden');
  }

  function hideGapSlideover() {
    document.getElementById('gap-slideover')?.classList.add('hidden');
  }

  // ---------------------------------------------------------------------------
  // Deliverables & Brand Profile
  // ---------------------------------------------------------------------------

  function renderDeliverablesControls() {
    if (!state.projectId) return;
    const docSel = document.getElementById('select-deliverable-type');
    const procSel = document.getElementById('select-deliverable-process');
    const frame = document.getElementById('deliverable-preview-frame');
    const models = state.workspace?.process_models || [];

    if (procSel) {
      procSel.innerHTML = models
        .map((m) => `<option value="${esc(m.id)}">${esc(m.title)}</option>`)
        .join('');
      if (state.activeProcessId) procSel.value = state.activeProcessId;
      procSel.classList.toggle('hidden', docSel?.value !== 'prozesssteckbrief');
    }

    // Hydrate brand form inputs from workspace
    const bp = state.workspace?.brand_profile || {};
    const orgInput = document.getElementById('input-brand-org');
    const fontSel = document.getElementById('select-brand-font');
    const primInput = document.getElementById('input-brand-primary');
    const accInput = document.getElementById('input-brand-accent');
    const footInput = document.getElementById('input-brand-footer');

    if (orgInput && !orgInput.matches(':focus')) orgInput.value = bp.org_name || state.workspace?.project?.title || '';
    if (fontSel && bp.font_family) fontSel.value = bp.font_family;
    if (primInput && bp.primary_color) primInput.value = bp.primary_color;
    if (accInput && bp.accent_color) accInput.value = bp.accent_color;
    if (footInput && !footInput.matches(':focus')) footInput.value = bp.footer_text || '';

    const docType = docSel?.value || 'remediation_plan';
    const pid = docType === 'prozesssteckbrief' ? (procSel?.value || state.activeProcessId) : state.activeProcessId;

    const htmlUrl = API.getDeliverableUrl(state.projectId, docType, 'html', pid);
    const pdfUrl = API.getDeliverableUrl(state.projectId, docType, 'pdf', pid);
    const xlsxUrl = API.getDeliverableUrl(state.projectId, docType, 'xlsx', pid);
    const bpmnUrl = API.getDeliverableUrl(state.projectId, docType, 'bpmn', pid);
    const oscalUrl = `/api/v1/export/oscal/${encodeURIComponent(state.projectId)}`;

    const lPdf = document.getElementById('link-download-pdf');
    const lHtml = document.getElementById('link-open-html');
    const lXlsx = document.getElementById('link-download-xlsx');
    const lBpmn = document.getElementById('link-download-bpmn');
    const lOscal = document.getElementById('link-download-oscal');
    if (lPdf) lPdf.href = pdfUrl;
    if (lHtml) lHtml.href = htmlUrl;
    if (lXlsx) lXlsx.href = xlsxUrl;
    if (lBpmn) lBpmn.href = bpmnUrl;
    if (lOscal) lOscal.href = oscalUrl;

    if (frame && state.activeView === 'view-deliverables') {
      frame.src = `${htmlUrl}&_ts=${Date.now()}`;
    }
  }

  // ---------------------------------------------------------------------------
  // Actions (Turns, Canvas Sync, Gap Status Updates, Brand, GDPR, BYOK)
  // ---------------------------------------------------------------------------

  async function handleSendTurn() {
    if (!state.projectId || state.isSending) return;
    const msgBox = document.getElementById('input-turn-message');
    const sendBtn = document.getElementById('btn-send-turn');
    const docSel = document.getElementById('select-upload-doctype');
    const langSel = document.getElementById('select-language');

    const msg = (msgBox?.value || '').trim();
    const file = state.pendingFile;

    if (!msg && !file) {
      showToast('Enter a short message or attach a file first.', 'info');
      return;
    }

    state.isSending = true;
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.textContent = 'Working...';
    }

    try {
      const res = await API.sendTurn(state.projectId, {
        inputKind: 'chat',
        message: msg,
        activeProcessId: state.activeProcessId,
        docType: docSel?.value || 'AUTO',
        file,
        language: langSel?.value || 'en'
      });
      if (msgBox) msgBox.value = '';
      clearPendingFile();
      applyWorkspace(res.workspace);
      showToast('Workspace updated.', 'success');
    } catch (err) {
      showToast(err.message || 'Turn failed.', 'error');
    } finally {
      state.isSending = false;
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
      }
    }
  }

  async function handleSaveOrExtractBpmn(extractDescription) {
    if (!state.projectId || !state.activeProcessId || !studio) {
      showToast('Select or create a process diagram first.', 'info');
      return;
    }
    try {
      const xml = await studio.exportXML();
      if (!xml) {
        showToast('No BPMN XML available to save.', 'error');
        return;
      }
      const res = await API.syncProcessBpmn(state.projectId, state.activeProcessId, xml, extractDescription);
      applyWorkspace(res.workspace);
      showToast(
        extractDescription ? 'Description extracted & gap checks updated.' : 'Diagram edits saved.',
        'success'
      );
    } catch (err) {
      showToast(`Diagram update failed: ${err.message}`, 'error');
    }
  }

  async function handleUpdateGapStatus(gapId, status, reopenNodeId = null) {
    if (!state.projectId || !gapId) return;
    try {
      await API.updateGapStatus(state.projectId, gapId, status);
      const ws = await API.getWorkspace(state.projectId);
      applyWorkspace(ws);
      if (reopenNodeId) {
        const updatedGaps = (ws.remediation_gaps || []).filter(
          (g) => g.id === gapId || (Array.isArray(g.affected_node_ids) && g.affected_node_ids.includes(reopenNodeId))
        );
        if (updatedGaps.length > 0) openGapSlideover(reopenNodeId, updatedGaps);
      }
      showToast(`Updated item to ${status.replace('_', ' ')}.`, 'success');
    } catch (err) {
      showToast(`Status update failed: ${err.message}`, 'error');
    }
  }

  function setPendingFile(file) {
    state.pendingFile = file || null;
    const chip = document.getElementById('pending-file-chip');
    const nameEl = document.getElementById('pending-file-name');
    if (chip && nameEl) {
      if (file) {
        nameEl.textContent = file.name;
        chip.classList.remove('hidden');
      } else {
        nameEl.textContent = '';
        chip.classList.add('hidden');
      }
    }
  }

  function clearPendingFile() {
    setPendingFile(null);
    const fi = document.getElementById('file-universal-upload');
    if (fi) fi.value = '';
  }

  function switchView(viewId) {
    state.activeView = viewId;
    document.querySelectorAll('.workspace-tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.view === viewId);
    });
    document.querySelectorAll('.workspace-view').forEach((view) => {
      view.classList.toggle('active', view.id === viewId);
    });
    if (viewId === 'view-bpmn' && studio) {
      requestAnimationFrame(() => studio.fitViewport());
    } else if (viewId === 'view-deliverables') {
      renderDeliverablesControls();
    }
  }

  // ---------------------------------------------------------------------------
  // Event Wiring
  // ---------------------------------------------------------------------------

  function bindEvents() {
    document.getElementById('select-project')?.addEventListener('change', (e) => {
      selectProject(e.target.value);
    });

    const newProjBar = document.getElementById('inline-new-project-bar');
    const newProjInput = document.getElementById('input-new-project-title');
    document.getElementById('btn-new-project')?.addEventListener('click', () => {
      newProjBar?.classList.remove('hidden');
      newProjInput?.focus();
    });
    document.getElementById('btn-cancel-new-project')?.addEventListener('click', () => {
      newProjBar?.classList.add('hidden');
    });

    const confirmNewProject = async () => {
      const title = (newProjInput?.value || '').trim();
      if (!title) {
        showToast('Enter a project title.', 'info');
        return;
      }
      try {
        const created = await API.createProject(title, '');
        if (newProjInput) newProjInput.value = '';
        newProjBar?.classList.add('hidden');
        await initProjects(created.id);
        showToast(`Created "${created.title}".`, 'success');
      } catch (err) {
        showToast(`Failed creating project: ${err.message}`, 'error');
      }
    };
    document.getElementById('btn-confirm-new-project')?.addEventListener('click', confirmNewProject);
    newProjInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        confirmNewProject();
      }
    });

    // GDPR Wipe with safe 2-click confirmation
    const gdprBtn = document.getElementById('btn-gdpr-purge');
    gdprBtn?.addEventListener('click', async () => {
      if (!state.projectId) return;
      if (!state.gdprConfirmArmed) {
        state.gdprConfirmArmed = true;
        gdprBtn.textContent = 'Confirm Wipe?';
        clearTimeout(state.gdprTimer);
        state.gdprTimer = setTimeout(() => {
          state.gdprConfirmArmed = false;
          gdprBtn.textContent = 'GDPR Wipe';
        }, 4000);
        return;
      }
      state.gdprConfirmArmed = false;
      gdprBtn.textContent = 'GDPR Wipe';
      clearTimeout(state.gdprTimer);
      try {
        await API.gdprPurge(state.projectId);
        sessionStorage.removeItem(STORAGE_PROJECT_KEY);
        localStorage.removeItem(STORAGE_PROJECT_KEY);
        state.activeProcessId = null;
        await initProjects();
        showToast('Project data and checkpoints permanently wiped.', 'success');
      } catch (err) {
        showToast(`Wipe failed: ${err.message}`, 'error');
      }
    });

    // Onboarding Cards
    document.getElementById('btn-onboard-scratch')?.addEventListener('click', () => {
      const box = document.getElementById('input-turn-message');
      if (box) {
        box.placeholder = 'e.g. Citizen Registration: Citizen emails PDF -> Clerk checks Anmeldesystem -> Certificate sent';
        box.focus();
      }
    });
    document.getElementById('btn-onboard-excel')?.addEventListener('click', () => {
      const dt = document.getElementById('select-upload-doctype');
      if (dt) dt.value = 'EXCEL_INVENTORY';
      document.getElementById('file-universal-upload')?.click();
    });
    document.getElementById('btn-onboard-document')?.addEventListener('click', () => {
      const dt = document.getElementById('select-upload-doctype');
      if (dt) dt.value = 'AUTO';
      document.getElementById('file-universal-upload')?.click();
    });
    document.getElementById('btn-onboard-bpmn')?.addEventListener('click', () => {
      document.getElementById('file-universal-upload')?.click();
    });

    // Universal Attachment & Composer Drag-n-Drop
    const fileInput = document.getElementById('file-universal-upload');
    document.getElementById('btn-attach-file')?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', (e) => {
      const file = e.target.files?.[0];
      if (file) setPendingFile(file);
    });
    document.getElementById('btn-clear-pending-file')?.addEventListener('click', clearPendingFile);

    const composer = document.getElementById('coach-composer');
    if (composer) {
      composer.addEventListener('dragover', (e) => {
        e.preventDefault();
        composer.classList.add('drag-over');
      });
      composer.addEventListener('dragleave', () => composer.classList.remove('drag-over'));
      composer.addEventListener('drop', (e) => {
        e.preventDefault();
        composer.classList.remove('drag-over');
        const file = e.dataTransfer?.files?.[0];
        if (file) setPendingFile(file);
      });
    }

    document.getElementById('btn-send-turn')?.addEventListener('click', handleSendTurn);
    document.getElementById('input-turn-message')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSendTurn();
      }
    });

    // Right Pane View Tabs
    document.querySelectorAll('.workspace-tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchView(btn.dataset.view));
    });

    // BPMN Toolbar
    document.getElementById('select-process-model')?.addEventListener('change', (e) => {
      state.activeProcessId = e.target.value || null;
      hideGapSlideover();
      renderBpmnView();
      renderDeliverablesControls();
    });
    document.getElementById('btn-extract-canvas-gaps')?.addEventListener('click', () => handleSaveOrExtractBpmn(true));
    document.getElementById('btn-save-bpmn')?.addEventListener('click', () => handleSaveOrExtractBpmn(false));
    document.getElementById('btn-zoom-fit')?.addEventListener('click', () => studio?.fitViewport());
    document.getElementById('btn-zoom-in')?.addEventListener('click', () => studio?.zoomIn());
    document.getElementById('btn-zoom-out')?.addEventListener('click', () => studio?.zoomOut());
    document.getElementById('btn-apply-resize')?.addEventListener('click', () => {
      const w = document.getElementById('node-width-input')?.value;
      const h = document.getElementById('node-height-input')?.value;
      if (w && h && studio?.resizeSelectedElement(w, h)) {
        showToast('Resized selected element.', 'success');
      } else {
        showToast('Select a BPMN box on canvas first.', 'info');
      }
    });
    document.getElementById('btn-export-bpmn')?.addEventListener('click', async () => {
      const xml = await studio?.exportXML();
      if (!xml) return;
      const blob = new Blob([xml], { type: 'application/xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'pmga-process.bpmn';
      a.click();
    });
    document.getElementById('btn-export-svg')?.addEventListener('click', async () => {
      const svg = await studio?.exportSVG();
      if (!svg) return;
      const blob = new Blob([svg], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'pmga-process.svg';
      a.click();
    });
    document.getElementById('btn-close-slideover')?.addEventListener('click', hideGapSlideover);

    // Architecture quick add entity
    document.getElementById('btn-add-entity')?.addEventListener('click', async () => {
      if (!state.projectId) return;
      const name = (document.getElementById('input-entity-name')?.value || '').trim();
      const entityType = document.getElementById('select-entity-type')?.value || 'APPLICATION';
      const ownerRole = (document.getElementById('input-entity-owner')?.value || '').trim();
      if (!name) {
        showToast('Enter an entity name.', 'info');
        return;
      }
      try {
        await API.createEntity(state.projectId, {
          name,
          entity_type: entityType,
          owner_role: ownerRole || null
        });
        document.getElementById('input-entity-name').value = '';
        const ws = await API.getWorkspace(state.projectId);
        applyWorkspace(ws);
        showToast(`Added ${entityType.toLowerCase()} "${name}".`, 'success');
      } catch (err) {
        showToast(`Failed adding entity: ${err.message}`, 'error');
      }
    });

    // Remediation filter
    document.getElementById('filter-gap-status')?.addEventListener('change', (e) => {
      state.gapFilterStatus = e.target.value;
      renderRemediationPlan();
    });

    // Deliverables & Brand Profile
    document.getElementById('select-deliverable-type')?.addEventListener('change', renderDeliverablesControls);
    document.getElementById('select-deliverable-process')?.addEventListener('change', renderDeliverablesControls);

    document.getElementById('input-brand-logo')?.addEventListener('change', (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        state.pendingLogoDataUri = String(reader.result || '');
      };
      reader.readAsDataURL(file);
    });

    document.getElementById('btn-apply-brand')?.addEventListener('click', async () => {
      if (!state.projectId) return;
      try {
        const payload = {
          org_name: document.getElementById('input-brand-org')?.value || '',
          font_family: document.getElementById('select-brand-font')?.value || 'Plus Jakarta Sans',
          primary_color: document.getElementById('input-brand-primary')?.value || '#1E4E68',
          accent_color: document.getElementById('input-brand-accent')?.value || '#C86D44',
          footer_text: document.getElementById('input-brand-footer')?.value || '',
          clear_logo: Boolean(document.getElementById('chk-clear-logo')?.checked)
        };
        if (state.pendingLogoDataUri) {
          payload.logo_data_uri = state.pendingLogoDataUri;
        }
        await API.saveBrandProfile(state.projectId, payload);
        state.pendingLogoDataUri = null;
        if (document.getElementById('chk-clear-logo')) document.getElementById('chk-clear-logo').checked = false;
        const ws = await API.getWorkspace(state.projectId);
        applyWorkspace(ws);
        showToast('Deliverable branding updated.', 'success');
      } catch (err) {
        showToast(`Branding update failed: ${err.message}`, 'error');
      }
    });

    // BYOK AI Settings Modal
    const modal = document.getElementById('modal-byok');
    const openByok = () => {
      const provSel = document.getElementById('select-llm-provider');
      const modelInput = document.getElementById('input-llm-model');
      const urlInput = document.getElementById('input-llm-base-url');
      const keyInput = document.getElementById('input-gemini-key');
      if (provSel) provSel.value = API.getProvider();
      if (modelInput) modelInput.value = API.getModelName();
      if (urlInput) urlInput.value = API.getBaseUrl();
      if (keyInput) keyInput.value = API.getApiKey();
      modal?.classList.remove('hidden');
    };
    const closeByok = () => modal?.classList.add('hidden');

    document.getElementById('btn-open-byok')?.addEventListener('click', openByok);
    document.getElementById('btn-banner-byok')?.addEventListener('click', openByok);
    document.getElementById('btn-close-byok')?.addEventListener('click', closeByok);

    document.getElementById('btn-clear-byok')?.addEventListener('click', () => {
      API.setApiKey('', false);
      const keyInput = document.getElementById('input-gemini-key');
      if (keyInput) keyInput.value = '';
      renderLlmStatus();
      closeByok();
      showToast('Session AI key cleared.', 'info');
    });

    document.getElementById('btn-save-byok')?.addEventListener('click', () => {
      const prov = document.getElementById('select-llm-provider')?.value || 'gemini';
      const model = document.getElementById('input-llm-model')?.value || '';
      const baseUrl = document.getElementById('input-llm-base-url')?.value || '';
      const key = document.getElementById('input-gemini-key')?.value || '';
      API.setProvider(prov);
      API.setModelName(model);
      API.setBaseUrl(baseUrl);
      API.setApiKey(key, true);
      renderLlmStatus();
      closeByok();
      showToast(key.trim() ? 'Session AI key ready.' : 'Saved provider settings (offline mode).', 'success');
    });

    document.getElementById('btn-test-llm')?.addEventListener('click', async () => {
      const out = document.getElementById('byok-test-result');
      const prov = document.getElementById('select-llm-provider')?.value || 'gemini';
      const model = document.getElementById('input-llm-model')?.value || '';
      const baseUrl = document.getElementById('input-llm-base-url')?.value || '';
      const key = document.getElementById('input-gemini-key')?.value || '';
      if (out) out.textContent = 'Testing connection...';
      try {
        const res = await API.testLLMConnection({ provider: prov, model_name: model, base_url: baseUrl, api_key: key });
        if (out) out.textContent = res.ok ? `Connected (${res.provider}: ${res.model})` : (res.error || 'Connection test failed.');
      } catch (err) {
        if (out) out.textContent = `Test failed: ${err.message}`;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', async () => {
    studio = new BPMNStudio('bpmn-canvas');
    bindEvents();
    await initProjects(readPreferredProjectId());
  });
})();
