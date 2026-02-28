const state = {
  settings: {
    apiKey: '',
    defaultModel: 'gpt-4.1-mini',
    temperature: 0.4,
    maxOutputTokens: 1200,
    engine: 'openai',
    codexPath: '',
    activeSkillId: 'xhs',
    contextSelectionBySkill: {}
  },
  files: [],
  filesBySkill: {},
  pipelines: [],
  pipeline: null,
  skills: [],
  activeSkillId: 'xhs',
  quickTemplates: {},
  quickTemplateLabels: {},
  outputs: {},
  contextCatalog: { groups: [] },
  stageStatus: {},
  streamMap: {},
  streamResolvers: {},
  progress: { current: 0, total: 0 }
};

const elements = {
  status: document.getElementById('status'),
  brief: document.getElementById('briefInput'),
  filesList: document.getElementById('filesList'),
  pipelineList: document.getElementById('pipelineList'),
  outputList: document.getElementById('outputList'),
  skillButtons: document.getElementById('skillButtons'),
  skillDesc: document.getElementById('skillDesc'),
  filterSearch: document.getElementById('filterSearch'),
  filterList: document.getElementById('filterList'),
  filterCount: document.getElementById('filterCount'),
  clearFilters: document.getElementById('clearFilters'),
  templateSop: document.getElementById('templateSop'),
  templateTopics: document.getElementById('templateTopics'),
  templateHeadline: document.getElementById('templateHeadline'),
  loadDefaults: document.getElementById('loadDefaults'),
  addFiles: document.getElementById('addFiles'),
  addStage: document.getElementById('addStage'),
  runAll: document.getElementById('runAll'),
  previewPrompt: document.getElementById('previewPrompt'),
  openSettings: document.getElementById('openSettings'),
  closeSettings: document.getElementById('closeSettings'),
  settingsModal: document.getElementById('settingsModal'),
  engineSelect: document.getElementById('engineSelect'),
  codexPathInput: document.getElementById('codexPathInput'),
  apiSettingsGroup: document.getElementById('apiSettings'),
  codexSettingsGroup: document.getElementById('codexSettings'),
  apiKeyInput: document.getElementById('apiKeyInput'),
  defaultModel: document.getElementById('defaultModel'),
  temperatureInput: document.getElementById('temperatureInput'),
  maxTokensInput: document.getElementById('maxTokensInput'),
  saveSettings: document.getElementById('saveSettings'),
  progressLabel: document.getElementById('progressLabel'),
  progressFill: document.getElementById('progressFill')
};

function setStatus(message) {
  elements.status.textContent = message;
}

function getEngine() {
  return state.settings.engine || 'openai';
}

function updateEngineUI() {
  const isCodex = getEngine() === 'codex';
  if (elements.apiSettingsGroup) {
    elements.apiSettingsGroup.style.display = isCodex ? 'none' : 'flex';
  }
  if (elements.codexSettingsGroup) {
    elements.codexSettingsGroup.style.display = isCodex ? 'flex' : 'none';
  }
}

function setProgress(current, total) {
  state.progress.current = current;
  state.progress.total = total;
  const safeTotal = total || 0;
  const safeCurrent = current || 0;
  const percent = safeTotal ? Math.min(100, Math.round((safeCurrent / safeTotal) * 100)) : 0;
  elements.progressLabel.textContent = `${safeCurrent}/${safeTotal}`;
  elements.progressFill.style.width = `${percent}%`;
}

function getStatusLabel(status) {
  switch (status) {
    case 'running':
      return '执行中';
    case 'done':
      return '完成';
    case 'error':
      return '错误';
    default:
      return '待执行';
  }
}

function renderTemplate(template, vars) {
  if (!template) return '';
  return template.replace(/\{\{(\w+)\}\}/g, (match, key) => {
    return vars[key] ?? '';
  });
}

function makeContextText() {
  const selected = state.files.filter((file) => file.include);
  if (!selected.length) return '';
  return selected
    .map((file) => `File: ${file.path}\n${file.content}`)
    .join('\n\n---\n\n');
}

function getFileEntry(path) {
  return state.files.find((file) => file.path === path);
}

function getSelectedCatalogPaths() {
  return state.files
    .filter((file) => file.source === 'catalog' && file.include)
    .map((file) => file.path);
}

