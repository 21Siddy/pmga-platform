/**
 * Gap Dashboard Renderer
 * Renders executive KPIs, psychological safety guidance, plain-English explanations,
 * actionable to-do checklists, consultation matrix, and pragmatic risk-based compensating controls.
 */

const GapDashboard = {
  render(gapData, containerId = 'findings-container') {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Update KPI summary cards
    const findings = gapData.findings || [];
    const critical = findings.filter(f => f.status === 'CRITICAL_GAP').length;
    const partial = findings.filter(f => f.status === 'PARTIAL_GAP').length;
    const compensating = findings.filter(f => f.compensating_controls && f.compensating_controls.length > 0).length;

    document.getElementById('kpi-score').textContent = `${gapData.compliance_score_percent || 0}%`;
    document.getElementById('kpi-critical').textContent = critical;
    document.getElementById('kpi-partial').textContent = partial;
    document.getElementById('kpi-compensating').textContent = compensating;

    const execSummary = document.getElementById('exec-summary-text');
    if (execSummary) {
      execSummary.textContent = gapData.executive_summary || 'Analysis complete.';
    }

    if (findings.length === 0) {
      container.innerHTML = `
        <div class="finding-card">
          <p>No operational gaps identified for the evaluated controls.</p>
        </div>
      `;
      return;
    }

    // Render findings list
    container.innerHTML = findings.map(f => this.renderFindingCard(f)).join('');
  },

  renderFindingCard(finding) {
    const badgeClass = finding.status === 'CRITICAL_GAP' ? 'critical' : (finding.status === 'PARTIAL_GAP' ? 'warning' : 'compliant');
    const badgeLabel = finding.status.replace('_', ' ');

    const todosHtml = (finding.actionable_todos || []).map(t => `<li>${t}</li>`).join('');
    const rolesHtml = (finding.roles_to_consult || []).map(r => `<li>${r}</li>`).join('');
    
    let compensatingHtml = '';
    if (finding.compensating_controls && finding.compensating_controls.length > 0) {
      const compList = finding.compensating_controls.map(c => `<p>• ${c}</p>`).join('');
      compensatingHtml = `
        <div class="compensating-box">
          <h5>Pragmatic Risk Compromise / Compensating Controls</h5>
          <p><em>If strict textbook compliance would halt critical public services, implement these options:</em></p>
          <div style="margin-top: 6px;">${compList}</div>
        </div>
      `;
    }

    return `
      <div class="finding-card">
        <div class="finding-header">
          <span class="finding-badge ${badgeClass}">${badgeLabel}</span>
          <span style="font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink-muted);">${finding.chapter}</span>
        </div>

        <div class="finding-title-row">
          <span class="finding-id">${finding.control_id}</span>
          <h4 class="finding-title">${finding.control_title}</h4>
        </div>

        <div class="plain-english-box">
          <strong>PLAIN-ENGLISH GAP SUMMARY (WHAT HAPPENS & WHY IT MATTERS):</strong>
          <p>${finding.plain_english_gap}</p>
        </div>

        <div class="finding-grid-2col">
          <div class="sub-panel">
            <h5>Policy vs Reality Comparison</h5>
            <p style="font-size: 0.82rem; margin-bottom: 6px;"><strong>Formal Mandate:</strong> ${finding.policy_claim || 'None stated'}</p>
            <p style="font-size: 0.82rem; color: #B45309;"><strong>Lived Practice:</strong> ${finding.operational_reality || 'Not documented'}</p>
          </div>
          <div class="sub-panel">
            <h5>Who Must Be Consulted</h5>
            <ul>${rolesHtml || '<li>Department Supervisors</li>'}</ul>
          </div>
        </div>

        <div class="sub-panel" style="margin-bottom: 16px;">
          <h5>Actionable Next Steps / To-Dos</h5>
          <ul>${todosHtml || '<li>Review operational instructions</li>'}</ul>
        </div>

        ${compensatingHtml}
      </div>
    `;
  }
};

window.GapDashboard = GapDashboard;
