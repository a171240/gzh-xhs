const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const DEFAULT_PROMPTS_PATH = path.join(__dirname, 'data', 'prompts.default.json');
const DEFAULT_CONTEXT_PATH = path.join(__dirname, 'data', 'default-context.json');
const CONTEXT_CATALOG_PATH = path.join(__dirname, 'data', 'context-catalog.json');
const SKILLS_PATH = path.join(__dirname, 'data', 'skills.json');
const UI_GUIDE_PATH = path.join(__dirname, 'data', 'ui-guide.json');
const WIZARD_SCHEMA_PATH = path.join(__dirname, 'data', 'wizard.schema.json');
const CODEX_CLI_RELATIVE_PATH = path.join('bin', 'windows-x86_64', 'codex.exe');
const CODEX_EXTENSION_PREFIX = 'openai.chatgpt-';
const WORKSPACE_ROOT = path.resolve(__dirname, '..');
const UI_VERSION = 2;
const RUNS_LIMIT = 200;
const DEFAULT_CODEX_MODEL = 'gpt-5.4';
const DEFAULT_REASONING_EFFORT = 'xhigh';
const ALLOWED_REASONING_EFFORTS = new Set(['low', 'medium', 'high', 'xhigh']);
const CRAWL_BRIDGE_PATH = path.join(WORKSPACE_ROOT, 'scripts', 'crawl_bridge.py');
const URL_READER_VENV_PYTHON = path.join(WORKSPACE_ROOT, '内容抓取', 'url-reader', '.venv', 'Scripts', 'python.exe');
const INTERNAL_KEYWORD_SCRIPT_PATH = path.join(WORKSPACE_ROOT, 'scripts', 'crawlers', 'keyword_matrix.py');
const EXTERNAL_KEYWORD_SCRIPT_PATH = path.join(
  process.env.USERPROFILE || process.env.HOME || '',
  '.codex',
  'skills',
  'keyword-crawler-matrix',
  'scripts',
  'crawl_keywords.py'
);
let OpenAIClient = null;

function readJson(filePath, fallback) {
  try {
    const raw = fs.readFileSync(filePath, 'utf8');
    return JSON.parse(raw);
  } catch (err) {
    return fallback;
  }
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
}

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function runsPath() {
  return path.join(app.getPath('userData'), 'runs.json');
}

function ensureRecordArray(data) {
  if (!data || typeof data !== 'object') return [];
  return Array.isArray(data.items) ? data.items : [];
}

function readRuns() {
  const raw = readJson(runsPath(), { items: [] });
  return ensureRecordArray(raw);
}

function writeRuns(items) {
  writeJson(runsPath(), { items: Array.isArray(items) ? items : [] });
}

function summarizeRun(run) {
  if (!run || typeof run !== 'object') return null;
  const files = Array.isArray(run.files) ? run.files : [];
  const artifacts = Array.isArray(run.artifacts) ? run.artifacts : [];
  const fileCount = new Set([...files, ...artifacts]).size;
  return {
    id: run.id || '',
    skillId: run.skillId || '',
    skillName: run.skillName || '',
    runType: run.runType || 'llm',
    mode: run.mode || '',
    status: run.status || '',
    startedAt: run.startedAt || null,
    endedAt: run.endedAt || null,
    briefSummary: run.briefSummary || '',
    fileCount
  };
}

function upsertRunRecord(payload) {
  const run = payload && typeof payload === 'object' ? { ...payload } : {};
  const runId = typeof run.id === 'string' && run.id.trim()
    ? run.id.trim()
    : `run_${Date.now()}_${Math.floor(Math.random() * 1000)}`;

  run.id = runId;
  run.updatedAt = new Date().toISOString();

  const current = readRuns();
  const next = [run, ...current.filter((item) => item && item.id !== runId)].slice(0, RUNS_LIMIT);
  writeRuns(next);

  return run;
}

