/**
 * CivicProcess Main Application Orchestrator
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
  const inputGeminiKey = document.getElementById('input-gemini-key');
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

  const filterIsoChapter = document.getElementById('filter-iso-chapter');
  const filterIsoSearch = document.getElementById('filter-iso-search');
  const isoTbody = document.getElementById('iso-controls-tbody');
  const btnRunGapAnalysis = document.getElementById('btn-run-gap-analysis');

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

  // --- 2. BYOK Configuration ---
  function updateByokStatus() {
    const key = API.getApiKey();
    if (key) {
      byokStatusBtn.classList.remove('unconfigured');
      byokStatusBtn.classList.add('configured');
      byokLabel.textContent = 'Gemini BYOK: Active';
      inputGeminiKey.value = key;
    } else {
      byokStatusBtn.classList.remove('configured');
      byokStatusBtn.classList.add('unconfigured');
      byokLabel.textContent = 'Gemini BYOK: Unset';
    }
  }

  byokStatusBtn.addEventListener('click', () => modalByok.classList.add('open'));
  btnCloseByok.addEventListener('click', () => modalByok.classList.remove('open'));
  btnSaveByok.addEventListener('click', () => {
    const key = inputGeminiKey.value.trim();
    API.setApiKey(key, chkSaveLocal.checked);
    updateByokStatus();
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
    const title = prompt('Enter a title for this municipal assessment project:', 'Citizen Service & Identity Review');
    if (!title) return;
    const proj = await API.createProject(title);
    await refreshProjects(proj.id);
  });

  async function loadProjectData(projId) {
    const data = await API.getProject(projId);
    if (!data) return;

    // Populate inputs from documents
    const actualDoc = data.documents.find(d => d.doc_type === 'ACTUAL_PROCESS');
    const formalDoc = data.documents.find(d => d.doc_type === 'FORMAL_POLICY');

    if (actualDoc) inputActual.value = actualDoc.extracted_text;
    if (formalDoc) inputFormal.value = formalDoc.extracted_text;

    // Load BPMN model if available
    if (data.bpmn_model && bpmnStudio) {
      await bpmnStudio.loadXML(data.bpmn_model.bpmn_xml);
      document.getElementById('bpmn-canvas-title').textContent = data.bpmn_model.title || 'Process Diagram';
    }

    // Load gap findings if available
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

  // --- 4. Benchmarks Ingestion ---
  async function loadBenchmarks() {
    const bms = await API.getBenchmarks();
    benchmarksList.innerHTML = bms.map(b => `
      <div class="bm-card" data-bmid="${b.id}">
        <h4>${b.title}</h4>
        <p>${b.description}</p>
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

  // --- 5. File Uploads & PII Pre-flight ---
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
    if (res.pii_report.has_pii) {
      alert(`GDPR Notice: ${res.pii_report.detected_count} sensitive citizen data items detected and pre-redacted.`);
    }
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

  // --- 6. Generate BPMN ---
  btnGenerateBpmn.addEventListener('click', async () => {
    const processText = inputActual.value.trim();
    if (!processText) {
      alert('Please provide process notes or load a benchmark case study first.');
      return;
    }

    if (!activeProjectId) {
      const proj = await API.createProject('New Process Assessment');
      await refreshProjects(proj.id);
    }

    btnGenerateBpmn.disabled = true;
    btnGenerateBpmn.textContent = 'Generating BPMN 2.0 Graph & Coordinates...';

    try {
      const res = await API.generateBPMN(activeProjectId, processText, inputFormal.value.trim());
      if (bpmnStudio) {
        await bpmnStudio.loadXML(res.bpmn_xml);
      }
      switchTab('tab-bpmn');
    } catch (err) {
      alert('Error generating BPMN: ' + err.message);
    } finally {
      btnGenerateBpmn.disabled = false;
      btnGenerateBpmn.textContent = 'Generate Open BPMN 2.0 Model & Layout →';
    }
  });

  // --- 7. BPMN Studio Toolbar Actions ---
  btnSaveBpmn.addEventListener('click', async () => {
    if (!bpmnStudio || !activeProjectId) return;
    const xml = await bpmnStudio.exportXML();
    await API.saveBPMN(activeProjectId, xml);
    alert('BPMN 2.0 diagram saved as a new project version.');
  });

  btnExportBpmn.addEventListener('click', async () => {
    if (!bpmnStudio) return;
    const xml = await bpmnStudio.exportXML();
    const blob = new Blob([xml], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `process_model_${activeProjectId || 'export'}.bpmn`;
    a.click();
  });

  btnExportSvg.addEventListener('click', async () => {
    if (!bpmnStudio) return;
    const svg = await bpmnStudio.exportSVG();
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `process_diagram_${activeProjectId || 'export'}.svg`;
    a.click();
  });

  btnProceedToIso.addEventListener('click', () => switchTab('tab-iso'));

  // --- 8. ISO Controls Catalog ---
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
      btn.addEventListener('click', () => {
        const c = allIsoControls.find(x => x.control_id === btn.dataset.cid);
        if (c) {
          alert(`Control: ${c.control_id} - ${c.title}\n\nTypical Municipal Evidence:\n${c.evidence}\n\nCommon Real-World Gaps:\n${c.common_gaps}\n\nPragmatic Compromise / Compensating Controls:\n${c.compensating_controls}`);
        }
      });
    });
  }

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
      alert('Please select or create an assessment project first.');
      return;
    }

    btnRunGapAnalysis.disabled = true;
    btnRunGapAnalysis.textContent = 'Analyzing Lived Reality vs ISO Baseline...';

    try {
      const res = await API.runGapAnalysis(activeProjectId);
      GapDashboard.render(res);
      switchTab('tab-gaps');
    } catch (err) {
      alert('Error analyzing gaps: ' + err.message);
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
    const confirmed = confirm('GDPR Art. 17 Complete Erasure:\nThis will permanently expunge this project, all uploaded documents, BPMN models, and gap assessments from server disk and database.\n\nAre you sure?');
    if (!confirmed) return;

    await API.gdprPurge(activeProjectId);
    activeProjectId = null;
    inputActual.value = '';
    inputFormal.value = '';
    await refreshProjects();
    switchTab('tab-intake');
    alert('Project data permanently expunged under GDPR principles.');
  });

  // --- Initialization ---
  bpmnStudio = new BPMNStudio('bpmn-canvas');
  updateByokStatus();
  await refreshProjects();
  await loadBenchmarks();
  await loadIsoControls();
});
