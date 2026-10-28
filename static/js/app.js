/**
 * PMGA Main Application Orchestrator
 */

document.addEventListener('DOMContentLoaded', async () => {
  let activeProjectId = null;
  let bpmnStudio = null;
  let allIsoControls = [];

  // Elements
  const tabs = document.querySelectorAll('.tab-btn');
  const panes = document.querySelectorAll('.tab-pane');
  const projectSelect = document.getElementById('select-active-project');
  const btnNewProject = document.getElementById('btn-new-project');
  const byokStatusBtn = document.getElementById('btn-byok-modal');
  const byokLabel = document.getElementById('byok-label');
  const modalByok = document.getElementById('modal-byok');
  const btnCloseByok = document.getElementById('btn-close-byok');
  const btnSaveByok = document.getElementById('btn-save-byok');
  const selectLlmProvider = document.getElementById('select-llm-provider');
  const inputGeminiKey = document.getElementById('input-gemini-key');
  const geminiKeyGroup = document.getElementById('gemini-key-group');
  const kiconnectInfoGroup = document.getElementById('kiconnect-info-group');
  const kiconnectStatusText = document.getElementById('kiconnect-status-text');
  const chkSaveLocal = document.getElementById('chk-save-local');

  const benchmarksList = document.getElementById('benchmarks-list');
  const inputActual = document.getElementById('input-actual-process');
  const inputFormal = document.getElementById('input-formal-policy');
  const fileActual = document.getElementById('file-actual-process');
  const fileFormal = document.getElementById('file-formal-policy');
  const fileActualName = document.getElementById('file-actual-name');
  const fileFormalName = document.getElementById('file-formal-name');
  const btnGenerateBpmn = document.getElementById('btn-generate-bpmn');

  const btnSaveBpmn = document.getElementById('btn-save-bpmn');
  const btnExportBpmn = document.getElementById('btn-export-bpmn');
  const btnExportSvg = document.getElementById('btn-export-svg');
  const btnProceedToIso = document.getElementById('btn-proceed-to-iso');

  // Box Resizing Elements (Point #7)
  const nodeWidthInput = document.getElementById('node-width-input');
  const nodeHeightInput = document.getElementById('node-height-input');
  const btnPresetCompact = document.getElementById('btn-preset-compact');
  const btnPresetStandard = document.getElementById('btn-preset-standard');
  const btnPresetWide = document.getElementById('btn-preset-wide');
  const btnPresetLarge = document.getElementById('btn-preset-large');

  // Telemetry Elements
  const telemetryBar = document.getElementById('telemetry-bar');
  const tSoundness = document.getElementById('t-soundness');
  const tFaithfulness = document.getElementById('t-faithfulness');
  const tDetail = document.getElementById('t-detail');

  // ISO Explorer & Accessible Modal (Screenshot #1 Fix)
  const filterIsoChapter = document.getElementById('filter-iso-chapter');
  const filterIsoSearch = document.getElementById('filter-iso-search');
  const isoTbody = document.getElementById('iso-controls-tbody');
  const btnRunGapAnalysis = document.getElementById('btn-run-gap-analysis');
  const modalIsoDetail = document.getElementById('modal-iso-detail');
  const modalIsoTitle = document.getElementById('modal-iso-title');
  const modalIsoBadge = document.getElementById('modal-iso-badge');
  const modalIsoBody = document.getElementById('modal-iso-body');
  const btnCloseIsoDetail = document.getElementById('btn-close-iso-detail');
  const btnDismissIsoDetail = document.getElementById('btn-dismiss-iso-detail');

  const btnExportExcel = document.getElementById('btn-export-excel-report');
  const btnGdprPurge = document.getElementById('btn-gdpr-purge');

  // --- 1. Tab Switching ---
  function switchTab(tabId) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
    panes.forEach(p => p.classList.toggle('active', p.id === tabId));
    if (tabId === 'tab-bpmn' && bpmnStudio && bpmnStudio.modeler) {
      setTimeout(() => {
        const canvas = bpmnStudio.modeler.get('canvas');
        if (canvas) canvas.zoom('fit-viewport');
      }, 50);
    }
  }

  tabs.forEach(t => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });

  // --- 2. LLM Provider Status & Modal ---
  async function updateLlmStatus() {
    try {
      const status = await API.getLLMStatus();
      const currentProvider = API.getProvider();
      selectLlmProvider.value = currentProvider;

      if (currentProvider === 'gemini') {
        const key = API.getApiKey();
        if (key || status.gemini.configured) {
          byokStatusBtn.classList.add('configured');
          byokStatusBtn.classList.remove('unconfigured');
          byokLabel.textContent = `Gemini (3.8-flash): Ready`;
        } else {
          byokStatusBtn.classList.add('unconfigured');
          byokStatusBtn.classList.remove('configured');
          byokLabel.textContent = 'Gemini Key Unset';
        }
        geminiKeyGroup.classList.remove('hidden');
        kiconnectInfoGroup.classList.add('hidden');
      } else {
        if (status.kiconnect.configured) {
          byokStatusBtn.classList.add('configured');
          byokStatusBtn.classList.remove('unconfigured');
          byokLabel.textContent = 'Uni KI-Connect: Ready';
          kiconnectStatusText.textContent = `Active Model: ${status.kiconnect.model} | Pacing: ${status.min_interval_sec}s`;
        } else {
          byokStatusBtn.classList.add('unconfigured');
          byokStatusBtn.classList.remove('configured');
          byokLabel.textContent = 'Uni KI-Connect: Check .env';
          kiconnectStatusText.textContent = 'API_BASE or API_KEY missing in .env file.';
        }
        geminiKeyGroup.classList.add('hidden');
        kiconnectInfoGroup.classList.remove('hidden');
      }

      inputGeminiKey.value = API.getApiKey();
    } catch (e) {
      console.warn('Could not fetch LLM status:', e);
    }
  }

  selectLlmProvider.addEventListener('change', (e) => {
    const prov = e.target.value;
    if (prov === 'gemini') {
      geminiKeyGroup.classList.remove('hidden');
      kiconnectInfoGroup.classList.add('hidden');
    } else {
      geminiKeyGroup.classList.add('hidden');
      kiconnectInfoGroup.classList.remove('hidden');
    }
  });

  byokStatusBtn.addEventListener('click', () => modalByok.classList.add('open'));
  btnCloseByok.addEventListener('click', () => modalByok.classList.remove('open'));
  btnSaveByok.addEventListener('click', () => {
    const prov = selectLlmProvider.value;
    API.setProvider(prov);
    if (prov === 'gemini') {
      API.setApiKey(inputGeminiKey.value.trim(), chkSaveLocal.checked);
    }
    updateLlmStatus();
    modalByok.classList.remove('open');
  });

  // --- 3. Project Management ---
  async function refreshProjects(selectNewId = null) {
    const projects = await API.listProjects();
    projectSelect.innerHTML = '<option value="">Select or Create an Assessment...</option>';
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.title} (${p.created_at.slice(0, 10)})`;
      projectSelect.appendChild(opt);
    });

    if (selectNewId) {
      projectSelect.value = selectNewId;
      activeProjectId = selectNewId;
      await loadProjectData(selectNewId);
    } else if (projects.length > 0 && !activeProjectId) {
      projectSelect.value = projects[0].id;
      activeProjectId = projects[0].id;
      await loadProjectData(activeProjectId);
    }
  }

  projectSelect.addEventListener('change', async (e) => {
    activeProjectId = e.target.value;
    if (activeProjectId) {
      await loadProjectData(activeProjectId);
    }
  });

  btnNewProject.addEventListener('click', async () => {
    const title = prompt('Enter a title for this assessment:', 'Department Workflow Review');
    if (!title) return;
    const proj = await API.createProject(title);
    await refreshProjects(proj.id);
  });

  async function loadProjectData(projId) {
    const data = await API.getProject(projId);
    if (!data) return;

    const actualDoc = data.documents.find(d => d.doc_type === 'ACTUAL_PROCESS');
    const formalDoc = data.documents.find(d => d.doc_type === 'FORMAL_POLICY');

    if (actualDoc) inputActual.value = actualDoc.extracted_text;
    if (formalDoc) inputFormal.value = formalDoc.extracted_text;

    if (data.bpmn_model && bpmnStudio) {
      await bpmnStudio.loadXML(data.bpmn_model.bpmn_xml);
      document.getElementById('bpmn-canvas-title').textContent = data.bpmn_model.title || 'Process Diagram';
    }

    if (data.gaps && data.gaps.length > 0) {
      const findings = data.gaps.map(g => ({
        ...g,
        actionable_todos: JSON.parse(g.actionable_todos_json || '[]'),
        roles_to_consult: JSON.parse(g.roles_to_consult_json || '[]'),
        compensating_controls: JSON.parse(g.compensating_controls_json || '[]')
      }));
      GapDashboard.render({ findings, compliance_score_percent: 75, executive_summary: 'Loaded previous assessment findings.' });
    }
  }

  // --- 4. Empirical Benchmarks Ingestion ---
  async function loadBenchmarks() {
    const bms = await API.getBenchmarks();
    benchmarksList.innerHTML = bms.map(b => `
      <div class="bm-card" data-bmid="${b.id}">
        <h4>${b.title}</h4>
        <p>${b.description}</p>
        <div class="bm-meta">Ref: ${b.reference_standard || 'ISO/IEC 27002'}</div>
        <div class="bm-load-btn">Load Case Study &rarr;</div>
      </div>
    `).join('');

    document.querySelectorAll('.bm-card').forEach(card => {
      card.addEventListener('click', async () => {
        const bmId = card.dataset.bmid;
        card.style.opacity = '0.5';
        const res = await API.loadBenchmark(bmId);
        await refreshProjects(res.project_id);
        card.style.opacity = '1';
        switchTab('tab-bpmn');
      });
    });
  }

  // --- 5. Free-Text File Uploads ---
  fileActual.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    fileActualName.textContent = file.name;
    if (!activeProjectId) {
      const proj = await API.createProject(`Assessment: ${file.name}`);
      await refreshProjects(proj.id);
    }
    const res = await API.uploadDocument(activeProjectId, 'ACTUAL_PROCESS', file);
    inputActual.value = res.pii_report.has_pii ? res.pii_report.redacted_text : inputActual.value;
  });

  fileFormal.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    fileFormalName.textContent = file.name;
    if (!activeProjectId) {
      const proj = await API.createProject(`Assessment: ${file.name}`);
      await refreshProjects(proj.id);
    }
    const res = await API.uploadDocument(activeProjectId, 'FORMAL_POLICY', file);
    inputFormal.value = res.pii_report.has_pii ? res.pii_report.redacted_text : inputFormal.value;
  });

  // --- 6. Scientific BPMN Generation & Verification Loop ---
  btnGenerateBpmn.addEventListener('click', async () => {
    const processText = inputActual.value.trim();
    if (!processText) {
      alert('Please enter process notes or select an empirical benchmark case study.');
      return;
    }

    if (!activeProjectId) {
      const proj = await API.createProject('Process Assessment');
      await refreshProjects(proj.id);
    }

    btnGenerateBpmn.disabled = true;
    btnGenerateBpmn.textContent = 'Generating & Mathematically Verifying BPMN Graph...';

    try {
      const res = await API.generateBPMN(activeProjectId, processText, inputFormal.value.trim());
      if (bpmnStudio) {
        await bpmnStudio.loadXML(res.bpmn_xml);
      }

      // Display telemetry scorecard
      if (res.telemetry) {
        telemetryBar.classList.remove('hidden');
        tSoundness.textContent = `${Math.round(res.telemetry.structural_soundness_score * 100)}%`;
        tFaithfulness.textContent = `${Math.round(res.telemetry.faithfulness_score * 100)}%`;
        const defects = res.telemetry.structural_defects.length;
        tDetail.textContent = defects === 0 ? 'Mathematically sound. Zero structural deadlocks.' : `Warnings: ${res.telemetry.structural_defects[0]}`;
      }

      switchTab('tab-bpmn');
    } catch (err) {
      alert('Error generating BPMN: ' + err.message);
    } finally {
      btnGenerateBpmn.disabled = false;
      btnGenerateBpmn.textContent = 'Run Scientific BPMN Generation & Verification →';
    }
  });

  // --- 7. Paint-Style Box & Node Resizing (Point #7) ---
  function applyResize(w, h) {
    if (bpmnStudio && bpmnStudio.resizeSelectedElement(w, h)) {
      nodeWidthInput.value = w;
      nodeHeightInput.value = h;
    } else if (!bpmnStudio.selectedElement) {
      alert('Please click on a BPMN task or element on the canvas to select it first.');
    }
  }

  nodeWidthInput.addEventListener('change', () => applyResize(nodeWidthInput.value, nodeHeightInput.value));
  nodeHeightInput.addEventListener('change', () => applyResize(nodeWidthInput.value, nodeHeightInput.value));

  btnPresetCompact.addEventListener('click', () => applyResize(100, 60));
  btnPresetStandard.addEventListener('click', () => applyResize(130, 80));
  btnPresetWide.addEventListener('click', () => applyResize(180, 80));
  btnPresetLarge.addEventListener('click', () => applyResize(200, 110));

  btnSaveBpmn.addEventListener('click', async () => {
    if (!bpmnStudio || !activeProjectId) return;
    const xml = await bpmnStudio.exportXML();
    await API.saveBPMN(activeProjectId, xml);
    alert('BPMN 2.0 diagram saved as a new version.');
  });

  btnExportBpmn.addEventListener('click', async () => {
    if (!bpmnStudio) return;
    const xml = await bpmnStudio.exportXML();
    const blob = new Blob([xml], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `PMGA_Process_Model_${activeProjectId || 'export'}.bpmn`;
    a.click();
  });

  btnExportSvg.addEventListener('click', async () => {
    if (!bpmnStudio) return;
    const svg = await bpmnStudio.exportSVG();
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `PMGA_Process_Diagram_${activeProjectId || 'export'}.svg`;
    a.click();
  });

  btnProceedToIso.addEventListener('click', () => switchTab('tab-iso'));

  // --- 8. ISO Controls Explorer & Accessible Drawer (Screenshot #1 Fix) ---
  async function loadIsoControls() {
    const data = await API.getISOControls();
    allIsoControls = data.controls || [];
    renderIsoTable(allIsoControls);
  }

  function renderIsoTable(controls) {
    isoTbody.innerHTML = controls.map(c => `
      <tr>
        <td><span class="control-id-badge">${c.control_id}</span></td>
        <td><strong>${c.title}</strong></td>
        <td>${c.chapter}</td>
        <td>${c.requirement}</td>
        <td>
          <button class="pill-btn secondary btn-inspect-control" data-cid="${c.control_id}" style="padding: 4px 8px; font-size: 0.75rem;">Inspect Details</button>
        </td>
      </tr>
    `).join('');

    document.querySelectorAll('.btn-inspect-control').forEach(btn => {
      btn.addEventListener('click', () => openIsoDetailModal(btn.dataset.cid));
    });
  }

  function openIsoDetailModal(controlId) {
    const c = allIsoControls.find(x => x.control_id === controlId);
    if (!c) return;

    modalIsoBadge.textContent = c.control_id;
    modalIsoTitle.textContent = c.title;

    modalIsoBody.innerHTML = `
      <div class="iso-detail-card">
        <h5>Chapter &amp; Domain</h5>
        <p>${c.chapter} &bull; Type: ${c.type} &bull; Concept: ${c.concept}</p>
      </div>

      <div class="iso-detail-card">
        <h5>Plain English Requirement</h5>
        <p>${c.requirement}</p>
      </div>

      <div class="iso-detail-card">
        <h5>Sub-Controls Checklist</h5>
        <pre>${c.sub_controls || 'Standard baseline review'}</pre>
      </div>

      <div class="iso-detail-card">
        <h5>Typical Audit Evidence</h5>
        <p>${c.evidence || 'Documented SOPs, access tickets, or system logs'}</p>
      </div>

      <div class="iso-detail-card">
        <h5>Common Real-World Operational Gaps</h5>
        <p>${c.common_gaps || 'Informal workarounds without audit logging'}</p>
      </div>

      <div class="iso-detail-card">
        <h5>Roles to Consult</h5>
        <p>${c.consult_roles || 'Department Leads and Systems Administrators'}</p>
      </div>

      <div class="iso-detail-card highlight">
        <h5>Pragmatic Compromise / Compensating Controls</h5>
        <p>${c.compensating_controls || 'Deploy temporary monitored jump-hosts or time-boxed delegation'}</p>
      </div>
    `;

    modalIsoDetail.classList.add('open');
  }

  function closeIsoDetailModal() {
    modalIsoDetail.classList.remove('open');
  }

  btnCloseIsoDetail.addEventListener('click', closeIsoDetailModal);
  btnDismissIsoDetail.addEventListener('click', closeIsoDetailModal);

  // Keyboard accessibility: Close modals on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeIsoDetailModal();
      modalByok.classList.remove('open');
    }
  });

  filterIsoChapter.addEventListener('change', (e) => {
    const chap = e.target.value.toLowerCase();
    const filtered = allIsoControls.filter(c => !chap || c.chapter.toLowerCase().includes(chap));
    renderIsoTable(filtered);
  });

  filterIsoSearch.addEventListener('input', (e) => {
    const term = e.target.value.toLowerCase();
    const filtered = allIsoControls.filter(c => 
      c.control_id.toLowerCase().includes(term) ||
      c.title.toLowerCase().includes(term) ||
      c.requirement.toLowerCase().includes(term)
    );
    renderIsoTable(filtered);
  });

  // --- 9. Run Gap Analysis ---
  btnRunGapAnalysis.addEventListener('click', async () => {
    if (!activeProjectId) {
      alert('Please select an assessment project first.');
      return;
    }

    btnRunGapAnalysis.disabled = true;
    btnRunGapAnalysis.textContent = 'Evaluating Operational Alignment...';

    try {
      const res = await API.runGapAnalysis(activeProjectId);
      GapDashboard.render(res);
      switchTab('tab-gaps');
    } catch (err) {
      alert('Error running gap analysis: ' + err.message);
    } finally {
      btnRunGapAnalysis.disabled = false;
      btnRunGapAnalysis.textContent = 'Run Plain-English Gap Analysis →';
    }
  });

  // --- 10. Exports & GDPR Purge ---
  btnExportExcel.addEventListener('click', () => {
    if (!activeProjectId) return;
    window.location.href = `/api/v1/export/excel/${activeProjectId}`;
  });

  btnGdprPurge.addEventListener('click', async () => {
    if (!activeProjectId) return;
    const confirmed = confirm('GDPR Art. 17 Erasure:\nPermanently expunge this project and all data from database and disk?');
    if (!confirmed) return;

    await API.gdprPurge(activeProjectId);
    activeProjectId = null;
    inputActual.value = '';
    inputFormal.value = '';
    await refreshProjects();
    switchTab('tab-intake');
    alert('Project permanently expunged.');
  });

  // --- Initialization ---
  bpmnStudio = new BPMNStudio('bpmn-canvas');
  await updateLlmStatus();
  await refreshProjects();
  await loadBenchmarks();
  await loadIsoControls();
});
