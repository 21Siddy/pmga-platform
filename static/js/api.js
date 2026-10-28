/**
 * CivicProcess API Client & BYOK Storage
 * Follows ISO 27001 / GDPR: Key is kept in browser memory or optional localStorage,
 * passed only via X-Gemini-API-Key header.
 */

const API = {
  getApiKey() {
    return localStorage.getItem('civic_gemini_key') || sessionStorage.getItem('civic_gemini_key') || '';
  },

  setApiKey(key, persistInLocal = true) {
    if (persistInLocal) {
      localStorage.setItem('civic_gemini_key', key);
    } else {
      sessionStorage.setItem('civic_gemini_key', key);
    }
  },

  getHeaders() {
    const headers = {};
    const key = this.getApiKey();
    if (key) {
      headers['X-Gemini-API-Key'] = key;
    }
    return headers;
  },

  async listProjects() {
    const res = await fetch('/api/v1/projects');
    return res.json();
  },

  async createProject(title, description = '') {
    const res = await fetch('/api/v1/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description })
    });
    return res.json();
  },

  async getProject(projectId) {
    const res = await fetch(`/api/v1/projects/${projectId}`);
    return res.json();
  },

  async getBenchmarks() {
    const res = await fetch('/api/v1/datasets/benchmarks');
    return res.json();
  },

  async loadBenchmark(benchmarkId, projectId = null) {
    const formData = new FormData();
    formData.append('benchmark_id', benchmarkId);
    if (projectId) formData.append('project_id', projectId);

    const res = await fetch('/api/v1/datasets/benchmarks/load', {
      method: 'POST',
      body: formData
    });
    return res.json();
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
    return res.json();
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
        policy_context: policyContext
      })
    });
    return res.json();
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
    return res.json();
  },

  async getISOControls(chapter = '') {
    const url = chapter ? `/api/v1/iso/controls?chapter=${encodeURIComponent(chapter)}` : '/api/v1/iso/controls';
    const res = await fetch(url);
    return res.json();
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
        selected_control_ids: selectedControlIds
      })
    });
    return res.json();
  },

  async gdprPurge(projectId) {
    const res = await fetch(`/api/v1/gdpr/purge/${projectId}`, {
      method: 'DELETE'
    });
    return res.json();
  }
};
