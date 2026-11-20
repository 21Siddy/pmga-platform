/**
 * PMGA BPMN Studio Module (v2)
 * Wraps bpmn-js with native gap overlays, focus/highlight, box resizing, zoom controls,
 * and a zero-dependency interactive SVG fallback if bpmn-js CDN is unavailable offline.
 */

class BPMNStudio {
  constructor(canvasContainerId) {
    this.container = document.getElementById(canvasContainerId);
    this.containerId = canvasContainerId;
    this.modeler = null;
    this.currentXml = null;
    this.selectedElement = null;
    this._currentGaps = [];
    this._onBadgeClick = null;
    this._fallbackMode = false;
    this._fallbackSvg = null;
    this._onResizeBound = false;
    this.initModeler();
  }

  initModeler() {
    this.container = document.getElementById(this.containerId);
    if (!this.container) return;

    if (!window.BpmnJS) {
      this._fallbackMode = true;
      return;
    }

    if (this.modeler && !this._fallbackMode) return;
    this._fallbackMode = false;
    this.container.innerHTML = '';

    this.modeler = new window.BpmnJS({
      container: this.container,
      keyboard: {
        bindTo: window
      }
    });

    this.modeler.on('selection.changed', (e) => {
      const selection = e.newSelection;
      if (selection && selection.length === 1) {
        this.selectedElement = selection[0];
        this.updateDimensionInputs(this.selectedElement);
      } else {
        this.selectedElement = null;
      }
    });

    if (!this._onResizeBound) {
      this._onResizeBound = true;
      window.addEventListener('resize', () => {
        if (this.modeler && !this._fallbackMode) {
          try {
            const canvas = this.modeler.get('canvas');
            if (canvas) canvas.resized();
          } catch (_) {
            // ignore during teardown
          }
        }
      });
    }
  }

  async loadXML(xml) {
    this.container = document.getElementById(this.containerId);
    if (!this.container) return null;
    this.currentXml = xml;

    if (!this.modeler && window.BpmnJS) {
      this.initModeler();
    }

    if (!window.BpmnJS || !this.modeler) {
      this._fallbackMode = true;
      this._renderFallbackSVG(xml);
      if (this._currentGaps.length > 0) {
        this.setGapBadges(this._currentGaps, this._onBadgeClick);
      }
      return { warnings: [], fallback: true };
    }

    try {
      const result = await this.modeler.importXML(xml);
      this.fitViewport();
      if (this._currentGaps.length > 0) {
        this.setGapBadges(this._currentGaps, this._onBadgeClick);
      }
      return result;
    } catch (err) {
      console.error('Error importing BPMN XML:', err);
      throw err;
    }
  }

  fitViewport() {
    this.container = document.getElementById(this.containerId);
    if (!this.container || this.container.clientWidth === 0 || this.container.clientHeight === 0) {
      return;
    }
    if (this._fallbackMode || !this.modeler) return;
    try {
      const canvas = this.modeler.get('canvas');
      canvas.resized();
      canvas.zoom('fit-viewport', 'auto');
    } catch (zoomErr) {
      console.warn('Viewport fit deferred:', zoomErr);
    }
  }

  zoomIn() {
    if (this._fallbackMode || !this.modeler) return;
    try {
      const canvas = this.modeler.get('canvas');
      canvas.zoom(Math.min(2.4, (canvas.zoom() || 1) * 1.18));
    } catch (_) {}
  }

  zoomOut() {
    if (this._fallbackMode || !this.modeler) return;
    try {
      const canvas = this.modeler.get('canvas');
      canvas.zoom(Math.max(0.35, (canvas.zoom() || 1) * 0.85));
    } catch (_) {}
  }

  async exportXML() {
    if (this._fallbackMode || !this.modeler) {
      return this.currentXml || null;
    }
    const { xml } = await this.modeler.saveXML({ format: true });
    this.currentXml = xml;
    return xml;
  }

  async exportSVG() {
    if (this._fallbackMode || !this.modeler) {
      return this._fallbackSvg || null;
    }
    const { svg } = await this.modeler.saveSVG();
    return svg;
  }

