/**
 * BPMN 2.0 Interactive Studio Module
 * Wraps bpmn-js Modeler in vanilla JS, providing zoom, save, and standard XML/SVG exports.
 */

class BPMNStudio {
  constructor(canvasContainerId) {
    this.container = document.getElementById(canvasContainerId);
    this.modeler = null;
    this.currentXml = null;
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

    // Resize canvas smoothly on window resize
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
      canvas.zoom('fit-viewport');
      return result;
    } catch (err) {
      console.error('Error importing BPMN XML into bpmn-js:', err);
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

  highlightElements(elementIds, cssClass = 'bpmn-gap-highlight') {
    if (!this.modeler) return;
    const canvas = this.modeler.get('canvas');
    elementIds.forEach(id => {
      try {
        canvas.addMarker(id, cssClass);
      } catch (e) {
        // Element might not exist in current view
      }
    });
  }
}

window.BPMNStudio = BPMNStudio;
