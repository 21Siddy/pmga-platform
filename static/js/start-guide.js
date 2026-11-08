/**
 * PMGA workspace diagnostics and start guide.
 * Asks one endpoint where the project stands and turns the answer
 * into a step list the user can click through.
 */

const StartGuide = {
  el: {},
  init(components) {
    this.el = {
      root: document.getElementById('start-guide'),
      steps: document.getElementById('start-guide-steps'),
      hint: document.getElementById('start-guide-hint'),
    };
    this.components = components;
  },

  esc(text) {
    if (text === null || text === undefined) return '';
    return String(text).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  },

  async refresh(projectId) {
    if (!this.el.steps) return;
    if (!projectId) {
      this.el.steps.innerHTML = '';
      this.el.hint.textContent = 'Create or pick an assessment first.';
      return;
    }
    let diag = null;
    try {
      diag = await API.getDiagnostics(projectId);
    } catch (err) {
      this.el.hint.textContent = 'Could not check the workspace: ' + err.message;
      return;
    }
    this.render(diag);
  },

  render(diag) {
    if (!diag || !this.el.steps) return;
    const openSteps = diag.steps.filter((s) => !s.done);

    this.el.hint.textContent = diag.steps.every((s) => s.done)
      ? 'Everything is ready.'
      : `Next: ${openSteps[0] ? openSteps[0].title : 'nothing left'}`;

    this.el.steps.innerHTML = diag.steps.map((s) => {
      const mark = s.done ? '&#10003;' : '&#9656;';
      const cls = s.done ? 'guide-step done' : 'guide-step todo';
      const detailBit = this.esc(s.detail || '');
      const hintBit = s.done ? '' : `<span class="guide-hint">${this.esc(s.hint || '')}</span>`;
      const actionable = !s.done && s.action && s.action !== 'none'
        ? ` data-action="${this.esc(s.action)}" tabindex="0"`
        : '';
      return `<li class="${cls}"${actionable}>
          <span class="guide-mark">${mark}</span>
          <span class="guide-title">${this.esc(s.title)}</span>
          <span class="guide-detail">${detailBit}</span>
          ${hintBit}
        </li>`;
    }).join('');

    this.el.steps.querySelectorAll('.guide-step[data-action]').forEach((item) => {
      item.addEventListener('click', () => this.components.go(item.dataset.action));
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.components.go(item.dataset.action);
        }
      });
    });
  },
};
