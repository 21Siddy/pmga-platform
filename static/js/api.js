/**
 * PMGA API Client
 * Supports Google Gemini & University KI-Connect (OpenAI-compatible) with rate-limit pacing.
 */

const API = {
  getApiKey() {
    return localStorage.getItem('pmga_gemini_key') || sessionStorage.getItem('pmga_gemini_key') || '';
  },

  setApiKey(key, persist = true) {
    if (persist) {
      localStorage.setItem('pmga_gemini_key', key);
    } else {
      sessionStorage.setItem('pmga_gemini_key', key);
    }
  },

  getProvider() {
    return localStorage.getItem('pmga_llm_provider') || 'gemini';
  },

  setProvider(provider) {
    localStorage.setItem('pmga_llm_provider', provider);
  },

  getHeaders() {
    const headers = {};
    const key = this.getApiKey();
    if (key) {
      headers['X-Gemini-API-Key'] = key;
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
        provider: this.getProvider()
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
        provider: this.getProvider()
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
  }
};