function listCodexPathCandidates() {
  const userHome = process.env.USERPROFILE || process.env.HOME || '';
  if (!userHome) return [];

  const roots = [
    path.join(userHome, '.vscode', 'extensions'),
    path.join(userHome, '.vscode-insiders', 'extensions')
  ];

  const candidates = [];

  for (const root of roots) {
    if (!fs.existsSync(root)) continue;

    let entries = [];
    try {
      entries = fs.readdirSync(root, { withFileTypes: true });
    } catch (err) {
      continue;
    }

    for (const entry of entries) {
      if (!entry.isDirectory() || !entry.name.startsWith(CODEX_EXTENSION_PREFIX)) continue;
      const codexPath = path.join(root, entry.name, CODEX_CLI_RELATIVE_PATH);
      if (!fs.existsSync(codexPath)) continue;

      let mtimeMs = 0;
      try {
        mtimeMs = fs.statSync(codexPath).mtimeMs || 0;
      } catch (err) {
        mtimeMs = 0;
      }

      candidates.push({ path: codexPath, mtimeMs });
    }
  }

  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates.map((item) => item.path);
}

function isCommandAvailable(command) {
  const checker = process.platform === 'win32' ? 'where' : 'which';
  const result = spawnSync(checker, [command], { stdio: 'ignore' });
  return result.status === 0;
}

function isUsableCodexPath(value) {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed) return false;

  if (path.isAbsolute(trimmed)) {
    return fs.existsSync(trimmed);
  }

  return isCommandAvailable(trimmed);
}

function resolveDefaultCodexPath() {
  const candidates = listCodexPathCandidates();
  if (candidates.length > 0) {
    return candidates[0];
  }

  return 'codex';
}

function normalizeReasoningEffort(value) {
  const effort = String(value || '').trim().toLowerCase();
  if (!ALLOWED_REASONING_EFFORTS.has(effort)) {
    return DEFAULT_REASONING_EFFORT;
  }
  return effort;
}

function normalizeSettingsDefaults(input) {
  const settings = input && typeof input === 'object' ? { ...input } : {};

  if (typeof settings.engine !== 'string' || !settings.engine.trim()) {
    settings.engine = 'codex';
  }

  if (!isUsableCodexPath(settings.codexPath)) {
    settings.codexPath = resolveDefaultCodexPath();
  }

  const currentModel = typeof settings.defaultModel === 'string' ? settings.defaultModel.trim() : '';
  if (!currentModel || currentModel === 'gpt-4.1-mini') {
    settings.defaultModel = DEFAULT_CODEX_MODEL;
  }

  settings.modelReasoningEffort = normalizeReasoningEffort(settings.modelReasoningEffort);

  if (typeof settings.activeSkillId !== 'string' || !settings.activeSkillId.trim()) {
    settings.activeSkillId = 'xhs';
  }

  if (!Number.isFinite(settings.uiVersion) || settings.uiVersion < UI_VERSION) {
    settings.uiVersion = UI_VERSION;
  }

  if (!Number.isFinite(settings.onboardingSeenVersion) || settings.onboardingSeenVersion < 0) {
    settings.onboardingSeenVersion = 0;
  }

  if (!settings.contextSelectionBySkill || typeof settings.contextSelectionBySkill !== 'object') {
    settings.contextSelectionBySkill = {};
  }

  if (!settings.lastModeBySkill || typeof settings.lastModeBySkill !== 'object') {
    settings.lastModeBySkill = {};
  }

  if (!settings.wizardDraftBySkill || typeof settings.wizardDraftBySkill !== 'object') {
    settings.wizardDraftBySkill = {};
  }

  return settings;
}

function readSettingsWithDefaults() {
  const filePath = settingsPath();
  const current = readJson(filePath, {});
  const normalized = normalizeSettingsDefaults(current);

  if (JSON.stringify(current) !== JSON.stringify(normalized)) {
    writeJson(filePath, normalized);
  }

  return normalized;
}

function promptsPath() {
  return path.join(app.getPath('userData'), 'prompts.json');
}