async function persistContextSelection() {
  const selection = getSelectedCatalogPaths();
  if (!state.settings.contextSelectionBySkill) {
    state.settings.contextSelectionBySkill = {};
  }
  state.settings.contextSelectionBySkill[state.activeSkillId] = selection;
  await window.api.saveSettings({
    activeSkillId: state.activeSkillId,
    contextSelectionBySkill: state.settings.contextSelectionBySkill
  });
  renderFilterCount();
}

function filterMatches(item, query) {
  if (!query) return true;
  const text = `${item.label} ${(item.tags || []).join(' ')} ${item.path}`.toLowerCase();
  return text.includes(query.toLowerCase());
}

function matchesSkill(targetSkills, skillId) {
  if (!targetSkills || !targetSkills.length) return true;
  return targetSkills.includes(skillId);
}

function getActiveSkill() {
  return state.skills.find((skill) => skill.id === state.activeSkillId) || state.skills[0] || null;
}

function updateQuickTemplateUI(skill) {
  if (!skill) return;
  state.quickTemplates = skill.quickTemplates || {};
  state.quickTemplateLabels = skill.quickTemplateLabels || {};

  if (elements.templateSop && state.quickTemplateLabels.sop) {
    elements.templateSop.textContent = state.quickTemplateLabels.sop;
  }
  if (elements.templateTopics && state.quickTemplateLabels.topics) {
    elements.templateTopics.textContent = state.quickTemplateLabels.topics;
  }
  if (elements.templateHeadline && state.quickTemplateLabels.headline) {
    elements.templateHeadline.textContent = state.quickTemplateLabels.headline;
  }
}

function renderSkillButtons() {
  if (!elements.skillButtons) return;
  elements.skillButtons.innerHTML = '';

  state.skills.forEach((skill) => {
    const btn = document.createElement('button');
    btn.className = 'ghost';
    if (skill.id === state.activeSkillId) {
      btn.classList.add('active');
    }
    btn.textContent = skill.name;
    btn.addEventListener('click', () => applySkill(skill.id));
    elements.skillButtons.appendChild(btn);
  });

  if (elements.skillDesc) {
    const active = getActiveSkill();
    elements.skillDesc.textContent = active ? active.description || '' : '';
  }
}

async function applySkill(skillId) {
  const skill = state.skills.find((item) => item.id === skillId);
  if (!skill) return;

  if (state.activeSkillId && state.files.length) {
    state.filesBySkill[state.activeSkillId] = state.files;
  }

  state.activeSkillId = skill.id;
  state.settings.activeSkillId = skill.id;

  await window.api.saveSettings({
    activeSkillId: skill.id,
    contextSelectionBySkill: state.settings.contextSelectionBySkill || {}
  });

  ensurePipelineForSkill(skill);
  state.outputs = {};
  state.stageStatus = {};
  state.streamMap = {};
  state.streamResolvers = {};

  updateQuickTemplateUI(skill);
  if (elements.brief && skill.briefPlaceholder) {
    elements.brief.placeholder = skill.briefPlaceholder;
  }
  renderSkillButtons();
  renderPipeline();
  renderOutputs();
  setProgress(0, state.pipeline ? state.pipeline.stages.length : 0);

  await loadFilesForSkill(skill);
}

async function loadFilesForSkill(skill) {
  const cached = state.filesBySkill[skill.id];
  if (cached) {
    state.files = cached;
    renderFiles();
    renderContextFilters();
    return;
  }
  state.files = [];
  await hydrateContextSelection(skill);
  state.filesBySkill[skill.id] = state.files;
}

function ensurePipelineForSkill(skill) {
  if (!skill) return;
  const existing = state.pipelines.find((pipe) => pipe.id === skill.pipelineId);
  if (existing) {
    state.pipeline = existing;
    return;
  }

  const fallback = {
    id: skill.pipelineId,
    name: skill.name || '内容流程',
    stages: []
  };
  state.pipelines.push(fallback);
  state.pipeline = fallback;
}

