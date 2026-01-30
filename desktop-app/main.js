const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const DEFAULT_PROMPTS_PATH = path.join(__dirname, 'data', 'prompts.default.json');
const DEFAULT_CONTEXT_PATH = path.join(__dirname, 'data', 'default-context.json');
const CONTEXT_CATALOG_PATH = path.join(__dirname, 'data', 'context-catalog.json');
const SKILLS_PATH = path.join(__dirname, 'data', 'skills.json');
const WORKSPACE_ROOT = path.resolve(__dirname, '..');
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
  return readJson(settingsPath(), {});
});

ipcMain.handle('settings:set', (event, partial) => {
  const current = readJson(settingsPath(), {});
  const next = { ...current, ...partial };
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
  const { prompt, codexPath, workingDirectory } = payload || {};

  if (!prompt || typeof prompt !== 'string' || !prompt.trim()) {
    throw new Error('缺少执行内容，请填写指令或输入。');
  }

  const sender = event.sender;
  const streamId = `codex_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
  const cmd = normalizeCodexPath(codexPath);
  const args = ['exec', '--json', '--skip-git-repo-check', '-'];
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