function ensurePromptsFile() {
  const target = promptsPath();
  if (!fs.existsSync(target)) {
    const seed = fs.readFileSync(DEFAULT_PROMPTS_PATH, 'utf8');
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, seed, 'utf8');
  }
  return target;
}

function resolveContextPath(entryPath) {
  if (!entryPath) return entryPath;
  if (path.isAbsolute(entryPath)) return entryPath;
  return path.resolve(__dirname, entryPath);
}

function loadContextFiles(filePaths) {
  return filePaths.map((filePath) => {
    const resolved = resolveContextPath(filePath);
    let content = '';
    let error = null;

    try {
      content = fs.readFileSync(resolved, 'utf8');
    } catch (err) {
      error = err.message || '无法读取文件';
      content = '[无法以 UTF-8 读取文件]';
    }

    return {
      path: resolved,
      content,
      error
    };
  });
}

function loadContextCatalog() {
  const catalog = readJson(CONTEXT_CATALOG_PATH, { groups: [] });
  const groups = Array.isArray(catalog.groups) ? catalog.groups : [];

  return {
    groups: groups.map((group) => {
      const items = Array.isArray(group.items) ? group.items : [];
      return {
        ...group,
        items: items.map((item) => {
          const resolvedPath = resolveContextPath(item.path);
          return {
            ...item,
            path: resolvedPath,
            exists: fs.existsSync(resolvedPath)
          };
        })
      };
    })
  };
}

function loadSkills() {
  const skills = readJson(SKILLS_PATH, { skills: [] });
  return Array.isArray(skills.skills) ? skills.skills : [];
}

function findSkill(skillId) {
  return loadSkills().find((skill) => skill.id === skillId);
}

