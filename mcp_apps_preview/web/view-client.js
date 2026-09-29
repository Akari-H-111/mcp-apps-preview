/*! McpAppLite: a dependency-free MCP Apps view client (SEP-1865, 2026-01-26). MIT.
 * Inline this file in your ui:// HTML (no CDN, no bundler) and call:
 *
 *   const app = await McpAppLite.connect({name: 'my-view', version: '1.0.0'}, {
 *     onToolInput(args) {}, onToolResult(result) {}, onHostContext(ctx) {}, onTeardown() {}
 *   });
 *   const result = await app.callTool('refresh', {id: 1});   // tools/call through the host
 *   app.sendMessage('Tell me more');                          // ui/message
 *   app.updateModelContext({structuredContent: {...}});       // ui/update-model-context
 */
(function (global) {
  'use strict';
  function connect(appInfo, handlers) {
    handlers = handlers || {};
    const parent = global.parent;
    if (!parent || parent === global) return Promise.reject(new Error('Not inside an MCP Apps host'));
    let nextId = 1;
    const pending = new Map();
    let hostContext = {};
    let lastSize = '';
    const send = message => parent.postMessage(message, '*');
    const request = (method, params) => new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, {resolve, reject});
      send({jsonrpc: '2.0', id, method, params: params || {}});
      setTimeout(() => { if (pending.delete(id)) reject(new Error(method + ' timed out')); }, 30000);
    });
    const notify = (method, params) => send({jsonrpc: '2.0', method, params: params || {}});
    const applyTheme = ctx => {
      if (ctx.theme) document.documentElement.dataset.theme = ctx.theme;
      const vars = ctx.styles && ctx.styles.variables;
      if (vars) Object.keys(vars).forEach(k => { if (vars[k]) document.documentElement.style.setProperty(k, vars[k]); });
    };
    const reportSize = () => {
      const width = Math.ceil(document.documentElement.scrollWidth);
      const height = Math.ceil(document.documentElement.scrollHeight);
      const key = width + 'x' + height;
      if (!height || key === lastSize) return;
      lastSize = key;
      notify('ui/notifications/size-changed', {width, height});
    };
    global.addEventListener('message', event => {
      if (event.source !== parent) return;
      const msg = event.data;
      if (!msg || msg.jsonrpc !== '2.0') return;
      if (msg.id !== undefined && !msg.method) {
        const waiter = pending.get(msg.id);
        if (!waiter) return;
        pending.delete(msg.id);
        msg.error ? waiter.reject(new Error(msg.error.message || 'Host error')) : waiter.resolve(msg.result);
        return;
      }
      switch (msg.method) {
        case 'ui/notifications/tool-input': if (handlers.onToolInput) handlers.onToolInput((msg.params || {}).arguments || {}); break;
        case 'ui/notifications/tool-input-partial': if (handlers.onToolInputPartial) handlers.onToolInputPartial((msg.params || {}).arguments || {}); break;
        case 'ui/notifications/tool-result': if (handlers.onToolResult) handlers.onToolResult(msg.params || {}); setTimeout(reportSize, 0); break;
        case 'ui/notifications/tool-cancelled': if (handlers.onToolCancelled) handlers.onToolCancelled((msg.params || {}).reason); break;
        case 'ui/notifications/host-context-changed':
          Object.assign(hostContext, msg.params || {}); applyTheme(msg.params || {});
          if (handlers.onHostContext) handlers.onHostContext(hostContext); break;
        case 'ui/resource-teardown':
          Promise.resolve(handlers.onTeardown && handlers.onTeardown()).finally(() => send({jsonrpc: '2.0', id: msg.id, result: {}})); break;
        case 'ping': send({jsonrpc: '2.0', id: msg.id, result: {}}); break;
      }
    });
    if (global.ResizeObserver) new ResizeObserver(reportSize).observe(document.documentElement);
    return request('ui/initialize', {appInfo, appCapabilities: {availableDisplayModes: ['inline']}, protocolVersion: '2026-01-26'})
      .then(result => {
        hostContext = (result && result.hostContext) || {};
        applyTheme(hostContext);
        notify('ui/notifications/initialized');
        return {
          get hostContext() { return hostContext; },
          hostCapabilities: (result && result.hostCapabilities) || {},
          callTool: (name, args) => request('tools/call', {name, arguments: args || {}}),
          readResource: uri => request('resources/read', {uri}),
          sendMessage: text => request('ui/message', {role: 'user', content: {type: 'text', text}}),
          updateModelContext: params => request('ui/update-model-context', params || {}),
          openLink: url => request('ui/open-link', {url}),
          log: (level, data) => notify('notifications/message', {level, data}),
          reportSize,
        };
      });
  }
  global.McpAppLite = {connect};
})(typeof window !== 'undefined' ? window : this);