  setGapBadges(gaps = [], onBadgeClick = null) {
    this._currentGaps = Array.isArray(gaps) ? gaps : [];
    this._onBadgeClick = onBadgeClick;

    const openGaps = this._currentGaps.filter((g) => g.status !== 'RESOLVED');
    if (this._fallbackMode || !this.modeler) {
      this._renderFallbackGapBadges(openGaps);
      return;
    }

    let overlays;
    let elementRegistry;
    try {
      overlays = this.modeler.get('overlays');
      elementRegistry = this.modeler.get('elementRegistry');
      overlays.clear();
    } catch (err) {
      console.warn('Overlays service unavailable:', err);
      return;
    }

    const byNode = new Map();
    const fallbackTargets = elementRegistry
      .getAll()
      .filter((el) => el.type && !el.type.includes('SequenceFlow') && el.type !== 'bpmn:Process' && el.type !== 'bpmn:Collaboration' && el.type !== 'label');
    const defaultNodeId = fallbackTargets[0] ? fallbackTargets[0].id : null;

    openGaps.forEach((gap) => {
      const nodeIds = Array.isArray(gap.affected_node_ids) && gap.affected_node_ids.length
        ? gap.affected_node_ids
        : (defaultNodeId ? [defaultNodeId] : []);
      nodeIds.forEach((nid) => {
        if (!elementRegistry.get(nid)) return;
        if (!byNode.has(nid)) byNode.set(nid, []);
        byNode.get(nid).push(gap);
      });
    });

    if (byNode.size === 0 && openGaps.length > 0 && defaultNodeId) {
      byNode.set(defaultNodeId, openGaps);
    }

    byNode.forEach((nodeGaps, nodeId) => {
      const sevOrder = { CRITICAL: 1, HIGH: 2, MODERATE: 3, INFO: 4 };
      const topSev = nodeGaps.reduce((best, g) => ((sevOrder[g.severity] || 9) < (sevOrder[best] || 9) ? g.severity : best), 'INFO');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `pmga-gap-badge sev-${topSev.toLowerCase()}`;
      btn.dataset.nodeId = nodeId;
      btn.title = `${nodeGaps.length} helpful next step(s) on ${nodeId}`;
      btn.textContent = String(nodeGaps.length);
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (typeof this._onBadgeClick === 'function') {
          this._onBadgeClick(nodeId, nodeGaps);
        }
      });

      try {
        overlays.add(nodeId, 'gap-badge', {
          position: { top: -10, right: 8 },
          html: btn
        });
      } catch (err) {
        console.warn(`Could not attach gap badge to ${nodeId}:`, err);
      }
    });
  }

  focusNode(nodeId) {
    if (!nodeId) return;
    if (this._fallbackMode || !this.modeler) {
      const el = this.container?.querySelector(`[data-bpmn-id="${CSS.escape(nodeId)}"]`);
      if (el) {
        this.container.querySelectorAll('.pmga-node-focused').forEach((n) => n.classList.remove('pmga-node-focused'));
        el.classList.add('pmga-node-focused');
        el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
      }
      return;
    }
    try {
      const elementRegistry = this.modeler.get('elementRegistry');
      const selection = this.modeler.get('selection');
      const el = elementRegistry.get(nodeId);
      if (el) {
        selection.select(el);
      }
    } catch (_) {}
  }

  resizeSelectedElement(width, height) {
    if (this._fallbackMode || !this.modeler || !this.selectedElement) return false;
    const modeling = this.modeler.get('modeling');
    if (!modeling) return false;

    const el = this.selectedElement;
    const newBounds = {
      x: el.x,
      y: el.y,
      width: parseInt(width, 10),
      height: parseInt(height, 10)
    };

    try {
      modeling.resizeShape(el, newBounds);
      return true;
    } catch (err) {
      console.warn('Resize failed on element type:', el.type, err);
      return false;
    }
  }

  updateDimensionInputs(element) {
    const wInput = document.getElementById('node-width-input');
    const hInput = document.getElementById('node-height-input');
    if (wInput && hInput && element) {
      wInput.value = Math.round(element.width);
      hInput.value = Math.round(element.height);
    }
  }

  _renderFallbackSVG(xml) {
    if (!this.container) return;
    this.container.innerHTML = '';
    if (!xml) {
      this.container.innerHTML = '<div class="bpmn-empty-state">No BPMN XML loaded yet.</div>';
      return;
    }
    try {
      const doc = new DOMParser().parseFromString(xml, 'text/xml');
      const allElems = Array.from(doc.getElementsByTagName('*'));
      const names = new Map();
      const types = new Map();
      allElems.forEach((el) => {
        const id = el.getAttribute('id');
        if (id && !el.localName.startsWith('BPMN')) {
          names.set(id, el.getAttribute('name') || '');
          types.set(id, el.localName);
        }
      });

      const shapes = [];
      const edges = [];
      let minX = 99999, minY = 99999, maxX = 200, maxY = 200;

      allElems.forEach((el) => {
        if (el.localName === 'BPMNShape') {
          const ref = el.getAttribute('bpmnElement');
          const b = Array.from(el.children).find((c) => c.localName === 'Bounds');
          if (ref && b) {
            const x = parseFloat(b.getAttribute('x') || '0');
            const y = parseFloat(b.getAttribute('y') || '0');
            const w = parseFloat(b.getAttribute('width') || '120');
            const h = parseFloat(b.getAttribute('height') || '70');
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + w);
            maxY = Math.max(maxY, y + h);
            shapes.push({ id: ref, x, y, w, h, name: names.get(ref) || ref, kind: types.get(ref) || 'task' });
          }
        } else if (el.localName === 'BPMNEdge') {
          const ref = el.getAttribute('bpmnElement');
          const pts = Array.from(el.children)
            .filter((c) => c.localName === 'waypoint')
            .map((wp) => ({ x: parseFloat(wp.getAttribute('x') || '0'), y: parseFloat(wp.getAttribute('y') || '0') }));
          if (pts.length >= 2) {
            edges.push({ id: ref, pts });
          }
        }
      });

      const pad = 40;
      const vx = Math.max(0, minX - pad);
      const vy = Math.max(0, minY - pad);
      const vw = Math.max(600, maxX - minX + pad * 2);
      const vh = Math.max(260, maxY - minY + pad * 2);

      const wrapper = document.createElement('div');
      wrapper.className = 'bpmn-fallback-wrap';
      wrapper.style.position = 'relative';
      wrapper.style.width = '100%';
      wrapper.style.height = '100%';
      wrapper.style.overflow = 'auto';

      const svgNS = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('viewBox', `${vx} ${vy} ${vw} ${vh}`);
      svg.setAttribute('width', '100%');
      svg.setAttribute('height', '100%');
      svg.style.minHeight = '360px';

      const defs = document.createElementNS(svgNS, 'defs');
      defs.innerHTML = '<marker id="pmga-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#475569"/></marker>';
      svg.appendChild(defs);

      // Draw pools/lanes first
      shapes.sort((a, b) => {
        const aPool = a.kind === 'participant' || a.kind === 'lane' ? 0 : 1;
        const bPool = b.kind === 'participant' || b.kind === 'lane' ? 0 : 1;
        return aPool - bPool;
      });

      shapes.forEach((s) => {
        const g = document.createElementNS(svgNS, 'g');
        g.setAttribute('data-bpmn-id', s.id);
        g.setAttribute('data-bpmn-kind', s.kind);
        g.style.cursor = 'pointer';

        if (s.kind === 'participant' || s.kind === 'lane') {
          const rect = document.createElementNS(svgNS, 'rect');
          rect.setAttribute('x', s.x);
          rect.setAttribute('y', s.y);
          rect.setAttribute('width', s.w);
          rect.setAttribute('height', s.h);
          rect.setAttribute('fill', s.kind === 'participant' ? 'rgba(248,245,239,0.75)' : 'rgba(255,255,255,0.55)');
          rect.setAttribute('stroke', '#94A3B8');
          rect.setAttribute('stroke-width', '1.4');
          rect.setAttribute('rx', '6');
          g.appendChild(rect);
        } else if (s.kind.includes('Event')) {
          const circle = document.createElementNS(svgNS, 'circle');
          circle.setAttribute('cx', s.x + s.w / 2);
          circle.setAttribute('cy', s.y + s.h / 2);
          circle.setAttribute('r', Math.min(s.w, s.h) / 2);
          circle.setAttribute('fill', '#FFFDF8');
          circle.setAttribute('stroke', s.kind === 'endEvent' ? '#C86D44' : '#1E4E68');
          circle.setAttribute('stroke-width', s.kind === 'endEvent' ? '3' : '2');
          g.appendChild(circle);
        } else if (s.kind.includes('Gateway')) {
          const poly = document.createElementNS(svgNS, 'polygon');
          const cx = s.x + s.w / 2, cy = s.y + s.h / 2;
          poly.setAttribute('points', `${cx},${s.y} ${s.x + s.w},${cy} ${cx},${s.y + s.h} ${s.x},${cy}`);
          poly.setAttribute('fill', '#FEF7EB');
          poly.setAttribute('stroke', '#B45309');
          poly.setAttribute('stroke-width', '2');
          g.appendChild(poly);
        } else {
          const rect = document.createElementNS(svgNS, 'rect');
          rect.setAttribute('x', s.x);
          rect.setAttribute('y', s.y);
          rect.setAttribute('width', s.w);
          rect.setAttribute('height', s.h);
          rect.setAttribute('fill', '#FFFFFF');
          rect.setAttribute('stroke', '#1E4E68');
          rect.setAttribute('stroke-width', '1.8');
          rect.setAttribute('rx', '8');
          g.appendChild(rect);
        }

        const txt = document.createElementNS(svgNS, 'text');
        txt.setAttribute('x', s.kind === 'participant' || s.kind === 'lane' ? s.x + 12 : s.x + s.w / 2);
        txt.setAttribute('y', s.kind === 'participant' || s.kind === 'lane' ? s.y + 18 : s.y + s.h / 2 + 4);
        txt.setAttribute('text-anchor', s.kind === 'participant' || s.kind === 'lane' ? 'start' : 'middle');
        txt.setAttribute('font-family', 'system-ui, sans-serif');
        txt.setAttribute('font-size', s.kind === 'participant' || s.kind === 'lane' ? '12' : '11.5');
        txt.setAttribute('fill', '#1E293B');
        txt.textContent = (s.name || s.id).slice(0, 32);
        g.appendChild(txt);

        svg.appendChild(g);
      });

      edges.forEach((e) => {
        const pl = document.createElementNS(svgNS, 'polyline');
        pl.setAttribute('points', e.pts.map((p) => `${p.x},${p.y}`).join(' '));
        pl.setAttribute('fill', 'none');
        pl.setAttribute('stroke', '#64748B');
        pl.setAttribute('stroke-width', '1.6');
        pl.setAttribute('marker-end', 'url(#pmga-arrow)');
        svg.appendChild(pl);
      });

      this._fallbackShapes = shapes;
      this._fallbackViewBox = { vx, vy, vw, vh };
      wrapper.appendChild(svg);
      this.container.appendChild(wrapper);
      this._fallbackSvg = new XMLSerializer().serializeToString(svg);
    } catch (err) {
      this.container.innerHTML = `<div class="bpmn-empty-state">Diagram preview unavailable (${err.message})</div>`;
    }
  }

  _renderFallbackGapBadges(openGaps) {
    if (!this.container || !this._fallbackShapes || this._fallbackShapes.length === 0) return;
    const wrap = this.container.querySelector('.bpmn-fallback-wrap');
    if (!wrap) return;
    wrap.querySelectorAll('.pmga-gap-badge-fallback').forEach((n) => n.remove());

    const shapeMap = new Map(this._fallbackShapes.map((s) => [s.id, s]));
    const nonLane = this._fallbackShapes.filter((s) => s.kind !== 'participant' && s.kind !== 'lane');
    const defaultId = nonLane[0]?.id || this._fallbackShapes[0].id;
    const byNode = new Map();

    openGaps.forEach((g) => {
      const ids = Array.isArray(g.affected_node_ids) && g.affected_node_ids.length ? g.affected_node_ids : [defaultId];
      ids.forEach((nid) => {
        const target = shapeMap.has(nid) ? nid : defaultId;
        if (!byNode.has(target)) byNode.set(target, []);
        byNode.get(target).push(g);
      });
    });

    const { vx, vy, vw, vh } = this._fallbackViewBox;
    byNode.forEach((nodeGaps, nodeId) => {
      const s = shapeMap.get(nodeId);
      if (!s) return;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pmga-gap-badge pmga-gap-badge-fallback sev-high';
      btn.dataset.nodeId = nodeId;
      btn.textContent = String(nodeGaps.length);
      btn.style.position = 'absolute';
      btn.style.left = `${((s.x + s.w - vx) / vw) * 100}%`;
      btn.style.top = `${((s.y - vy) / vh) * 100}%`;
      btn.style.transform = 'translate(-50%, -35%)';
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (typeof this._onBadgeClick === 'function') {
          this._onBadgeClick(nodeId, nodeGaps);
        }
      });
      wrap.appendChild(btn);
    });
  }
}

window.BPMNStudio = BPMNStudio;