async function loadOpenAI() {
  if (!OpenAIClient) {
    const mod = await import('openai');
    OpenAIClient = mod.default;
  }
  return OpenAIClient;
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#f6f2ed',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

function resolveWorkspaceDir(customDir) {
  if (typeof customDir === 'string' && customDir.trim()) {
    return customDir.trim();
  }
  return WORKSPACE_ROOT;
}

function normalizeCodexPath(value) {
  if (typeof value !== 'string') return 'codex';
  const trimmed = value.trim();
  return trimmed || 'codex';
}

function parseCodexJsonLine(line) {
  try {
    return JSON.parse(line);
  } catch (err) {
    return null;
  }
}

function isCommandRunnable(command, args = []) {
  try {
    const result = spawnSync(command, [...args, '--version'], {
      stdio: 'ignore',
      windowsHide: true
    });
    return result.status === 0;
  } catch (err) {
    return false;
  }
}

function resolvePythonCommand() {
  const candidates = [];

  if (fs.existsSync(URL_READER_VENV_PYTHON)) {
    candidates.push({ command: URL_READER_VENV_PYTHON, args: [], source: 'url-reader-venv' });
  }
  candidates.push({ command: 'python', args: [], source: 'system-python' });
  if (process.platform === 'win32') {
    candidates.push({ command: 'py', args: ['-3'], source: 'py-launcher' });
  }

  for (const item of candidates) {
    if (isCommandRunnable(item.command, item.args)) {
      return item;
    }
  }
  return null;
}

function resolveCrawlerRuntime() {
  const python = resolvePythonCommand();
  const keywordScriptSource = fs.existsSync(INTERNAL_KEYWORD_SCRIPT_PATH)
    ? 'internal'
    : (fs.existsSync(EXTERNAL_KEYWORD_SCRIPT_PATH) ? 'external' : 'missing');

  return {
    pythonAvailable: Boolean(python),
    pythonCommand: python ? python.command : '',
    pythonArgs: python ? python.args : [],
    pythonSource: python ? python.source : 'missing',
    bridgeScript: CRAWL_BRIDGE_PATH,
    bridgeExists: fs.existsSync(CRAWL_BRIDGE_PATH),
    urlReaderVenvPython: fs.existsSync(URL_READER_VENV_PYTHON) ? URL_READER_VENV_PYTHON : '',
    keywordScriptSource,
    internalKeywordScript: INTERNAL_KEYWORD_SCRIPT_PATH,
    externalKeywordScript: EXTERNAL_KEYWORD_SCRIPT_PATH
  };
}

function parseCrawlerBridgeResult(stdoutText) {
  const parsed = parseCrawlerBridgeOutput(stdoutText);
  return parsed.result;
}

function parseCrawlerBridgeOutput(stdoutText) {
  const lines = String(stdoutText || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  let resultPayload = null;
  const events = [];
  for (const line of lines) {
    const parsed = parseCodexJsonLine(line);
    if (!parsed || typeof parsed !== 'object') continue;
    if (parsed.type === 'result' && parsed.data && typeof parsed.data === 'object') {
      resultPayload = parsed.data;
    } else if (!resultPayload && parsed.status && typeof parsed.status === 'string') {
      resultPayload = parsed;
    } else {
      events.push(parsed);
    }
  }
  return { result: resultPayload, events };
}

function isPathWithinRoot(targetPath, rootPath) {
  const resolvedRoot = path.resolve(rootPath);
  const resolvedTarget = path.resolve(targetPath);
  const rootLower = resolvedRoot.toLowerCase();
  const targetLower = resolvedTarget.toLowerCase();
  if (targetLower === rootLower) return true;
  return targetLower.startsWith(rootLower + path.sep.toLowerCase());
}

function sanitizePathSegment(value) {
  // Windows forbids <>:"/\|?* and control chars; also avoid trailing dots/spaces.
  return String(value || '')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/[. ]+$/g, '_');
}

function sanitizeRelativeFilePath(relPath) {
  const parts = String(relPath || '').split(/[\\/]+/).filter(Boolean);
  const sanitized = parts.map((part) => sanitizePathSegment(part));
  return sanitized.join(path.sep);
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

ipcMain.handle('settings:get', () => {
  return readSettingsWithDefaults();
});

ipcMain.handle('settings:set', (event, partial) => {
  const current = readSettingsWithDefaults();
  const next = normalizeSettingsDefaults({ ...current, ...partial });
  writeJson(settingsPath(), next);
  return next;
});

ipcMain.handle('prompts:list', () => {
  const filePath = ensurePromptsFile();
  return readJson(filePath, { pipelines: [] });
});

ipcMain.handle('prompts:defaults', () => {
  return readJson(DEFAULT_PROMPTS_PATH, { pipelines: [] });
});

ipcMain.handle('prompts:save', (event, data) => {
  const filePath = ensurePromptsFile();
  writeJson(filePath, data);
  return { ok: true };
});

ipcMain.handle('files:open', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择上下文文件',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: '文本', extensions: ['md', 'txt', 'json'] },
      { name: '全部', extensions: ['*'] }
    ]
  });

  if (result.canceled) {
    return [];
  }

  return result.filePaths.map((filePath) => {
    let content = '';
    try {
      content = fs.readFileSync(filePath, 'utf8');
    } catch (err) {
      content = '[无法以 UTF-8 读取文件]';
    }

    return { path: filePath, content };
  });
});

ipcMain.handle('files:defaults', () => {
  const defaults = readJson(DEFAULT_CONTEXT_PATH, { files: [] });
  const paths = Array.isArray(defaults.files) ? defaults.files : [];
  if (!paths.length) {
    return [];
  }
  return loadContextFiles(paths);
});

ipcMain.handle('files:defaults-for-skill', (event, skillId) => {
  const skill = findSkill(skillId);
  const paths = skill && Array.isArray(skill.defaultContexts) ? skill.defaultContexts : [];
  if (!paths.length) {
    return [];
  }
  return loadContextFiles(paths);
});

ipcMain.handle('skills:list', () => {
  return loadSkills();
});

ipcMain.handle('context:catalog', () => {
  return loadContextCatalog();
});

