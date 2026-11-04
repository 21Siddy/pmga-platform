/**
 * PMGA Main Application Orchestrator
 * Pure vanilla JavaScript — Swiss editorial, non-blocking, accessible.
 */

document.addEventListener('DOMContentLoaded', async () => {
  let activeProjectId = null;
  let bpmnStudio = null;
  let surveyPreviewStudio = null;
  let allIsoControls = [];
  let currentSurvey = null;
  let focusedRowId = null;
  let activeQuestionCode = null;
  let cachedCorpusManifest = [];
  let gdprPendingTimeout = null;

  // --- UI Elements ---
  const tabs = document.querySelectorAll('.tab-btn');
  const panes = document.querySelectorAll('.tab-pane');
  const projectSelect = document.getElementById('select-active-project');
  const btnNewProjectToggle = document.getElementById('btn-new-project-toggle');
  const newProjectInline = document.getElementById('new-project-inline');
  const inputNewProjectTitle = document.getElementById('input-new-project-title');
  const btnCreateProjectConfirm = document.getElementById('btn-create-project-confirm');
  const btnCreateProjectCancel = document.getElementById('btn-create-project-cancel');

  const btnHeaderExportExcel = document.getElementById('btn-header-export-excel');
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

  // Status Toast
  const statusToast = document.getElementById('status-toast');
  const toastMessage = document.getElementById('toast-message');
  const toastDismiss = document.getElementById('toast-dismiss');
  let toastTimer = null;

  // Benchmarks & Corpus
  const benchmarksList = document.getElementById('benchmarks-list');
  const selectCorpusModel = document.getElementById('select-corpus-model');
  const selectCorpusStyle = document.getElementById('select-corpus-style');
  const btnLoadCorpusStyle = document.getElementById('btn-load-corpus-style');
  const btnLoadCorpusBpmn = document.getElementById('btn-load-corpus-bpmn');
  const corpusModelInfo = document.getElementById('corpus-model-info');
  const corpusArchetypeTag = document.getElementById('corpus-archetype-tag');
  const corpusRefTag = document.getElementById('corpus-ref-tag');
  const corpusNodesCount = document.getElementById('corpus-nodes-count');
  const corpusEdgesCount = document.getElementById('corpus-edges-count');

  // Intake & Upload
  const inputActual = document.getElementById('input-actual-process');
  const inputFormal = document.getElementById('input-formal-policy');
  const fileActual = document.getElementById('file-actual-process');
  const fileFormal = document.getElementById('file-formal-policy');
  const fileActualName = document.getElementById('file-actual-name');
  const fileFormalName = document.getElementById('file-formal-name');
  const btnGenerateBpmn = document.getElementById('btn-generate-bpmn');

  // Survey Workspace Elements
  const surveyWorkspace = document.getElementById('survey-workspace');
  const surveyProgressBar = document.getElementById('survey-progress-bar');
  const surveyProgressText = document.getElementById('survey-progress-text');
  const surveyProgressLabel = document.getElementById('survey-progress-label');
  const activeQuestionCard = document.getElementById('active-question-card');
  const qPhaseBadge = document.getElementById('q-phase-badge');
  const qRowBadge = document.getElementById('q-row-badge');
  const qTitleText = document.getElementById('q-title-text');
  const qChipsContainer = document.getElementById('q-chips-container');
  const qChipsList = document.getElementById('q-chips-list');
  const qAnswerInput = document.getElementById('q-answer-input');
  const chkIsRepeatable = document.getElementById('chk-is-repeatable');
  const btnSaveSurveyAnswer = document.getElementById('btn-save-survey-answer');
  const btnSkipSurveyQuestion = document.getElementById('btn-skip-survey-question');
  const surveyRowsTbody = document.getElementById('survey-rows-tbody');
  const btnOpenFullStudio = document.getElementById('btn-open-full-studio');

  // Tab 2 BPMN Studio Controls
  const btnSaveBpmn = document.getElementById('btn-save-bpmn');
  const btnExportBpmn = document.getElementById('btn-export-bpmn');
  const btnExportSvg = document.getElementById('btn-export-svg');
  const btnProceedToIso = document.getElementById('btn-proceed-to-iso');
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

  // Tab 3 ISO Controls & Gap Report
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

  // ============================================================================
  // NON-BLOCKING TOAST NOTIFICATIONS (Rule #4)
  // ============================================================================
  function showToast(message, type = 'info', durationMs = 4500) {
    if (toastTimer) clearTimeout(toastTimer);
    toastMessage.textContent = message;
    statusToast.className = `status-toast toast-${type}`;
    statusToast.classList.remove('hidden');

    if (durationMs > 0) {
      toastTimer = setTimeout(() => {
        statusToast.classList.add('hidden');
      }, durationMs);
    }
  }

  toastDismiss.addEventListener('click', () => {
    if (toastTimer) clearTimeout(toastTimer);
    statusToast.classList.add('hidden');
  });

  // ============================================================================
  // TAB NAVIGATION
  // ============================================================================
  function switchTab(tabId) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
    panes.forEach(p => p.classList.toggle('active', p.id === tabId));

    if (tabId === 'tab-bpmn' && bpmnStudio && bpmnStudio.modeler) {
      setTimeout(() => {
        try {
          const canvas = bpmnStudio.modeler.get('canvas');
          if (canvas) {
            canvas.resized();
            canvas.zoom('fit-viewport');
          }
        } catch (e) {
          console.warn('Canvas zoom on tab switch:', e);
        }
      }, 50);
    }

    if (tabId === 'tab-intake' && surveyPreviewStudio && surveyPreviewStudio.modeler) {
      setTimeout(() => {
        try {
          const canvas = surveyPreviewStudio.modeler.get('canvas');
          if (canvas) {
            canvas.resized();
            canvas.zoom('fit-viewport');
          }
        } catch (e) {
          console.warn('Preview canvas zoom on tab switch:', e);
        }
      }, 50);
    }
  }

  tabs.forEach(t => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });

  btnOpenFullStudio.addEventListener('click', () => switchTab('tab-bpmn'));

  // ============================================================================
  // LLM STATUS & MODAL ROUTING
  // ============================================================================
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
          byokLabel.textContent = 'Gemini (3.8-flash): Ready';
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
    showToast('LLM configuration saved and activated.', 'success');
  });

  // ============================================================================
  // PROJECT CREATION & SELECTION (Inline, No window.prompt)
  // ============================================================================
  btnNewProjectToggle.addEventListener('click', () => {
    const isHidden = newProjectInline.classList.contains('hidden');
    if (isHidden) {
      newProjectInline.classList.remove('hidden');
      inputNewProjectTitle.focus();
    } else {
      newProjectInline.classList.add('hidden');
    }
  });

  btnCreateProjectCancel.addEventListener('click', () => {
    newProjectInline.classList.add('hidden');
    inputNewProjectTitle.value = '';
  });

  async function handleInlineProjectCreate() {
    const title = inputNewProjectTitle.value.trim();
    if (!title) {
      showToast('Please enter an assessment title.', 'error');
      inputNewProjectTitle.focus();
      return;
    }
    try {
      const proj = await API.createProject(title);
      newProjectInline.classList.add('hidden');
      inputNewProjectTitle.value = '';
      await refreshProjects(proj.id);
      showToast(`Assessment created: ${title}`, 'success');
    } catch (err) {
      showToast('Failed to create project: ' + err.message, 'error');
    }
  }

  btnCreateProjectConfirm.addEventListener('click', handleInlineProjectCreate);
  inputNewProjectTitle.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleInlineProjectCreate();
    if (e.key === 'Escape') {
      newProjectInline.classList.add('hidden');
      inputNewProjectTitle.value = '';
    }
  });

  async function refreshProjects(selectNewId = null) {
    try {
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
    } catch (err) {
      console.warn('Failed to load project list:', err);
    }
  }

  projectSelect.addEventListener('change', async (e) => {
    activeProjectId = e.target.value;
    if (activeProjectId) {
      await loadProjectData(activeProjectId);
    }
  });

  async function loadProjectData(projId) {
    try {
      const data = await API.getProject(projId);
      if (!data) return;

      const actualDoc = data.documents.find(d => d.doc_type === 'ACTUAL_PROCESS');
      const formalDoc = data.documents.find(d => d.doc_type === 'FORMAL_POLICY');

      if (actualDoc) inputActual.value = actualDoc.extracted_text;
      if (formalDoc) inputFormal.value = formalDoc.extracted_text;

      if (data.bpmn_model) {
        if (bpmnStudio) {
          try {
            await bpmnStudio.loadXML(data.bpmn_model.bpmn_xml);
            document.getElementById('bpmn-canvas-title').textContent = data.bpmn_model.title || 'Process Diagram';
          } catch (err) {
            console.warn('Background BPMN load deferred:', err);
          }
        }
        if (surveyPreviewStudio) {
          try {
            await surveyPreviewStudio.loadXML(data.bpmn_model.bpmn_xml);
          } catch (err) {
            console.warn('Survey preview BPMN load deferred:', err);
          }
        }
      }

      // Load survey rows and questions
      await loadSurvey(projId);

      // Load gaps if available
      if (data.gaps && data.gaps.length > 0) {
        const findings = data.gaps.map(g => ({
          ...g,
          actionable_todos: JSON.parse(g.actionable_todos_json || '[]'),
          roles_to_consult: JSON.parse(g.roles_to_consult_json || '[]'),
          compensating_controls: JSON.parse(g.compensating_controls_json || '[]')
        }));
        GapDashboard.render({ findings, compliance_score_percent: 75, executive_summary: 'Loaded previous assessment findings.' });
      }
    } catch (err) {
      showToast('Error loading project: ' + err.message, 'error');
    }
  }

  // ============================================================================
  // VERBATIM SQL SURVEY & PRELEAD CONCEPT INTERACTION
  // ============================================================================
  async function loadSurvey(projId) {
    if (!projId) return;
    try {
      currentSurvey = await API.getSurvey(projId);
      renderSurveyState(currentSurvey);
    } catch (err) {
      console.warn('Survey state fetch deferred:', err);
    }
  }

  function renderSurveyState(survey) {
    if (!survey) return;

    // 1. Progress Bar
    const progress = survey.progress || { total_slots: 0, answered_slots: 0, percent: 0, rows_count: 0 };
    surveyProgressBar.style.width = `${progress.percent}%`;
    surveyProgressText.textContent = `${progress.percent}% Complete (${progress.answered_slots}/${progress.total_slots} slots)`;
    surveyProgressLabel.textContent = `${progress.rows_count} item${progress.rows_count === 1 ? '' : 's'} recorded in SQLite survey table`;

    // 2. Table Rows
    const rows = survey.rows || [];
    if (rows.length === 0) {
      surveyRowsTbody.innerHTML = '<tr><td colspan="5" class="empty-state">No rows ingested yet. Drop an Excel file or paste process notes above.</td></tr>';
    } else {
      surveyRowsTbody.innerHTML = rows.map((r, idx) => {
        const isFocused = focusedRowId === r.id;
        const kindBadge = `<span class="row-badge">${r.q01_entry_kind || 'system'}</span>`;
        const procName = r.q02_process_name || '<em style="color:var(--ink-muted)">Unlinked</em>';
        return `
          <tr class="clickable-row ${isFocused ? 'row-focused' : ''}" data-rowid="${r.id}">
            <td><strong>#${idx + 1}</strong></td>
            <td><strong>${escapeHtml(r.raw_entry_text)}</strong></td>
            <td>${kindBadge}</td>
            <td>${escapeHtml(procName)}</td>
            <td><span class="phase-badge">${r.answered_count || 0}/14</span></td>
          </tr>
        `;
      }).join('');

      // Row click listener to focus questions on that specific entry
      surveyRowsTbody.querySelectorAll('.clickable-row').forEach(rowEl => {
        rowEl.addEventListener('click', () => {
          focusedRowId = parseInt(rowEl.dataset.rowid, 10);
          renderSurveyState(currentSurvey);
        });
      });
    }

    // 3. Active Question Card
    renderActiveQuestionCard(survey);
  }

  function renderActiveQuestionCard(survey) {
    const rows = survey.rows || [];
    if (rows.length === 0) {
      qPhaseBadge.textContent = 'Phase: Standby';
      qRowBadge.textContent = 'No Entries';
      qTitleText.textContent = 'Upload or paste process evidence above to start the guided discovery interview.';
      qChipsContainer.classList.add('hidden');
      qAnswerInput.value = '';
      qAnswerInput.disabled = true;
      btnSaveSurveyAnswer.disabled = true;
      btnSkipSurveyQuestion.disabled = true;
      return;
    }

    qAnswerInput.disabled = false;
    btnSaveSurveyAnswer.disabled = false;
    btnSkipSurveyQuestion.disabled = false;

    // Pick active question: if a row is explicitly focused, find its first unanswered question
    let activeQuestion = null;
    let focusedRow = null;

    if (focusedRowId) {
      focusedRow = rows.find(r => r.id === focusedRowId);
    }
    if (!focusedRow && rows.length > 0) {
      focusedRow = rows[0];
      focusedRowId = focusedRow.id;
    }

    // If global next_question matches or exists
    if (survey.next_question && (!focusedRowId || survey.next_question.row_id === focusedRowId)) {
      activeQuestion = survey.next_question;
    } else if (focusedRow) {
      // Find first null slot for this focused row
      const candidateCodes = [
        'q02_process_name', 'q03_trigger_event', 'q04_inputs_needed',
        'q05_step_sequence', 'q06_actor_role', 'q07_systems_used',
        'q08_data_created', 'q09_completion_state', 'q10_handover_target',
        'q11_can_fail', 'q12_fallback_procedure', 'q13_is_repeatable',
        'q14_raw_criticality'
      ];
      for (const code of candidateCodes) {
        if (!focusedRow[code]) {
          activeQuestion = {
            row_id: focusedRow.id,
            raw_entry: focusedRow.raw_entry_text,
            code: code,
            phase: 'Prozesserhebung & Struktur',
            text: `Please specify ${code.replace(/^q\d+_/, '').replace(/_/g, ' ')} for '${focusedRow.raw_entry_text}'.`
          };
          break;
        }
      }
    }

    if (!activeQuestion) {
      // All questions for this row or dataset answered
      qPhaseBadge.textContent = 'Phase: Discovery Complete';
      qRowBadge.textContent = focusedRow ? escapeHtml(focusedRow.raw_entry_text) : 'All Complete';
      qTitleText.textContent = `All initial survey questions answered for '${focusedRow ? focusedRow.raw_entry_text : 'this entry'}'. You can review the live diagram or export to Excel.`;
      qChipsContainer.classList.add('hidden');
      qAnswerInput.value = '';
      activeQuestionCode = null;
      return;
    }

    activeQuestionCode = activeQuestion.code;
    focusedRowId = activeQuestion.row_id;
    qPhaseBadge.textContent = `Phase: ${activeQuestion.phase || 'Prozesserhebung'}`;
    qRowBadge.textContent = `Focused: ${activeQuestion.raw_entry || 'Entry'}`;
    qTitleText.textContent = activeQuestion.text;

    // Render Prelead Concepts Chips
    const preleadList = (survey.prelead_concepts && survey.prelead_concepts[activeQuestion.code]) || [];
    if (preleadList.length > 0) {
      qChipsContainer.classList.remove('hidden');
      qChipsList.innerHTML = preleadList.map(concept => `
        <button type="button" class="chip-btn" data-val="${escapeHtml(concept)}">${escapeHtml(concept)}</button>
      `).join('');

      qChipsList.querySelectorAll('.chip-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          qAnswerInput.value = btn.dataset.val;
          qAnswerInput.focus();
        });
      });
    } else {
      qChipsContainer.classList.add('hidden');
      qChipsList.innerHTML = '';
    }

    // Set input placeholder
    qAnswerInput.value = '';
    qAnswerInput.placeholder = `Type verbatim answer for '${activeQuestion.raw_entry}'...`;
    chkIsRepeatable.checked = false;
    qAnswerInput.focus();
  }

  // Save Survey Answer
  btnSaveSurveyAnswer.addEventListener('click', async () => {
    if (!activeProjectId || !focusedRowId || !activeQuestionCode) {
      showToast('No active question selected.', 'info');
      return;
    }

    let answer = qAnswerInput.value.trim();
    if (!answer) {
      showToast('Please type an answer or click Skip.', 'info');
      qAnswerInput.focus();
      return;
    }

    // If repeatable checkbox checked and answering q13
    if (chkIsRepeatable.checked && activeQuestionCode === 'q13_is_repeatable') {
      answer = '1';
    }

    btnSaveSurveyAnswer.disabled = true;
    btnSaveSurveyAnswer.textContent = 'Saving...';

    try {
      const res = await API.answerSurveyQuestion(activeProjectId, focusedRowId, activeQuestionCode, answer, true);

      // If repeatable checkbox was checked regardless of current question, update q13 too
      if (chkIsRepeatable.checked && activeQuestionCode !== 'q13_is_repeatable') {
        await API.answerSurveyQuestion(activeProjectId, focusedRowId, 'q13_is_repeatable', '1', false);
      }

      // Update both canvases if live BPMN XML returned
      if (res.bpmn_xml) {
        if (surveyPreviewStudio) {
          try { await surveyPreviewStudio.loadXML(res.bpmn_xml); } catch (e) { console.warn(e); }
        }
        if (bpmnStudio) {
          try { await bpmnStudio.loadXML(res.bpmn_xml); } catch (e) { console.warn(e); }
        }
      }

      await loadSurvey(activeProjectId);
      showToast('Answer recorded verbatim. Diagram updated.', 'success', 2500);
    } catch (err) {
      showToast('Failed to record answer: ' + err.message, 'error');
    } finally {
      btnSaveSurveyAnswer.disabled = false;
      btnSaveSurveyAnswer.textContent = 'Save & Next Question →';
    }
  });

  // Skip Question (Non-blocking cycle)
  btnSkipSurveyQuestion.addEventListener('click', () => {
    if (!currentSurvey || !currentSurvey.rows || currentSurvey.rows.length === 0) return;
    const rows = currentSurvey.rows;
    const currentIndex = rows.findIndex(r => r.id === focusedRowId);
    const nextIndex = (currentIndex + 1) % rows.length;
    focusedRowId = rows[nextIndex].id;
    renderSurveyState(currentSurvey);
    showToast('Skipped to next item.', 'info', 1800);
  });

  // Enter key on answer input triggers save
  qAnswerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      btnSaveSurveyAnswer.click();
    }
  });

  // ============================================================================
  // FILE UPLOADS & INGESTION (Multi-sheet Excel, docx, pdf, bpmn)
  // ============================================================================
  fileActual.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    fileActualName.textContent = file.name;

    if (!activeProjectId) {
      const proj = await API.createProject(`Assessment: ${file.name}`);
      await refreshProjects(proj.id);
    }

    if (file.name.endsWith('.bpmn') || file.name.endsWith('.xml')) {
      const reader = new FileReader();
      reader.onload = async (ev) => {
        const xmlText = ev.target.result;
        try {
          const res = await API.parseExternalBPMN(xmlText, file.name);
          inputActual.value = res.text_styles.shorthand || res.text_styles.formal;
          await API.saveBPMN(activeProjectId, xmlText);
          if (bpmnStudio) await bpmnStudio.loadXML(xmlText);
          if (surveyPreviewStudio) await surveyPreviewStudio.loadXML(xmlText);
          document.getElementById('bpmn-canvas-title').textContent = `${res.title} (Parsed from .bpmn)`;
          await loadSurvey(activeProjectId);
          showToast('BPMN file parsed and loaded.', 'success');
        } catch (err) {
          showToast('Failed to parse uploaded BPMN XML: ' + err.message, 'error');
        }
      };
      reader.readAsText(file);
      return;
    }

    showToast(`Uploading and extracting '${file.name}'...`, 'info', 2000);
    try {
      const res = await API.uploadDocument(activeProjectId, 'ACTUAL_PROCESS', file);
      inputActual.value = res.pii_report.has_pii ? res.pii_report.redacted_text : inputActual.value;
      await loadSurvey(activeProjectId);
      showToast(`'${file.name}' ingested. Verbatim survey rows generated.`, 'success');
    } catch (err) {
      showToast('Upload error: ' + err.message, 'error');
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

    try {
      const res = await API.uploadDocument(activeProjectId, 'FORMAL_POLICY', file);
      inputFormal.value = res.pii_report.has_pii ? res.pii_report.redacted_text : inputFormal.value;
      showToast(`Directive '${file.name}' ingested for compliance context.`, 'success');
    } catch (err) {
      showToast('Upload error: ' + err.message, 'error');
    }
  });

  // ============================================================================
  // BPMN GENERATION & VERIFICATION PIPELINE
  // ============================================================================
  btnGenerateBpmn.addEventListener('click', async () => {
    const processText = inputActual.value.trim();
    if (!processText) {
      showToast('Please enter process notes, drop an Excel file, or pick a reference benchmark.', 'error');
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
      if (surveyPreviewStudio) {
        await surveyPreviewStudio.loadXML(res.bpmn_xml);
      }

      // Display telemetry scorecard
      if (res.telemetry) {
        telemetryBar.classList.remove('hidden');
        tSoundness.textContent = `${Math.round(res.telemetry.structural_soundness_score * 100)}%`;
        tFaithfulness.textContent = `${Math.round(res.telemetry.faithfulness_score * 100)}%`;
        const defects = res.telemetry.structural_defects.length;
        tDetail.textContent = defects === 0 ? 'Mathematically sound. Zero structural deadlocks.' : `Warnings: ${res.telemetry.structural_defects[0]}`;
      }

      await loadSurvey(activeProjectId);
      showToast('BPMN diagram generated and verified.', 'success');
    } catch (err) {
      showToast('Error generating BPMN: ' + err.message, 'error');
    } finally {
      btnGenerateBpmn.disabled = false;
      btnGenerateBpmn.textContent = 'Generate Diagram & Start Guided Survey →';
    }
  });

  // ============================================================================
  // BENCHMARKS & CORPUS WORKBENCH
  // ============================================================================
  async function loadBenchmarks() {
    try {
      const bms = await API.getBenchmarks();
      benchmarksList.innerHTML = bms.map(b => `
        <div class="bm-card" data-bmid="${b.id}">
          <h4>${escapeHtml(b.title)}</h4>
          <p>${escapeHtml(b.description)}</p>
          <div class="bm-meta">Ref: ${escapeHtml(b.reference_standard || 'ISO/IEC 27002')}</div>
          <div class="bm-load-btn">Load Case Study &rarr;</div>
        </div>
      `).join('');

      document.querySelectorAll('.bm-card').forEach(card => {
        card.addEventListener('click', async () => {
          const bmId = card.dataset.bmid;
          card.style.opacity = '0.5';
          try {
            const res = await API.loadBenchmark(bmId);
            await refreshProjects(res.project_id);
            showToast('Benchmark case study loaded into assessment.', 'success');
          } catch (err) {
            showToast('Failed to load benchmark: ' + err.message, 'error');
          } finally {
            card.style.opacity = '1';
          }
        });
      });
    } catch (err) {
      console.warn('Could not load benchmarks:', err);
    }
  }

  async function loadCorpusWorkbench() {
    try {
      cachedCorpusManifest = await API.getCorpusManifest();
      selectCorpusModel.innerHTML = '<option value="">-- Choose Ground-Truth Archetype (12 Models) --</option>' +
        cachedCorpusManifest.map(m => `<option value="${m.id}">[${m.archetype}] ${escapeHtml(m.title)}</option>`).join('');

      selectCorpusModel.addEventListener('change', () => {
        const m = cachedCorpusManifest.find(item => item.id === selectCorpusModel.value);
        if (m) {
          corpusArchetypeTag.textContent = m.archetype;
          corpusRefTag.textContent = m.reference;
          corpusNodesCount.textContent = m.node_count;
          corpusEdgesCount.textContent = m.edge_count;
          corpusModelInfo.style.display = 'block';
        } else {
          corpusModelInfo.style.display = 'none';
        }
      });

      btnLoadCorpusStyle.addEventListener('click', async () => {
        const modelId = selectCorpusModel.value;
        if (!modelId) {
          showToast('Please select a Ground-Truth Model from the dropdown first.', 'error');
          return;
        }
        const styleKey = selectCorpusStyle.value;
        btnLoadCorpusStyle.textContent = 'Loading...';
        try {
          const detail = await API.getCorpusModel(modelId);
          const styleText = detail.text_styles[styleKey] || detail.text_styles.formal;
          inputActual.value = styleText;
          inputFormal.value = `# FORMAL REFERENCE STANDARD (${detail.title})\nStandard / Regulation: ${detail.reference}\nArchetype: ${detail.archetype}\nGround-Truth Element Constraints: Exactly ${detail.graph.nodes.length} nodes, ${detail.graph.edges.length} edges.`;
          showToast(`Loaded ${styleKey} linguistic style for ${detail.title}.`, 'success');
        } catch (e) {
          showToast('Failed to load model style: ' + e.message, 'error');
        } finally {
          btnLoadCorpusStyle.textContent = 'Load Style into Notes';
        }
      });

      btnLoadCorpusBpmn.addEventListener('click', async () => {
        const modelId = selectCorpusModel.value;
        if (!modelId) {
          showToast('Please select a Ground-Truth Model from the dropdown first.', 'error');
          return;
        }
        btnLoadCorpusBpmn.textContent = 'Loading Canvas...';
        try {
          const detail = await API.getCorpusModel(modelId);
          if (!activeProjectId) {
            const proj = await API.createProject(`Corpus Ground-Truth: ${detail.title}`);
            await refreshProjects(proj.id);
          }
          await API.saveBPMN(activeProjectId, detail.bpmn_xml);
          if (bpmnStudio) await bpmnStudio.loadXML(detail.bpmn_xml);
          if (surveyPreviewStudio) await surveyPreviewStudio.loadXML(detail.bpmn_xml);
          document.getElementById('bpmn-canvas-title').textContent = `${detail.title} (Ground Truth G*)`;
          switchTab('tab-bpmn');
          showToast('Ground-truth BPMN loaded to Studio.', 'success');
        } catch (e) {
          showToast('Failed to load Ground-Truth BPMN: ' + e.message, 'error');
        } finally {
          btnLoadCorpusBpmn.textContent = 'View Ground-Truth BPMN →';
        }
      });
    } catch (e) {
      console.warn('Failed to load corpus manifest:', e);
    }
  }

  // ============================================================================
  // STUDIO TOOLS (Box Resizing & Exports)
  // ============================================================================
  function applyResize(w, h) {
    if (bpmnStudio && bpmnStudio.resizeSelectedElement(w, h)) {
      nodeWidthInput.value = w;
      nodeHeightInput.value = h;
      showToast(`Resized element to ${w}x${h} px.`, 'info', 1500);
    } else if (!bpmnStudio.selectedElement) {
      showToast('Click a task or element on the canvas to select it first.', 'info');
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
    showToast('BPMN 2.0 diagram saved as a new version.', 'success');
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
    showToast('Downloaded .bpmn XML.', 'info', 2000);
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
    showToast('Downloaded .svg diagram.', 'info', 2000);
  });

  btnProceedToIso.addEventListener('click', () => switchTab('tab-iso'));

  // ============================================================================
  // ISO CONTROLS & GAP ANALYSIS
  // ============================================================================
  async function loadIsoControls() {
    try {
      const data = await API.getISOControls();
      allIsoControls = data.controls || [];
      renderIsoTable(allIsoControls);
    } catch (err) {
      console.warn('Failed to load ISO controls:', err);
    }
  }

  function renderIsoTable(controls) {
    isoTbody.innerHTML = controls.map(c => `
      <tr>
        <td><span class="control-id-badge">${escapeHtml(c.control_id)}</span></td>
        <td><strong>${escapeHtml(c.title)}</strong></td>
        <td>${escapeHtml(c.chapter)}</td>
        <td>${escapeHtml(c.requirement)}</td>
        <td>
          <button class="pill-btn secondary btn-inspect-control" data-cid="${c.control_id}" style="padding: 4px 8px; font-size: 0.75rem;">Inspect Details</button>
        </td>
      </tr>
    `).join('');

    isoTbody.querySelectorAll('.btn-inspect-control').forEach(btn => {
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
        <p>${escapeHtml(c.chapter)} &bull; Type: ${escapeHtml(c.type)} &bull; Concept: ${escapeHtml(c.concept)}</p>
      </div>
      <div class="iso-detail-card">
        <h5>Plain English Requirement</h5>
        <p>${escapeHtml(c.requirement)}</p>
      </div>
      <div class="iso-detail-card">
        <h5>Sub-Controls Checklist</h5>
        <pre>${escapeHtml(c.sub_controls || 'Standard baseline review')}</pre>
      </div>
      <div class="iso-detail-card">
        <h5>Typical Audit Evidence</h5>
        <p>${escapeHtml(c.evidence || 'Documented SOPs, access tickets, or system logs')}</p>
      </div>
      <div class="iso-detail-card">
        <h5>Common Real-World Operational Gaps</h5>
        <p>${escapeHtml(c.common_gaps || 'Informal workarounds without audit logging')}</p>
      </div>
      <div class="iso-detail-card">
        <h5>Roles to Consult</h5>
        <p>${escapeHtml(c.consult_roles || 'Department Leads and Systems Administrators')}</p>
      </div>
      <div class="iso-detail-card highlight">
        <h5>Pragmatic Compromise / Compensating Controls</h5>
        <p>${escapeHtml(c.compensating_controls || 'Deploy temporary monitored jump-hosts or time-boxed delegation')}</p>
      </div>
    `;

    modalIsoDetail.classList.add('open');
  }

  function closeIsoDetailModal() {
    modalIsoDetail.classList.remove('open');
  }

  btnCloseIsoDetail.addEventListener('click', closeIsoDetailModal);
  btnDismissIsoDetail.addEventListener('click', closeIsoDetailModal);

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

  btnRunGapAnalysis.addEventListener('click', async () => {
    if (!activeProjectId) {
      showToast('Please select an assessment project first.', 'error');
      return;
    }

    btnRunGapAnalysis.disabled = true;
    btnRunGapAnalysis.textContent = 'Evaluating Operational Alignment...';

    try {
      const res = await API.runGapAnalysis(activeProjectId);
      GapDashboard.render(res);
      showToast('Gap analysis completed against 93 ISO 27002 controls.', 'success');
    } catch (err) {
      showToast('Error running gap analysis: ' + err.message, 'error');
    } finally {
      btnRunGapAnalysis.disabled = false;
      btnRunGapAnalysis.textContent = 'Run Plain-English Gap Analysis →';
    }
  });

  // ============================================================================
  // EXPORTS & NON-BLOCKING GDPR PURGE
  // ============================================================================
  function triggerExcelDownload() {
    if (!activeProjectId) {
      showToast('Please select or create an assessment first.', 'error');
      return;
    }
    showToast('Preparing multi-sheet Excel workbook...', 'info', 2000);
    window.location.href = `/api/v1/export/excel/${activeProjectId}`;
  }

  btnHeaderExportExcel.addEventListener('click', triggerExcelDownload);
  btnExportExcel.addEventListener('click', triggerExcelDownload);

  // Non-blocking 2-step confirmation pattern (Replaces window.confirm)
  btnGdprPurge.addEventListener('click', async () => {
    if (!activeProjectId) {
      showToast('No active project to purge.', 'info');
      return;
    }

    if (!btnGdprPurge.dataset.confirming) {
      // First click: prompt state
      btnGdprPurge.dataset.confirming = 'true';
      btnGdprPurge.textContent = 'Confirm GDPR Purge? (Click to Erase)';
      btnGdprPurge.style.backgroundColor = '#991B1B';

      gdprPendingTimeout = setTimeout(() => {
        delete btnGdprPurge.dataset.confirming;
        btnGdprPurge.textContent = 'GDPR 1-Click Project Purge';
        btnGdprPurge.style.backgroundColor = '';
      }, 5000);
      return;
    }

    // Second click: execute purge
    clearTimeout(gdprPendingTimeout);
    delete btnGdprPurge.dataset.confirming;
    btnGdprPurge.textContent = 'Purging...';

    try {
      await API.gdprPurge(activeProjectId);
      activeProjectId = null;
      inputActual.value = '';
      inputFormal.value = '';
      await refreshProjects();
      switchTab('tab-intake');
      showToast('Project and all evidence permanently expunged under GDPR Art. 17.', 'info', 5000);
    } catch (err) {
      showToast('Failed to purge project: ' + err.message, 'error');
    } finally {
      btnGdprPurge.textContent = 'GDPR 1-Click Project Purge';
      btnGdprPurge.style.backgroundColor = '';
    }
  });

  // Utility to escape untrusted HTML
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ============================================================================
  // INITIALIZATION
  // ============================================================================
  bpmnStudio = new BPMNStudio('bpmn-canvas');
  surveyPreviewStudio = new BPMNStudio('survey-preview-canvas');

  await updateLlmStatus();
  await refreshProjects();
  await loadBenchmarks();
  await loadCorpusWorkbench();
  await loadIsoControls();
});
