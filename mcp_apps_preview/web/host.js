'use strict';
// MCP Apps Preview: a minimal, spec-following web host (SEP-1865, 2026-01-26).
(function () {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const state = {server: null, tool: null, frame: null, view: null, origin: sandboxOrigin()};
  const PRESETS = {chat: {width: 720}, display: {width: 960, height: 540}, phone: {width: 390}};
  const THEMES = {
    light: {'--color-background-primary': '#ffffff', '--color-background-secondary': '#f5f6f8', '--color-text-primary': '#16181d',
            '--color-text-secondary': '#667085', '--color-border-primary': '#e3e6eb'},
    dark: {'--color-background-primary': '#12151b', '--color-background-secondary': '#1b2029', '--color-text-primary': '#eef1f6',
           '--color-text-secondary': '#a3abbb', '--color-border-primary': '#2b3240'}};

  function sandboxOrigin() {
    const other = location.hostname === 'localhost' ? '127.0.0.1' : 'localhost';
    return `${location.protocol}//${other}${location.port ? ':' + location.port : ''}`;
  }
  async function api(path, body) {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : undefined);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function log(direction, message, note) {
    const li = el('li', direction);
    const arrow = {out: 'host → view', in: 'view → host', err: 'error', info: 'host'}[direction];
    const label = message.method || (message.error ? `error #${message.id}` : `result #${message.id}`);
    li.append(el('small', null, `${new Date().toTimeString().slice(0, 8)} ${arrow} `), el('b', null, label));
    const detail = message.params || message.result || message.error;
    if (note) li.append(document.createTextNode(' ' + note));
    if (detail) li.append(document.createTextNode(' ' + JSON.stringify(detail).slice(0, 240)));
    $('log').prepend(li);
  }

  // ---------- example arguments from a JSON Schema ----------
  function example(schema) {
    if (!schema || typeof schema !== 'object') return null;
    if ('default' in schema) return schema.default;
    if (schema.examples && schema.examples.length) return schema.examples[0];
    if (schema.enum) return schema.enum[0];
    if (schema.const !== undefined) return schema.const;
    const options = schema.anyOf || schema.oneOf;
    if (options) return example(options.find(o => o.type !== 'null') || options[0]);
    switch (schema.type) {
      case 'object': {
        const out = {};
        for (const key of schema.required || []) out[key] = example((schema.properties || {})[key]);
        return out;
      }
      case 'array': return schema.minItems ? [example(schema.items)] : [];
      case 'string': return '';
      case 'integer': case 'number': return schema.minimum || 0;
      case 'boolean': return false;
      default: return null;
    }
  }

  // ---------- view lifecycle ----------
  function context() {
    const preset = PRESETS[$('preset').value], theme = $('theme').value;
    return {theme, displayMode: 'inline', availableDisplayModes: ['inline'], platform: 'web', locale: navigator.language,
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone, userAgent: 'mcp-apps-preview',
      containerDimensions: preset.height ? {width: preset.width, height: preset.height} : {width: preset.width, maxHeight: 4000},
      deviceCapabilities: {touch: false, hover: true}, styles: {variables: THEMES[theme]},
      toolInfo: state.view ? {tool: state.view.tool} : undefined};
  }
  function post(message) {
    if (!state.frame) return;
    log('out', message);
    state.frame.contentWindow.postMessage(message, state.origin);
  }
  async function render(tool, args, result) {
    const resource = await api('/api/resource?uri=' + encodeURIComponent(tool.ui));
    const box = $('frame-box');
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
    frame.setAttribute('title', `${tool.name} view`);
    frame.src = state.origin + '/sandbox.html';
    box.replaceChildren(frame);
    state.frame = frame;
    state.view = {tool: {name: tool.name, description: tool.description, inputSchema: tool.inputSchema}, args, result, resource, initialized: false};
    applyPreset();
  }
  function applyPreset() {
    const preset = $('preset').value;
    $('frame-box').className = 'frame-box ' + preset;
    if (state.frame) {
      state.frame.style.height = PRESETS[preset].height ? PRESETS[preset].height + 'px' : state.frame.style.height || '160px';
      if (state.view && state.view.initialized) post({jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: context()});
    }
  }
  window.addEventListener('message', async event => {
    if (!state.frame || event.source !== state.frame.contentWindow || event.origin !== state.origin) return;
    const msg = event.data;
    if (!msg || msg.jsonrpc !== '2.0') return;
    log('in', msg);
    const reply = result => post({jsonrpc: '2.0', id: msg.id, result});
    const fail = (message, code) => post({jsonrpc: '2.0', id: msg.id, error: {code: code || -32000, message}});
    const view = state.view;
    switch (msg.method) {
      case 'ui/notifications/sandbox-proxy-ready': {
        const meta = (view.resource._meta && view.resource._meta.ui) || {};
        const html = view.resource.text != null ? view.resource.text : atob(view.resource.blob || '');
        post({jsonrpc: '2.0', method: 'ui/notifications/sandbox-resource-ready', params: {html, csp: meta.csp || {}, permissions: meta.permissions || {}}});
        break;
      }
      case 'ui/initialize':
        reply({protocolVersion: '2026-01-26', hostInfo: {name: 'mcp-apps-preview', version: (state.server || {}).version || '0'},
               hostCapabilities: {serverTools: {}, serverResources: {}, logging: {}, openLinks: {}}, hostContext: context()});
        break;
      case 'ui/notifications/initialized':
        view.initialized = true;
        post({jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: {arguments: view.args}});
        post({jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: view.result});
        break;
      case 'ui/notifications/size-changed':
        if (!PRESETS[$('preset').value].height && msg.params && msg.params.height > 0) state.frame.style.height = Math.min(4000, msg.params.height) + 'px';
        break;
      case 'tools/call':
        try { reply(await api('/api/call', {name: msg.params && msg.params.name, arguments: (msg.params && msg.params.arguments) || {}, caller: 'app'})); }
        catch (error) { log('err', {method: 'tools/call'}, error.message); fail(error.message); }
        break;
      case 'resources/read':
        try { reply({contents: [await api('/api/resource?uri=' + encodeURIComponent((msg.params || {}).uri || ''))]}); }
        catch (error) { fail(error.message); }
        break;
      case 'ui/open-link':
        if (/^https:\/\//.test((msg.params || {}).url || '')) { window.open(msg.params.url, '_blank', 'noopener'); reply({}); } else fail('Only https links');
        break;
      case 'ui/request-display-mode': reply({mode: 'inline'}); break;
      case 'ui/message': case 'ui/update-model-context': case 'ping': reply({}); break;
      case 'notifications/message': break;
      default: if (msg.id !== undefined && msg.method) fail('Method not supported: ' + msg.method, -32601);
    }
  });

  // ---------- tools, calls, lint ----------
  function select(tool, button) {
    state.tool = tool;
    document.querySelectorAll('#tools button').forEach(b => b.classList.toggle('on', b === button));
    $('call-form').hidden = false;
    $('tool-name').textContent = tool.name;
    $('tool-desc').textContent = (tool.description || '').split('\n')[0];
    $('args').value = JSON.stringify(example(tool.inputSchema) || {}, null, 2);
  }
  $('call-form').addEventListener('submit', async event => {
    event.preventDefault();
    let args;
    try { args = JSON.parse($('args').value || '{}'); } catch (error) { $('call-status').textContent = 'Arguments are not valid JSON'; return; }
    $('call-status').textContent = 'Calling…';
    try {
      const started = performance.now();
      const result = await api('/api/call', {name: state.tool.name, arguments: args, caller: 'host'});
      $('result').textContent = JSON.stringify(result, null, 2);
      $('call-status').textContent = `Returned in ${Math.round(performance.now() - started)} ms` + (result.isError ? ' (isError)' : '');
      log('info', {method: 'tools/call ' + state.tool.name}, result.isError ? 'isError=true' : 'ok');
      if (state.tool.ui) await render(state.tool, args, result);
      else $('frame-box').replaceChildren(el('p', 'muted empty', 'This tool has no MCP Apps view. See the Result tab.'));
    } catch (error) { $('call-status').textContent = 'Error: ' + error.message; log('err', {method: 'tools/call'}, error.message); }
  });
  async function loadLint() {
    const box = $('lint');
    box.replaceChildren(el('p', 'muted', 'Linting…'));
    try {
      const report = await api('/api/lint');
      box.replaceChildren(el('p', 'sum', `${report.ok ? 'PASS' : 'FAIL'} · ${report.counts.error} errors · ${report.counts.warning} warnings · ${report.counts.info} notes`));
      report.findings.forEach(f => box.append(el('div', 'f ' + f.severity, `${f.severity} · ${f.rule} · ${f.subject}: ${f.message}`)));
    } catch (error) { box.replaceChildren(el('p', 'muted', error.message)); }
  }
  for (const name of ['log', 'lint', 'result']) {
    $('tab-' + name).addEventListener('click', () => {
      for (const other of ['log', 'lint', 'result']) { $(other).hidden = other !== name; $('tab-' + other).classList.toggle('on', other === name); }
      if (name === 'lint') loadLint();
    });
  }
  $('preset').addEventListener('change', applyPreset);
  $('theme').addEventListener('change', () => { if (state.view && state.view.initialized) post({jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: context()}); });

  api('/api/server').then(info => {
    state.server = info;
    const server = info.server || {};
    $('server').textContent = `${server.name || 'server'} ${server.version || ''} · protocol ${info.protocol} · ${info.label}`;
    const list = $('tools');
    info.tools.forEach(tool => {
      const button = el('button', null, tool.name);
      button.type = 'button';
      if (tool.ui) button.append(el('span', 'tag', 'view'));
      if (tool.visibility.length === 1 && tool.visibility[0] === 'app') button.append(el('span', 'tag app', 'app-only'));
      button.addEventListener('click', () => select(tool, button));
      const li = el('li'); li.append(button); list.append(li);
    });
    const first = info.tools.find(t => t.ui && t.visibility.includes('model'));
    if (first) select(first, list.querySelector('button'));
  }).catch(error => { $('server').textContent = 'Cannot reach the MCP server: ' + error.message; });
  window.__preview = {state, example};
})();