ipcMain.handle('context:load', (event, paths) => {
  if (!Array.isArray(paths)) {
    return [];
  }
  return loadContextFiles(paths);
});

ipcMain.handle('runs:list', () => {
  return readRuns()
    .map((run) => summarizeRun(run))
    .filter(Boolean);
});

ipcMain.handle('runs:get', (event, runId) => {
  if (typeof runId !== 'string' || !runId.trim()) {
    return null;
  }
  const found = readRuns().find((run) => run && run.id === runId.trim());
  return found || null;
});

ipcMain.handle('runs:save', (event, payload) => {
  const saved = upsertRunRecord(payload);
  return {
    ok: true,
    run: saved,
    summary: summarizeRun(saved)
  };
});

ipcMain.handle('help:get-guide', () => {
  return readJson(UI_GUIDE_PATH, { version: UI_VERSION, steps: [] });
});

ipcMain.handle('wizard:get-schema', () => {
  return readJson(WIZARD_SCHEMA_PATH, { version: UI_VERSION, skills: {} });
});

ipcMain.handle('path:open', async (event, targetPath) => {
  if (typeof targetPath !== 'string' || !targetPath.trim()) {
    return { ok: false, error: 'Path is required' };
  }

  const normalized = path.resolve(targetPath.trim());
  const openError = await shell.openPath(normalized);
  if (openError) {
    return { ok: false, error: openError };
  }
  return { ok: true };
});

ipcMain.handle('outputs:save', (event, payload) => {
  const data = payload && typeof payload === 'object' ? payload : {};
  const files = Array.isArray(data.files) ? data.files : [];

  if (!files.length) {
    return { ok: false, error: 'No files provided' };
  }

  const written = [];

  files.forEach((file) => {
    if (!file || typeof file !== 'object') return;
    const relPath = typeof file.path === 'string' ? file.path.trim() : '';
    if (!relPath) return;
    if (path.isAbsolute(relPath)) {
      throw new Error('Only relative paths are allowed');
    }

    const safeRel = sanitizeRelativeFilePath(relPath);
    const absPath = path.resolve(WORKSPACE_ROOT, safeRel);

    if (!isPathWithinRoot(absPath, WORKSPACE_ROOT)) {
      throw new Error('Path escapes workspace root');
    }

    if (!absPath.toLowerCase().endsWith('.md')) {
      throw new Error('Only .md outputs are allowed');
    }

    fs.mkdirSync(path.dirname(absPath), { recursive: true });
    fs.writeFileSync(absPath, String(file.content || ''), 'utf8');
    written.push(absPath);
  });

  return { ok: true, written };
});

ipcMain.handle('crawler:runtime', () => {
  const runtime = resolveCrawlerRuntime();
  const issues = [];
  const hints = [];

  if (!runtime.bridgeExists) {
    issues.push('missing_bridge');
    hints.push(`未找到抓取桥接脚本：${runtime.bridgeScript}`);
  }

  if (!runtime.pythonAvailable) {
    issues.push('missing_python');
    hints.push('未检测到可用 Python，请安装 Python 3.10+ 并确保命令可用。');
  }

  if (runtime.keywordScriptSource === 'missing') {
    issues.push('missing_keyword_script');
    hints.push(`未找到关键词抓取脚本（内置：${runtime.internalKeywordScript}；外部：${runtime.externalKeywordScript}）。`);
  }

  if (!runtime.urlReaderVenvPython) {
    hints.push(`URL Reader 虚拟环境不存在：${URL_READER_VENV_PYTHON}`);
  }

  return {
    ...runtime,
    issues,
    hints
  };
});

