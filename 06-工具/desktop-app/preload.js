const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (partial) => ipcRenderer.invoke('settings:set', partial),
  listPrompts: () => ipcRenderer.invoke('prompts:list'),
  getDefaultPrompts: () => ipcRenderer.invoke('prompts:defaults'),
  savePrompts: (data) => ipcRenderer.invoke('prompts:save', data),
  openFiles: () => ipcRenderer.invoke('files:open'),
  loadDefaultContext: () => ipcRenderer.invoke('files:defaults'),
  loadDefaultContextForSkill: (skillId) => ipcRenderer.invoke('files:defaults-for-skill', skillId),
  getContextCatalog: () => ipcRenderer.invoke('context:catalog'),
  loadContextFiles: (paths) => ipcRenderer.invoke('context:load', paths),
  getSkills: () => ipcRenderer.invoke('skills:list'),
  listRuns: () => ipcRenderer.invoke('runs:list'),
  getRun: (runId) => ipcRenderer.invoke('runs:get', runId),
  saveRun: (run) => ipcRenderer.invoke('runs:save', run),
  getGuide: () => ipcRenderer.invoke('help:get-guide'),
  getWizardSchema: () => ipcRenderer.invoke('wizard:get-schema'),
  openPath: (targetPath) => ipcRenderer.invoke('path:open', targetPath),
  saveOutputs: (payload) => ipcRenderer.invoke('outputs:save', payload),
  getCrawlerRuntime: () => ipcRenderer.invoke('crawler:runtime'),
  runCrawler: (payload) => ipcRenderer.invoke('crawler:run', payload),
  runOpenAI: (payload) => ipcRenderer.invoke('openai:generate', payload),
  runOpenAIStream: (payload) => ipcRenderer.invoke('openai:generate-stream', payload),
  runCodexStream: (payload) => ipcRenderer.invoke('codex:generate-stream', payload),
  onStreamEvent: (handler) => {
    ipcRenderer.on('openai:stream:delta', (event, data) => handler({ type: 'delta', ...data }));
    ipcRenderer.on('openai:stream:done', (event, data) => handler({ type: 'done', ...data }));
    ipcRenderer.on('openai:stream:error', (event, data) => handler({ type: 'error', ...data }));
  },
  onCodexEvent: (handler) => {
    ipcRenderer.on('codex:stream:delta', (event, data) => handler({ type: 'delta', ...data }));
    ipcRenderer.on('codex:stream:done', (event, data) => handler({ type: 'done', ...data }));
    ipcRenderer.on('codex:stream:error', (event, data) => handler({ type: 'error', ...data }));
  }
});
