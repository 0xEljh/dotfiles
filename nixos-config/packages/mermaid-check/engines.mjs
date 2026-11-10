import { existsSync } from 'node:fs';

async function svgValidator() {
  const { JSDOM } = await import('jsdom');
  return svg => {
    const dom = new JSDOM(svg, { contentType: 'image/svg+xml' });
    try {
      const root = dom.window.document.documentElement;
      if (root.localName !== 'svg') throw new Error('Renderer did not return an SVG');
      if (root.getAttribute('aria-roledescription') === 'error') throw new Error('Renderer returned an error diagram');
      const box = root.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number);
      // Some valid types (notably info) use the default SVG viewport instead.
      if (box && (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0)) {
        throw new Error('Renderer returned an empty or invalid SVG viewBox');
      }
    } finally { dom.window.close(); }
  };
}

export async function createBackend(mode, { config, launch, peleOptions }) {
  if (mode === 'pele') {
    const { render } = await import('pele');
    return {
      label: 'pele render; not Mermaid compatibility',
      check(text) { render(text, peleOptions); },
      healthy: () => true,
      close: async () => {},
    };
  }
  if (mode === 'syntax') {
    const { JSDOM } = await import('jsdom');
    const dom = new JSDOM('');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    try {
      // DOMPurify captures window during import, so load Mermaid after the shim.
      const { default: mermaid } = await import('mermaid');
      mermaid.initialize({ ...config, startOnLoad: false });
      return {
        label: 'Mermaid syntax only; no rendering',
        async check(text) {
          mermaid.initialize({ ...config, startOnLoad: false });
          await mermaid.parse(text);
        },
        healthy: () => true,
        close: async () => dom.window.close(),
      };
    } catch (error) { dom.window.close(); throw error; }
  }
  if (Object.keys(config).length) {
    // Validate explicit configuration during setup, not as a diagram failure.
    const { default: mermaid } = await import('mermaid');
    mermaid.initialize({ ...config, startOnLoad: false });
  }
  const { default: puppeteer, ProtocolError, TimeoutError } = await import('puppeteer-core');
  const { renderMermaid } = await import('@mermaid-js/mermaid-cli');
  const validateSvg = await svgValidator();
  const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const executablePath = process.env.PUPPETEER_EXECUTABLE_PATH
    || process.env.MERMAID_CHECK_BROWSER
    || (process.platform === 'darwin' && existsSync(macChrome) ? macChrome : undefined);
  if (!executablePath && !launch.executablePath && !launch.channel) {
    throw new Error('No browser configured. Set PUPPETEER_EXECUTABLE_PATH or use --puppeteer-config');
  }
  const browser = await puppeteer.launch({ headless: true, executablePath, ...launch });
  return {
    label: 'Mermaid full render',
    async check(text) {
      const limit = config.maxTextSize ?? 50_000;
      if (text.length > limit) {
        throw new Error(`Source exceeds maxTextSize (${limit}); increase it with --config to render the actual diagram`);
      }
      const result = await renderMermaid(browser, text, 'svg', {
        mermaidConfig: config,
        // Font embedding affects portability, not whether layout/drawing succeeds.
        fontEmbed: false,
      });
      validateSvg(new TextDecoder().decode(result.data));
    },
    healthy: error => browser.connected && !(error instanceof ProtocolError) && !(error instanceof TimeoutError),
    close: async () => browser.close(),
  };
}