ipcMain.handle('crawler:run', async (event, payload) => {
  const runtime = resolveCrawlerRuntime();
  if (!runtime.bridgeExists) {
    throw new Error(`抓取桥接脚本不存在：${runtime.bridgeScript}`);
  }
  if (!runtime.pythonAvailable) {
    throw new Error('未找到可用 Python，请先安装 Python 3.10+，或确认 url-reader 虚拟环境可用。');
  }

  const request = payload && typeof payload === 'object' ? { ...payload } : {};
  const tempDir = path.join(app.getPath('userData'), 'tmp', 'crawler');
  fs.mkdirSync(tempDir, { recursive: true });
  const tempPayloadPath = path.join(
    tempDir,
    `crawler-payload-${Date.now()}-${Math.floor(Math.random() * 1000)}.json`
  );
  fs.writeFileSync(tempPayloadPath, JSON.stringify(request, null, 2), 'utf8');

  const args = [
    ...(Array.isArray(runtime.pythonArgs) ? runtime.pythonArgs : []),
    CRAWL_BRIDGE_PATH,
    '--payload-file',
    tempPayloadPath
  ];

  return await new Promise((resolve, reject) => {
    let stdout = '';
    let stderr = '';
    let finished = false;

    const finalize = (fn) => {
      if (finished) return;
      finished = true;
      try {
        fs.unlinkSync(tempPayloadPath);
      } catch (err) {
        // ignore cleanup error
      }
      fn();
    };

    const child = spawn(runtime.pythonCommand, args, {
      cwd: WORKSPACE_ROOT,
      windowsHide: true
    });

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');

    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });

    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });

    child.on('error', (err) => {
      const message = err && err.code === 'ENOENT'
        ? '启动抓取失败：未找到 Python 可执行文件。'
        : (err.message || '启动抓取子进程失败');
      finalize(() => reject(new Error(message)));
    });

    child.on('close', (code) => {
      const parsed = parseCrawlerBridgeOutput(stdout);
      const result = parsed.result;
      const normalizedStatus = String(result && result.status ? result.status : '').toLowerCase();
      const okStatus = new Set(['done', 'partial']);

      if (!result) {
        const fallbackMessage = stderr.trim() || `抓取执行失败（退出码 ${code}）`;
        finalize(() => reject(new Error(fallbackMessage)));
        return;
      }

      if (!okStatus.has(normalizedStatus)) {
        const errors = Array.isArray(result.errors) ? result.errors.filter(Boolean) : [];
        const message = errors[0] || stderr.trim() || `抓取执行失败（退出码 ${code}）`;
        finalize(() => reject(new Error(message)));
        return;
      }

      finalize(() => resolve({
        ok: true,
        ...result,
        status: normalizedStatus || 'done',
        events: parsed.events,
        stderr: stderr.trim() || ''
      }));
    });
  });
});

ipcMain.handle('openai:generate-stream', async (event, payload) => {
  const { apiKey, model, instructions, input, temperature, maxOutputTokens } = payload || {};

  if (!apiKey) {
    throw new Error('缺少 API 密钥，请在设置中填写。');
  }

  const OpenAI = await loadOpenAI();
  const client = new OpenAI({ apiKey });
  const sender = event.sender;
  const streamId = `stream_${Date.now()}_${Math.floor(Math.random() * 1000)}`;

  const stream = await client.responses.create({
    model: model || 'gpt-4.1-mini',
    instructions: instructions || undefined,
    input: input || '',
    temperature: Number.isFinite(temperature) ? temperature : undefined,
    max_output_tokens: Number.isFinite(maxOutputTokens) ? maxOutputTokens : undefined,
    stream: true
  });

  (async () => {
    let text = '';
    try {
      for await (const chunk of stream) {
        if (chunk.type === 'response.output_text.delta') {
          text += chunk.delta || '';
          sender.send('openai:stream:delta', { id: streamId, delta: chunk.delta || '' });
        }

        if (chunk.type === 'response.completed') {
          sender.send('openai:stream:done', { id: streamId, text });
          return;
        }
      }
      sender.send('openai:stream:done', { id: streamId, text });
    } catch (err) {
      sender.send('openai:stream:error', { id: streamId, message: err.message || 'Stream error' });
    }
  })();

  return { id: streamId };
});