function createStage() {
  return {
    id: `stage_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
    name: '新步骤',
    model: '',
    instructions: '你是严谨的结构化写作助手。',
    template: '【参考资料】\n{{context}}\n\n【用户Brief】\n{{input}}\n\n请输出下一步的可交付内容。'
  };
}

function renderFiles() {
  elements.filesList.innerHTML = '';

  if (!state.files.length) {
    const empty = document.createElement('div');
    empty.className = 'file-card';
    empty.textContent = '暂无上下文文件。';
    elements.filesList.appendChild(empty);
    return;
  }

  state.files.forEach((file, index) => {
    const card = document.createElement('div');
    card.className = 'file-card';

    const info = document.createElement('div');
    info.className = 'file-info';

    const name = document.createElement('div');
    name.textContent = file.path.split('\\').pop();

    const pathEl = document.createElement('div');
    pathEl.className = 'file-path';
    pathEl.textContent = file.path;

    info.appendChild(name);
    info.appendChild(pathEl);

    const actions = document.createElement('div');
    actions.className = 'stage-actions';

    const toggle = document.createElement('button');
    toggle.className = 'ghost';
    toggle.textContent = file.include ? '已启用' : '已停用';
    toggle.addEventListener('click', () => {
      file.include = !file.include;
      if (file.source === 'catalog') {
        persistContextSelection();
        renderContextFilters();
      }
      renderFiles();
    });

    const remove = document.createElement('button');
    remove.className = 'ghost';
    remove.textContent = '移除';
    remove.addEventListener('click', () => {
      state.files.splice(index, 1);
      renderFiles();
    });

    actions.appendChild(toggle);
    actions.appendChild(remove);

    card.appendChild(info);
    card.appendChild(actions);
    elements.filesList.appendChild(card);
  });
}

function renderFilterCount() {
  if (!elements.filterCount) return;
  const count = getSelectedCatalogPaths().length;
  elements.filterCount.textContent = `已选 ${count}`;
}

function renderContextFilters() {
  if (!elements.filterList) return;
  elements.filterList.innerHTML = '';

  const query = elements.filterSearch ? elements.filterSearch.value.trim() : '';
  const groups = state.contextCatalog.groups || [];
  const skillId = state.activeSkillId;

  groups.forEach((group) => {
    if (!matchesSkill(group.skills, skillId)) {
      return;
    }

    const items = (group.items || []).filter((item) => {
      if (!matchesSkill(item.skills || group.skills, skillId)) {
        return false;
      }
      return filterMatches(item, query);
    });
    if (!items.length) return;

    const groupEl = document.createElement('div');
    groupEl.className = 'filter-group';

    const title = document.createElement('div');
    title.className = 'filter-group-title';
    title.textContent = group.title || '分组';

    groupEl.appendChild(title);

    items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'filter-item';

      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.disabled = item.exists === false;

      const fileEntry = getFileEntry(item.path);
      checkbox.checked = Boolean(fileEntry && fileEntry.include);

      checkbox.addEventListener('change', async () => {
        if (!checkbox.checked) {
          if (fileEntry) {
            fileEntry.include = false;
          }
          await persistContextSelection();
          renderFiles();
          renderContextFilters();
          return;
        }

        if (fileEntry) {
          fileEntry.include = true;
        } else {
          const loaded = await window.api.loadContextFiles([item.path]);
          loaded.forEach((file) => {
            state.files.push({ ...file, include: true, source: 'catalog', skillId: state.activeSkillId });
          });
        }

        await persistContextSelection();
        renderFiles();
        renderContextFilters();
      });

      const titleEl = document.createElement('span');
      titleEl.textContent = item.label || '未命名';

      const subtitle = document.createElement('small');
      subtitle.textContent = item.exists === false ? '文件缺失' : item.path;

      const titleRow = document.createElement('div');
      titleRow.style.display = 'flex';
      titleRow.style.alignItems = 'center';
      titleRow.style.gap = '6px';
      titleRow.appendChild(checkbox);
      titleRow.appendChild(titleEl);

      label.appendChild(titleRow);
      label.appendChild(subtitle);

      const tagWrap = document.createElement('div');
      tagWrap.className = 'filter-tags';
      (item.tags || []).forEach((tag) => {
        const tagEl = document.createElement('span');
        tagEl.className = 'filter-tag';
        tagEl.textContent = tag;
        tagWrap.appendChild(tagEl);
      });

      row.appendChild(label);
      if (tagWrap.childNodes.length) {
        row.appendChild(tagWrap);
      }

      groupEl.appendChild(row);
    });

    elements.filterList.appendChild(groupEl);
  });

  renderFilterCount();
}

function renderPipeline() {
  const activeSkill = getActiveSkill();
  ensurePipelineForSkill(activeSkill);
  elements.pipelineList.innerHTML = '';

  if (!state.pipeline.stages.length) {
    const empty = document.createElement('div');
    empty.className = 'stage-card';
    empty.textContent = '暂无步骤，请先新增一步。';
    elements.pipelineList.appendChild(empty);
    return;
  }

  state.pipeline.stages.forEach((stage, index) => {
    const card = document.createElement('div');
    card.className = 'stage-card';

    const header = document.createElement('div');
    header.className = 'stage-header';

    const nameInput = document.createElement('input');
    nameInput.value = stage.name;
    nameInput.addEventListener('input', (event) => {
      stage.name = event.target.value;
      queueSavePrompts();
      renderOutputs();
    });

    const actions = document.createElement('div');
    actions.className = 'stage-actions';

    const status = document.createElement('div');
    const statusValue = state.stageStatus[stage.id] || 'idle';
    status.className = `stage-status ${statusValue}`;
    status.textContent = getStatusLabel(statusValue);

    const runButton = document.createElement('button');
    runButton.className = 'primary';
    runButton.textContent = '执行';
    runButton.disabled = statusValue === 'running';
    runButton.addEventListener('click', () => runStage(index));

    const deleteButton = document.createElement('button');
    deleteButton.className = 'ghost';
    deleteButton.textContent = '删除';
    deleteButton.addEventListener('click', () => {
      state.pipeline.stages.splice(index, 1);
      delete state.outputs[stage.id];
      queueSavePrompts();
      renderPipeline();
      renderOutputs();
    });

    actions.appendChild(status);
    actions.appendChild(runButton);
    actions.appendChild(deleteButton);

    header.appendChild(nameInput);
    header.appendChild(actions);

    const meta = document.createElement('div');
    meta.className = 'stage-meta';

    const modelInput = document.createElement('input');
    modelInput.placeholder = `模型（默认 ${state.settings.defaultModel}）`;
    modelInput.value = stage.model || '';
    modelInput.addEventListener('input', (event) => {
      stage.model = event.target.value;
      queueSavePrompts();
    });

    const temperatureInput = document.createElement('input');
    temperatureInput.placeholder = `温度（默认 ${state.settings.temperature}）`;
    temperatureInput.type = 'number';
    temperatureInput.min = '0';
    temperatureInput.max = '2';
    temperatureInput.step = '0.1';
    temperatureInput.value = Number.isFinite(stage.temperature) ? stage.temperature : '';
    temperatureInput.addEventListener('input', (event) => {
      const value = parseFloat(event.target.value);
      stage.temperature = Number.isFinite(value) ? value : null;
      queueSavePrompts();
    });

    meta.appendChild(modelInput);
    meta.appendChild(temperatureInput);

    const instructions = document.createElement('textarea');
    instructions.value = stage.instructions || '';
    instructions.addEventListener('input', (event) => {
      stage.instructions = event.target.value;
      queueSavePrompts();
    });

    const template = document.createElement('textarea');
    template.value = stage.template || '';
    template.addEventListener('input', (event) => {
      stage.template = event.target.value;
      queueSavePrompts();
    });

    const hints = document.createElement('div');
    hints.className = 'file-path';
    hints.textContent = '模板变量：{{input}} {{context}} {{prev}}';

    card.appendChild(header);
    card.appendChild(meta);
    card.appendChild(instructions);
    card.appendChild(template);
    card.appendChild(hints);

    elements.pipelineList.appendChild(card);
  });
}

function renderOutputs() {
  elements.outputList.innerHTML = '';

  if (!state.pipeline || !state.pipeline.stages.length) {
    const empty = document.createElement('div');
    empty.className = 'output-card';
    empty.textContent = '输出将显示在这里。';
    elements.outputList.appendChild(empty);
    return;
  }

  state.pipeline.stages.forEach((stage) => {
    const card = document.createElement('div');
    card.className = 'output-card';

    const title = document.createElement('h4');
    title.textContent = stage.name || '步骤输出';

    const pre = document.createElement('pre');
    pre.textContent = state.outputs[stage.id] || '暂无输出。';

    card.appendChild(title);
    card.appendChild(pre);
    elements.outputList.appendChild(card);
  });
}

let outputRenderQueued = false;
function queueRenderOutputs() {
  if (outputRenderQueued) return;
  outputRenderQueued = true;
  requestAnimationFrame(() => {
    renderOutputs();
    outputRenderQueued = false;
  });
}

function setStageStatus(stageId, status) {
  state.stageStatus[stageId] = status;
  renderPipeline();
}

async function runStage(index) {
  const stage = state.pipeline.stages[index];
  if (!stage) return;

  const engine = getEngine();
  if (engine === 'openai' && !state.settings.apiKey) {
    setStatus('请先在“设置”里填写 API 密钥');
    showSettings(true);
    return;
  }

  setStatus(`正在执行：${stage.name}`);
  setStageStatus(stage.id, 'running');
  setProgress(index + 1, state.pipeline.stages.length);

  const context = makeContextText();
  const prevOutput = index > 0 ? state.outputs[state.pipeline.stages[index - 1].id] || '' : '';
  const input = renderTemplate(stage.template, {
    input: elements.brief.value,
    context,
    prev: prevOutput
  });
  const instructionText = stage.instructions || '';

  try {
    state.outputs[stage.id] = engine === 'codex' ? '正在调用 Codex…' : '正在生成中…';
    queueRenderOutputs();
    let streamResult = null;
    if (engine === 'codex') {
      const parts = [];
      if (instructionText.trim()) {
        parts.push(`【指令】\n${instructionText}`);
      }
      if (input.trim()) {
        parts.push(`【输入】\n${input}`);
      }
      const prompt = parts.join('\n\n').trim();
      streamResult = await window.api.runCodexStream({
        prompt,
        codexPath: state.settings.codexPath
      });
    } else {
      const payload = {
        apiKey: state.settings.apiKey,
        model: stage.model || state.settings.defaultModel,
        instructions: instructionText,
        input,
        temperature: Number.isFinite(stage.temperature) ? stage.temperature : state.settings.temperature,
        maxOutputTokens: state.settings.maxOutputTokens
      };
      streamResult = await window.api.runOpenAIStream(payload);
    }
    const { id } = streamResult || {};
    state.streamMap[id] = stage.id;

    return await new Promise((resolve, reject) => {
      state.streamResolvers[stage.id] = { resolve, reject };
    });
  } catch (err) {
    const message = err.message || '未知错误';
    setStatus(`错误：${message}`);
    setStageStatus(stage.id, 'error');
    state.outputs[stage.id] = `发生错误：${message}`;
    queueRenderOutputs();
  }
}

async function runAll() {
  if (!state.pipeline || !state.pipeline.stages.length) return;
  for (let i = 0; i < state.pipeline.stages.length; i += 1) {
    try {
      await runStage(i);
    } catch (err) {
      setStatus(`错误：${err.message}`);
      break;
    }
  }
}

let saveTimer = null;
function queueSavePrompts() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(savePrompts, 400);
}

async function savePrompts() {
  if (!state.pipeline) return;
  if (!state.pipelines.length) {
    state.pipelines = [state.pipeline];
  }
  await window.api.savePrompts({ pipelines: state.pipelines });
}

function showSettings(show) {
  elements.settingsModal.setAttribute('aria-hidden', show ? 'false' : 'true');
}

async function loadSettings() {
  const settings = await window.api.getSettings();
  const legacySelection = Array.isArray(settings.contextSelection) ? settings.contextSelection : null;
  const contextSelectionBySkill = typeof settings.contextSelectionBySkill === 'object' && settings.contextSelectionBySkill !== null
    ? settings.contextSelectionBySkill
    : {};

  if (legacySelection && !contextSelectionBySkill.xhs) {
    contextSelectionBySkill.xhs = legacySelection;
  }

  state.settings = {
    apiKey: settings.apiKey || '',
    defaultModel: settings.defaultModel || 'gpt-4.1-mini',
    temperature: Number.isFinite(settings.temperature) ? settings.temperature : 0.4,
    maxOutputTokens: Number.isFinite(settings.maxOutputTokens) ? settings.maxOutputTokens : 1200,
    engine: settings.engine || (settings.apiKey ? 'openai' : 'codex'),
    codexPath: settings.codexPath || '',
    activeSkillId: settings.activeSkillId || 'xhs',
    contextSelectionBySkill
  };
  state.activeSkillId = state.settings.activeSkillId;

  elements.apiKeyInput.value = state.settings.apiKey;
  elements.defaultModel.value = state.settings.defaultModel;
  elements.temperatureInput.value = state.settings.temperature;
  elements.maxTokensInput.value = state.settings.maxOutputTokens;
  if (elements.engineSelect) {
    elements.engineSelect.value = state.settings.engine;
  }
  if (elements.codexPathInput) {
    elements.codexPathInput.value = state.settings.codexPath;
  }
  updateEngineUI();
}

async function loadPrompts() {
  const data = await window.api.listPrompts();
  const defaults = await window.api.getDefaultPrompts();
  const pipelines = Array.isArray(data.pipelines) ? data.pipelines : [];
  const defaultPipelines = Array.isArray(defaults.pipelines) ? defaults.pipelines : [];
  const pipelineMap = new Map();

  pipelines.forEach((pipe) => {
    if (pipe && pipe.id) {
      pipelineMap.set(pipe.id, pipe);
    }
  });

  defaultPipelines.forEach((pipe) => {
    if (pipe && pipe.id && !pipelineMap.has(pipe.id)) {
      pipelineMap.set(pipe.id, pipe);
    }
  });

  state.pipelines = Array.from(pipelineMap.values());
}

async function loadSkills() {
  const skills = await window.api.getSkills();
  state.skills = Array.isArray(skills) ? skills : [];
  if (!state.skills.length) {
    state.skills = [
      { id: 'xhs', name: '小红书内容生成', description: '', pipelineId: 'xhs' },
      { id: 'wechat', name: '公众号爆文写作', description: '', pipelineId: 'wechat' }
    ];
  }
}

async function addFiles() {
  const selected = await window.api.openFiles();
  if (!selected.length) return;

  selected.forEach((file) => {
    const existing = state.files.find((item) => item.path === file.path);
    if (!existing) {
      state.files.push({ ...file, include: true, source: 'manual', skillId: state.activeSkillId });
    }
  });

  renderFiles();
}

async function loadContextCatalog() {
  const catalog = await window.api.getContextCatalog();
  state.contextCatalog = catalog || { groups: [] };
  renderContextFilters();
}

async function hydrateContextSelection(skill) {
  if (!skill) return;
  let files = [];
  const selection = (state.settings.contextSelectionBySkill || {})[skill.id] || [];

  if (selection.length) {
    files = await window.api.loadContextFiles(selection);
  } else {
    files = await window.api.loadDefaultContextForSkill(skill.id);
    if (!files.length) {
      files = await window.api.loadDefaultContext();
    }
  }

  if (!files.length) {
    setStatus('未找到默认上下文文件');
    return;
  }

  state.files.forEach((file) => {
    if (file.source === 'catalog') {
      file.include = false;
    }
  });

  files.forEach((file) => {
    const existing = state.files.find((item) => item.path === file.path);
    if (!existing) {
      state.files.push({ ...file, include: true, source: 'catalog', skillId: skill.id });
    } else {
      existing.include = true;
    }
  });

  await persistContextSelection();
  renderFiles();
  renderContextFilters();
  state.filesBySkill[state.activeSkillId] = state.files;
  setStatus('已加载默认上下文');
}

async function loadDefaultContext() {
  const skill = getActiveSkill();
  const files = skill ? await window.api.loadDefaultContextForSkill(skill.id) : await window.api.loadDefaultContext();
  if (!files.length) {
    setStatus('未找到默认上下文文件');
    return;
  }

  state.files.forEach((file) => {
    if (file.source === 'catalog') {
      file.include = false;
    }
  });

  files.forEach((file) => {
    const existing = state.files.find((item) => item.path === file.path);
    if (!existing) {
      state.files.push({ ...file, include: true, source: 'catalog', skillId: skill ? skill.id : undefined });
    } else {
      existing.include = true;
    }
  });

  await persistContextSelection();
  renderFiles();
  renderContextFilters();
  setStatus('已加载默认上下文');
}

function previewStage() {
  if (!state.pipeline || !state.pipeline.stages.length) return;
  const stage = state.pipeline.stages[0];
  const context = makeContextText();
  const input = renderTemplate(stage.template, {
    input: elements.brief.value,
    context,
    prev: ''
  });
  state.outputs[stage.id] = `--- 指令 ---\n${stage.instructions || ''}\n\n--- 输入 ---\n${input}`;
  renderOutputs();
}

function handleStreamEvent(event) {
  const stageId = state.streamMap[event.id];
  if (!stageId) return;

  if (event.type === 'delta') {
    state.outputs[stageId] = `${state.outputs[stageId] || ''}${event.delta || ''}`;
    queueRenderOutputs();
    return;
  }

  if (event.type === 'done') {
    if (event.text) {
      state.outputs[stageId] = event.text;
    }
    setStageStatus(stageId, 'done');
    setStatus('步骤已完成');
    queueRenderOutputs();
    delete state.streamMap[event.id];
    if (state.streamResolvers[stageId]) {
      state.streamResolvers[stageId].resolve();
      delete state.streamResolvers[stageId];
    }
    return;
  }

  if (event.type === 'error') {
    setStageStatus(stageId, 'error');
    setStatus(`错误：${event.message}`);
    delete state.streamMap[event.id];
    if (state.streamResolvers[stageId]) {
      state.streamResolvers[stageId].reject(new Error(event.message));
      delete state.streamResolvers[stageId];
    }
  }
}

function applyQuickTemplate(type) {
  const text = state.quickTemplates[type];
  const label = state.quickTemplateLabels[type] || '模板';
  if (!text) {
    setStatus('当前技能未配置该模板');
    return;
  }
  elements.brief.value = text;
  setStatus(`已载入${label}`);
}

function bindEvents() {
  if (elements.filterSearch) {
    elements.filterSearch.addEventListener('input', renderContextFilters);
  }
  if (elements.clearFilters) {
    elements.clearFilters.addEventListener('click', async () => {
      state.files.forEach((file) => {
        if (file.source === 'catalog') {
          file.include = false;
        }
      });
      await persistContextSelection();
      renderFiles();
      renderContextFilters();
    });
  }
  elements.loadDefaults.addEventListener('click', loadDefaultContext);
  elements.addFiles.addEventListener('click', addFiles);
  elements.addStage.addEventListener('click', () => {
    ensurePipelineForSkill(getActiveSkill());
    state.pipeline.stages.push(createStage());
    queueSavePrompts();
    renderPipeline();
    renderOutputs();
  });
  elements.runAll.addEventListener('click', runAll);
  elements.previewPrompt.addEventListener('click', previewStage);
  elements.openSettings.addEventListener('click', () => showSettings(true));
  elements.closeSettings.addEventListener('click', () => showSettings(false));
  if (elements.engineSelect) {
    elements.engineSelect.addEventListener('change', () => {
      state.settings.engine = elements.engineSelect.value;
      updateEngineUI();
    });
  }
  elements.saveSettings.addEventListener('click', async () => {
    const payload = {
      engine: elements.engineSelect ? elements.engineSelect.value : state.settings.engine,
      codexPath: elements.codexPathInput ? elements.codexPathInput.value.trim() : '',
      apiKey: elements.apiKeyInput.value.trim(),
      defaultModel: elements.defaultModel.value.trim() || 'gpt-4.1-mini',
      temperature: parseFloat(elements.temperatureInput.value),
      maxOutputTokens: parseInt(elements.maxTokensInput.value, 10),
      activeSkillId: state.activeSkillId,
      contextSelectionBySkill: state.settings.contextSelectionBySkill || {}
    };
    const next = await window.api.saveSettings(payload);
    state.settings = {
      apiKey: next.apiKey || '',
      defaultModel: next.defaultModel || 'gpt-4.1-mini',
      temperature: Number.isFinite(next.temperature) ? next.temperature : 0.4,
      maxOutputTokens: Number.isFinite(next.maxOutputTokens) ? next.maxOutputTokens : 1200,
      engine: next.engine || state.settings.engine,
      codexPath: next.codexPath || '',
      activeSkillId: next.activeSkillId || state.activeSkillId,
      contextSelectionBySkill: typeof next.contextSelectionBySkill === 'object' && next.contextSelectionBySkill !== null
        ? next.contextSelectionBySkill
        : {}
    };
    showSettings(false);
    setStatus('设置已保存');
    updateEngineUI();
    renderPipeline();
  });

  if (elements.templateSop) {
    elements.templateSop.addEventListener('click', () => applyQuickTemplate('sop'));
  }

  if (elements.templateTopics) {
    elements.templateTopics.addEventListener('click', () => applyQuickTemplate('topics'));
  }

  if (elements.templateHeadline) {
    elements.templateHeadline.addEventListener('click', () => applyQuickTemplate('headline'));
  }
}

async function init() {
  await loadSettings();
  await loadPrompts();
  await loadSkills();
  await loadContextCatalog();
  const defaultSkill = state.skills.find((skill) => skill.id === state.activeSkillId) || state.skills[0];
  if (defaultSkill) {
    await applySkill(defaultSkill.id);
  }
  window.api.onStreamEvent(handleStreamEvent);
  window.api.onCodexEvent(handleStreamEvent);
  bindEvents();
}

init();
