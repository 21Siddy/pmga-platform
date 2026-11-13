/**
 * PMGA API Client
 * Supports multi-provider BYOK (Gemini, OpenAI, Anthropic, Mistral, Azure, University gateway).
 * Keys are held in memory / sessionStorage for the current run only — never written to disk.
 */

const API = {
  _runtimeKey: '',

  getApiKey() {
    return this._runtimeKey || sessionStorage.getItem('pmga_llm_key') || '';
  },

  setApiKey(key, keepInSession = true) {
    const clean = (key || '').trim();
    this._runtimeKey = clean;
    localStorage.removeItem('pmga_gemini_key');
    if (keepInSession && clean) {
      sessionStorage.setItem('pmga_llm_key', clean);
    } else {
      sessionStorage.removeItem('pmga_llm_key');
    }
  },

  getProvider() {
    return sessionStorage.getItem('pmga_llm_provider') || localStorage.getItem('pmga_llm_provider') || 'gemini';
  },

  setProvider(provider) {
    sessionStorage.setItem('pmga_llm_provider', provider);
    localStorage.setItem('pmga_llm_provider', provider);
  },

  getModelName() {
    return sessionStorage.getItem('pmga_llm_model') || '';
  },

  setModelName(modelName) {
    const clean = (modelName || '').trim();
    if (clean) sessionStorage.setItem('pmga_llm_model', clean);
    else sessionStorage.removeItem('pmga_llm_model');
  },

  getBaseUrl() {
    return sessionStorage.getItem('pmga_llm_base_url') || '';
  },

  setBaseUrl(baseUrl) {
    const clean = (baseUrl || '').trim();
    if (clean) sessionStorage.setItem('pmga_llm_base_url', clean);
    else sessionStorage.removeItem('pmga_llm_base_url');
  },

  getHeaders(overrideKey = null) {
    const headers = {};
    const key = overrideKey !== null ? (overrideKey || '').trim() : this.getApiKey();
    if (key) {
      headers['X-LLM-API-Key'] = key;
    }
    return headers;
  },

  async handleResponse(res) {
    if (!res.ok) {
      let errorMsg = `Server error ${res.status}`;
      try {
        const errJson = await res.json();
        errorMsg = errJson.detail || errJson.message || errorMsg;
      } catch (e) {
        // Fallback for non-JSON errors
        const text = await res.text();
        if (text) errorMsg = text.slice(0, 200);
      }
      throw new Error(errorMsg);
    }
    return res.json();
  },

  async getLLMStatus() {
    const res = await fetch('/api/v1/llm/status');
    return this.handleResponse(res);
  },

  async getLLMProviders() {
    const res = await fetch('/api/v1/llm/providers');
    return this.handleResponse(res);
  },

  async testLLMConnection({ provider, model_name, base_url, api_key }) {
    const res = await fetch('/api/v1/llm/test', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.getHeaders(api_key)
      },
      body: JSON.stringify({
        provider,
        model_name: model_name || null,
        base_url: base_url || null
      })
    });
    return this.handleResponse(res);
  },

  async listProjects() {
    const res = await fetch('/api/v1/projects');
    return this.handleResponse(res);
  },

  async createProject(title, description = '') {
    const res = await fetch('/api/v1/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description })
    });
    return this.handleResponse(res);
  },

  async getProject(projectId) {
    const res = await fetch(`/api/v1/projects/${projectId}`);
    return this.handleResponse(res);
  },

  async getBenchmarks() {
    const res = await fetch('/api/v1/datasets/benchmarks');
    return this.handleResponse(res);
  },

  async loadBenchmark(benchmarkId, projectId = null) {
    const formData = new FormData();
    formData.append('benchmark_id', benchmarkId);
    if (projectId) formData.append('project_id', projectId);

    const res = await fetch('/api/v1/datasets/benchmarks/load', {
      method: 'POST',
      body: formData
    });
    return this.handleResponse(res);
  },

  async uploadDocument(projectId, docType, file) {
    const formData = new FormData();
    formData.append('project_id', projectId);
    formData.append('doc_type', docType);
    formData.append('file', file);

    const res = await fetch('/api/v1/documents/upload', {
      method: 'POST',
      body: formData
    });
    return this.handleResponse(res);
  },

  async generateBPMN(projectId, processText, policyContext) {
    const res = await fetch('/api/v1/bpmn/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.getHeaders()
      },
      body: JSON.stringify({
        project_id: projectId,
        process_text: processText,
        policy_context: policyContext,
        provider: this.getProvider(),
        model_name: this.getModelName() || null,
        base_url: this.getBaseUrl() || null
      })
    });
    return this.handleResponse(res);
  },

  async saveBPMN(projectId, bpmnXml) {
    const res = await fetch('/api/v1/bpmn/update', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        bpmn_xml: bpmnXml
      })
    });
    return this.handleResponse(res);
  },

  async getISOControls(chapter = '') {
    const url = chapter ? `/api/v1/iso/controls?chapter=${encodeURIComponent(chapter)}` : '/api/v1/iso/controls';
    const res = await fetch(url);
    return this.handleResponse(res);
  },

  async runGapAnalysis(projectId, selectedControlIds = null) {
    const res = await fetch('/api/v1/analysis/gaps', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.getHeaders()
      },
      body: JSON.stringify({
        project_id: projectId,
        selected_control_ids: selectedControlIds,
        provider: this.getProvider(),
        model_name: this.getModelName() || null,
        base_url: this.getBaseUrl() || null
      })
    });
    return this.handleResponse(res);
  },

  async gdprPurge(projectId) {
    const res = await fetch(`/api/v1/gdpr/purge/${projectId}`, {
      method: 'DELETE'
    });
    return this.handleResponse(res);
  },

  async getCorpusManifest() {
    const res = await fetch('/api/v1/corpus');
    return this.handleResponse(res);
  },

  async getCorpusModel(modelId) {
    const res = await fetch(`/api/v1/corpus/${modelId}`);
    return this.handleResponse(res);
  },

  async parseExternalBPMN(bpmnXml, title = 'Uploaded BPMN') {
    const res = await fetch('/api/v1/corpus/parse-bpmn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bpmn_xml: bpmnXml, title })
    });
    return this.handleResponse(res);
  },

  async evaluateBPMN(groundTruthModelId, predictedGraph) {
    const res = await fetch('/api/v1/corpus/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ground_truth_model_id: groundTruthModelId,
        predicted_graph: predictedGraph
      })
    });
    return this.handleResponse(res);
  },

  async getSurvey(projectId) {
    const res = await fetch(`/api/v1/projects/${projectId}/survey`);
    return this.handleResponse(res);
  },

  async answerSurveyQuestion(projectId, rowId, questionCode, answer, acceptAsConcept = true) {
    const res = await fetch(`/api/v1/projects/${projectId}/survey/${rowId}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question_code: questionCode,
        answer: answer,
        accept_as_concept: acceptAsConcept
      })
    });
    return this.handleResponse(res);
  },

  async getDiagnostics(projectId) {
    const res = await fetch(`/api/v1/diagnostics/${projectId}`);
    return this.handleResponse(res);
  },

  async getEntities(projectId) {
    const res = await fetch(`/api/v1/projects/${projectId}/entities`);
    return this.handleResponse(res);
  },

  async createEntity(projectId, payload) {
    const res = await fetch(`/api/v1/projects/${projectId}/entities`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return this.handleResponse(res);
  },

  async deleteEntity(projectId, entityId) {
    const res = await fetch(`/api/v1/projects/${projectId}/entities/${entityId}`, {
      method: 'DELETE'
    });
    return this.handleResponse(res);
  },

  async createDependency(projectId, payload) {
    const res = await fetch(`/api/v1/projects/${projectId}/dependencies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return this.handleResponse(res);
  },

  async getPlotholes(projectId) {
    const res = await fetch(`/api/v1/projects/${projectId}/plotholes`);
    return this.handleResponse(res);
  }
};