ipcMain.handle('openai:generate', async (event, payload) => {
  const { apiKey, model, instructions, input, temperature, maxOutputTokens } = payload || {};

  if (!apiKey) {
    throw new Error('缺少 API 密钥，请在设置中填写。');
  }

  const OpenAI = await loadOpenAI();
  const client = new OpenAI({ apiKey });

  const response = await client.responses.create({
    model: model || 'gpt-4.1-mini',
    instructions: instructions || undefined,
    input: input || '',
    temperature: Number.isFinite(temperature) ? temperature : undefined,
    max_output_tokens: Number.isFinite(maxOutputTokens) ? maxOutputTokens : undefined
  });

  return {
    text: response.output_text || '',
    usage: response.usage || null
  };
});

ipcMain.handle('codex:generate-stream', async (event, payload) => {
  const {
    prompt,
    codexPath,
    workingDirectory,
    model,
    modelReasoningEffort
  } = payload || {};

  if (!prompt || typeof prompt !== 'string' || !prompt.trim()) {
    throw new Error('缺少执行内容，请填写指令或输入。');
  }

  const sender = event.sender;
  const streamId = `codex_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
  const cmd = normalizeCodexPath(codexPath);
  const args = ['exec', '--json', '--skip-git-repo-check'];
  const modelName = typeof model === 'string' && model.trim() ? model.trim() : DEFAULT_CODEX_MODEL;
  const reasoningEffort = normalizeReasoningEffort(modelReasoningEffort);

  args.push('-m', modelName);
  args.push('-c', `model_reasoning_effort="${reasoningEffort}"`);
  args.push('-');
  const cwd = resolveWorkspaceDir(workingDirectory);

  let buffer = '';
  let latestText = '';
  let lastError = '';
  let finished = false;

  const child = spawn(cmd, args, {
    cwd,
    windowsHide: true
  });

  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');

  const finish = (type, data) => {
    if (finished) return;
    finished = true;
    sender.send(type, data);
  };

  const emitDelta = (text) => {
    if (!text) return;
    sender.send('codex:stream:delta', { id: streamId, delta: text });
  };

  const handleCodexEvent = (payload) => {
    if (!payload || typeof payload !== 'object') return;

    if (payload.type === 'error') {
      lastError = payload.message || payload.error || 'Codex 执行失败';
      return;
    }

    const item = payload.item;
    if (!item || typeof item !== 'object') return;

    const itemType = item.type;
    if (itemType !== 'agent_message' && itemType !== 'assistant_message') return;
    if (typeof item.text !== 'string') return;

    const nextText = item.text;
    if (nextText.startsWith(latestText)) {
      emitDelta(nextText.slice(latestText.length));
    } else {
      emitDelta(nextText);
    }
    latestText = nextText;
  };

  const flushBuffer = () => {
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || '';
    lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const parsed = parseCodexJsonLine(trimmed);
      if (parsed) {
        handleCodexEvent(parsed);
      }
    });
  };

  child.stdout.on('data', (chunk) => {
    buffer += chunk;
    flushBuffer();
  });

  child.stderr.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (text && !lastError && /error/i.test(text)) {
      lastError = text;
    }
  });

  child.on('error', (err) => {
    const message = err && err.code === 'ENOENT'
      ? '未找到 Codex CLI，请先安装 @openai/codex 并确保 PATH 可用。'
      : (err.message || 'Codex 启动失败');
    finish('codex:stream:error', { id: streamId, message });
  });

  child.on('close', (code) => {
    if (buffer.trim()) {
      const parsed = parseCodexJsonLine(buffer.trim());
      if (parsed) {
        handleCodexEvent(parsed);
      }
    }

    if (finished) return;

    if (code === 0) {
      finish('codex:stream:done', { id: streamId, text: latestText });
    } else {
      const message = lastError || `Codex 退出码 ${code}`;
      finish('codex:stream:error', { id: streamId, message });
    }
  });

  child.stdin.write(prompt);
  child.stdin.end();

  return { id: streamId };
});
