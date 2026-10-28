/**
 * PMGA BPMN Studio Module
 * Wraps bpmn-js with box resizing (Point #7), custom dimensions, zoom, and exports.
 */

class BPMNStudio {
  constructor(canvasContainerId) {
    this.container = document.getElementById(canvasContainerId);
    this.modeler = null;
    this.currentXml = null;
    this.selectedElement = null;
    this.initModeler();
  }

  initModeler() {
    if (!window.BpmnJS) {
      console.error('bpmn-js library not loaded yet');
      return;
    }

    this.modeler = new window.BpmnJS({
      container: this.container,
      keyboard: {
        bindTo: window
      }
    });

    // Listen to selection events to update dimension inputs
    this.modeler.on('selection.changed', (e) => {
      const selection = e.newSelection;
      if (selection && selection.length === 1) {
        this.selectedElement = selection[0];
        this.updateDimensionInputs(this.selectedElement);
      } else {
        this.selectedElement = null;
      }
    });

    window.addEventListener('resize', () => {
      if (this.modeler) {
        const canvas = this.modeler.get('canvas');
        if (canvas) canvas.resized();
      }
    });
  }

  async loadXML(xml) {
    if (!this.modeler) {
      this.initModeler();
    }
    this.currentXml = xml;
    try {
      const result = await this.modeler.importXML(xml);
      const canvas = this.modeler.get('canvas');
      // Only fit viewport if the canvas container is visible in DOM (has non-zero dimensions)
      const container = document.getElementById(this.containerId);
      if (container && container.clientWidth > 0 && container.clientHeight > 0) {
        try {
          canvas.resized();
          canvas.zoom('fit-viewport');
        } catch (zoomErr) {
          console.warn('Viewport fit deferred until tab switch:', zoomErr);
        }
      }
      return result;
    } catch (err) {
      console.error('Error importing BPMN XML:', err);
      throw err;
    }
  }

  async exportXML() {
    if (!this.modeler) return null;
    const { xml } = await this.modeler.saveXML({ format: true });
    this.currentXml = xml;
    return xml;
  }

  async exportSVG() {
    if (!this.modeler) return null;
    const { svg } = await this.modeler.saveSVG();
    return svg;
  }

  // Paint-Style Box Resizing (Point #7)
  resizeSelectedElement(width, height) {
    if (!this.modeler || !this.selectedElement) return false;
    const modeling = this.modeler.get('modeling');
    if (!modeling) return false;

    const el = this.selectedElement;
    const currentBounds = el.businessObject && el.businessObject.di && el.businessObject.di.bounds
      ? el.businessObject.di.bounds
      : { x: el.x, y: el.y, width: el.width, height: el.height };

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
}

window.BPMNStudio = BPMNStudio;
