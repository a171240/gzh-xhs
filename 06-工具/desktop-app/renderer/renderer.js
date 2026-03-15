const STEP_DEFS = [
  { step: 1, title: '选择技能/模式' },
  { step: 2, title: '填写 Brief' },
  { step: 3, title: '选择素材包' },
  { step: 4, title: '确认参数' },
  { step: 5, title: '执行与交付' }
];

const DEFAULT_CODEX_MODEL = 'gpt-5.4';
const DEFAULT_REASONING_EFFORT = 'xhigh';
const ALLOWED_REASONING_EFFORTS = new Set(['low', 'medium', 'high', 'xhigh']);

const state = {
  settings: {
    apiKey: '',
    defaultModel: DEFAULT_CODEX_MODEL,
    modelReasoningEffort: DEFAULT_REASONING_EFFORT,
    temperature: 0.4,
    maxOutputTokens: 1200,
    engine: 'codex',
    codexPath: 'codex',
    activeSkillId: 'xhs',
    contextSelectionBySkill: {},
    lastModeBySkill: {},
    wizardDraftBySkill: {},
    uiVersion: 2,
    onboardingSeenVersion: 0
  },
  skills: [],
  pipelines: [],
  wizardSummaryTemplates: {},
  contextCatalog: { groups: [] },
  wizardSchema: { version: 2, skills: {} },
  guide: { title: '', intro: '', steps: [] },
  filesBySkill: {},
  proOutputsBySkill: {},
  proStageStatusBySkill: {},
  activeSkillId: 'xhs',
  runs: [],
  runDetails: {},
  streamHandlers: {},
  progress: { current: 0, total: 0 },
  ui: {
    view: 'home',
    wizard: {
      skillId: 'xhs',
      step: 1,
      lastRunId: '',
      liveStages: {}
    },
    selectedRunId: '',
    guideIndex: 0
  }
};

const el = {
  status: document.getElementById('status'),
  progressLabel: document.getElementById('progressLabel'),
  progressFill: document.getElementById('progressFill'),

  navHome: document.getElementById('navHome'),
  navExecution: document.getElementById('navExecution'),
  navPro: document.getElementById('navPro'),
  openGuide: document.getElementById('openGuide'),
  openSettings: document.getElementById('openSettings'),

  viewHome: document.getElementById('viewHome'),
  viewWizard: document.getElementById('viewWizard'),
  viewExecution: document.getElementById('viewExecution'),
  viewPro: document.getElementById('viewPro'),

  homeXhs: document.getElementById('homeXhs'),
  homeWechat: document.getElementById('homeWechat'),
  homeCrawler: document.getElementById('homeCrawler'),
  homeRuns: document.getElementById('homeRuns'),
  homeSettings: document.getElementById('homeSettings'),

  wizardSkillTitle: document.getElementById('wizardSkillTitle'),
  wizardStepNav: document.getElementById('wizardStepNav'),
  wizardExample: document.getElementById('wizardExample'),
  wizardBackHome: document.getElementById('wizardBackHome'),
  wizardBreadcrumb: document.getElementById('wizardBreadcrumb'),
  wizardSkillCards: document.getElementById('wizardSkillCards'),
  wizardModeCards: document.getElementById('wizardModeCards'),
  wizardBriefAccordion: document.getElementById('wizardBriefAccordion'),
  wizardContextSummary: document.getElementById('wizardContextSummary'),
  wizardContextAccordion: document.getElementById('wizardContextAccordion'),
  wizardLoadDefaults: document.getElementById('wizardLoadDefaults'),
  wizardAddFiles: document.getElementById('wizardAddFiles'),
  wizardBriefPreview: document.getElementById('wizardBriefPreview'),
  wizardEngine: document.getElementById('wizardEngine'),
  wizardModel: document.getElementById('wizardModel'),
  wizardReasoningEffort: document.getElementById('wizardReasoningEffort'),
  wizardTemperature: document.getElementById('wizardTemperature'),
  wizardMaxTokens: document.getElementById('wizardMaxTokens'),
  wizardSaveParams: document.getElementById('wizardSaveParams'),
  wizardRun: document.getElementById('wizardRun'),
  wizardOpenRunDetail: document.getElementById('wizardOpenRunDetail'),
  wizardLive: document.getElementById('wizardLive'),
  wizardPrev: document.getElementById('wizardPrev'),
  wizardNext: document.getElementById('wizardNext'),

  refreshRuns: document.getElementById('refreshRuns'),
  runsList: document.getElementById('runsList'),
  runDetail: document.getElementById('runDetail'),

  proSkillButtons: document.getElementById('proSkillButtons'),
  proSkillDesc: document.getElementById('proSkillDesc'),
  proBriefInput: document.getElementById('proBriefInput'),
  proLoadDefaults: document.getElementById('proLoadDefaults'),
  proAddFiles: document.getElementById('proAddFiles'),
  proFilesList: document.getElementById('proFilesList'),
  proAddStage: document.getElementById('proAddStage'),
  proRunAll: document.getElementById('proRunAll'),
  proPipelineList: document.getElementById('proPipelineList'),
  proPreviewPrompt: document.getElementById('proPreviewPrompt'),
  proFilterSearch: document.getElementById('proFilterSearch'),
  proFilterCount: document.getElementById('proFilterCount'),
  proClearFilters: document.getElementById('proClearFilters'),
  proFilterList: document.getElementById('proFilterList'),
  proOutputList: document.getElementById('proOutputList'),

  settingsModal: document.getElementById('settingsModal'),
  closeSettings: document.getElementById('closeSettings'),
  engineSelect: document.getElementById('engineSelect'),
  codexSettings: document.getElementById('codexSettings'),
  apiSettings: document.getElementById('apiSettings'),
  codexPathInput: document.getElementById('codexPathInput'),
  codexModelInput: document.getElementById('codexModelInput'),
  codexReasoningEffort: document.getElementById('codexReasoningEffort'),
  apiKeyInput: document.getElementById('apiKeyInput'),
  defaultModel: document.getElementById('defaultModel'),
  temperatureInput: document.getElementById('temperatureInput'),
  maxTokensInput: document.getElementById('maxTokensInput'),
  syncPrompts: document.getElementById('syncPrompts'),
  saveSettings: document.getElementById('saveSettings'),

  guideModal: document.getElementById('guideModal'),
  guideTitle: document.getElementById('guideTitle'),
  guideIntro: document.getElementById('guideIntro'),
  guideStepList: document.getElementById('guideStepList'),
  guideStepDetail: document.getElementById('guideStepDetail'),
  closeGuide: document.getElementById('closeGuide'),
  guidePrev: document.getElementById('guidePrev'),
  guideNext: document.getElementById('guideNext')
};

let settingsPatchTimer = null;
let queuedSettingsPatch = {};
let savePromptsTimer = null;

function setStatus(message) {
  if (el.status) {
    el.status.textContent = message || '就绪';
  }
}

function setProgress(current, total) {
  state.progress.current = current || 0;
  state.progress.total = total || 0;
  if (el.progressLabel) {
    el.progressLabel.textContent = `${state.progress.current}/${state.progress.total}`;
  }
  if (el.progressFill) {
    const ratio = state.progress.total > 0 ? state.progress.current / state.progress.total : 0;
    const width = Math.max(0, Math.min(100, Math.round(ratio * 100)));
    el.progressFill.style.width = `${width}%`;
  }
}

function showModal(node, visible) {
  if (!node) return;
  node.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function isObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value);
}

function normalizeReasoningEffort(value) {
  const effort = String(value || '').trim().toLowerCase();
  if (!ALLOWED_REASONING_EFFORTS.has(effort)) {
    return DEFAULT_REASONING_EFFORT;
  }
  return effort;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function getTodayStrings() {
  const now = new Date();
  const yyyy = String(now.getFullYear());
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  return {
    today: `${yyyy}-${mm}-${dd}`,
    todayCompact: `${yyyy}${mm}${dd}`
  };
}

function renderTemplate(template, vars) {
  if (!template || typeof template !== 'string') return '';
  return template.replace(/\{\{([^}]+)\}\}/g, (match, key) => {
    const name = String(key || '').trim();
    return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name] ?? '') : '';
  });
}

function normalizeSettings(raw) {
  const source = isObject(raw) ? raw : {};
  const contextSelectionBySkill = isObject(source.contextSelectionBySkill) ? { ...source.contextSelectionBySkill } : {};
  if (Array.isArray(source.contextSelection) && !contextSelectionBySkill.xhs) {
    contextSelectionBySkill.xhs = source.contextSelection;
  }

  const incomingModel = typeof source.defaultModel === 'string' ? source.defaultModel.trim() : '';
  const defaultModel = !incomingModel || incomingModel === 'gpt-4.1-mini' ? DEFAULT_CODEX_MODEL : incomingModel;

  return {
    apiKey: source.apiKey || '',
    defaultModel,
    modelReasoningEffort: normalizeReasoningEffort(source.modelReasoningEffort),
    temperature: Number.isFinite(source.temperature) ? source.temperature : 0.4,
    maxOutputTokens: Number.isFinite(source.maxOutputTokens) ? source.maxOutputTokens : 1200,
    engine: source.engine || 'codex',
    codexPath: source.codexPath || 'codex',
    activeSkillId: source.activeSkillId || 'xhs',
    contextSelectionBySkill,
    lastModeBySkill: isObject(source.lastModeBySkill) ? { ...source.lastModeBySkill } : {},
    wizardDraftBySkill: isObject(source.wizardDraftBySkill) ? { ...source.wizardDraftBySkill } : {},
    uiVersion: Number.isFinite(source.uiVersion) ? source.uiVersion : 2,
    onboardingSeenVersion: Number.isFinite(source.onboardingSeenVersion) ? source.onboardingSeenVersion : 0
  };
}

function applySettingsState(raw, syncForms = true) {
  state.settings = normalizeSettings(raw);
  state.activeSkillId = state.settings.activeSkillId;
  if (syncForms) {
    syncSettingsForm();
    syncWizardParamFields();
  }
}

function getEngine() {
  return state.settings.engine || 'codex';
}

function updateEngineVisibility() {
  const isCodex = getEngine() === 'codex';
  if (el.codexSettings) {
    el.codexSettings.style.display = isCodex ? 'grid' : 'none';
  }
  if (el.apiSettings) {
    el.apiSettings.style.display = isCodex ? 'none' : 'grid';
  }
}

function syncSettingsForm() {
  if (!el.engineSelect) return;
  el.engineSelect.value = state.settings.engine;
  el.codexPathInput.value = state.settings.codexPath || '';
  if (el.codexModelInput) {
    el.codexModelInput.value = state.settings.defaultModel || DEFAULT_CODEX_MODEL;
  }
  if (el.codexReasoningEffort) {
    el.codexReasoningEffort.value = normalizeReasoningEffort(state.settings.modelReasoningEffort);
  }
  el.apiKeyInput.value = state.settings.apiKey || '';
  el.defaultModel.value = state.settings.defaultModel || DEFAULT_CODEX_MODEL;
  el.temperatureInput.value = state.settings.temperature;
  el.maxTokensInput.value = state.settings.maxOutputTokens;
  updateEngineVisibility();
}

function syncWizardParamFields() {
  if (el.wizardEngine) {
    el.wizardEngine.value = state.settings.engine;
  }
  if (el.wizardModel) {
    el.wizardModel.value = state.settings.defaultModel;
  }
  if (el.wizardReasoningEffort) {
    el.wizardReasoningEffort.value = normalizeReasoningEffort(state.settings.modelReasoningEffort);
  }
  if (el.wizardTemperature) {
    el.wizardTemperature.value = state.settings.temperature;
  }
  if (el.wizardMaxTokens) {
    el.wizardMaxTokens.value = state.settings.maxOutputTokens;
  }
}

function queueSettingsPatch(patch) {
  queuedSettingsPatch = { ...queuedSettingsPatch, ...patch };
  clearTimeout(settingsPatchTimer);
  settingsPatchTimer = setTimeout(async () => {
    const payload = { ...queuedSettingsPatch };
    queuedSettingsPatch = {};
    try {
      const next = await window.api.saveSettings(payload);
      applySettingsState(next, false);
    } catch (err) {
      setStatus(`保存设置失败：${err.message}`);
    }
  }, 280);
}

async function saveSettingsNow(patch) {
  const next = await window.api.saveSettings(patch);
  applySettingsState(next, true);
  return next;
}

function getSkill(skillId) {
  return state.skills.find((skill) => skill.id === skillId) || null;
}

function getWizardSkillSchema(skillId) {
  if (!state.wizardSchema || !isObject(state.wizardSchema.skills)) {
    return null;
  }
  return state.wizardSchema.skills[skillId] || null;
}

function getPipelineForSkill(skillId) {
  const skill = getSkill(skillId);
  if (!skill) return null;
  return state.pipelines.find((pipe) => pipe.id === skill.pipelineId) || null;
}

function ensurePipelineForSkill(skillId) {
  const skill = getSkill(skillId);
  if (!skill) return null;

  let pipeline = getPipelineForSkill(skillId);
  if (pipeline) return pipeline;

  pipeline = {
    id: skill.pipelineId,
    name: skill.name,
    stages: []
  };
  state.pipelines.push(pipeline);
  return pipeline;
}

function queueSavePrompts() {
  clearTimeout(savePromptsTimer);
  savePromptsTimer = setTimeout(async () => {
    try {
      await window.api.savePrompts({
        pipelines: state.pipelines,
        wizardSummaryTemplates: state.wizardSummaryTemplates
      });
    } catch (err) {
      setStatus(`保存提示词失败：${err.message}`);
    }
  }, 360);
}

function getFilesForSkill(skillId) {
  if (!Array.isArray(state.filesBySkill[skillId])) {
    state.filesBySkill[skillId] = [];
  }
  return state.filesBySkill[skillId];
}

function getSelectedCatalogPaths(skillId) {
  return getFilesForSkill(skillId)
    .filter((file) => file.source === 'catalog' && file.include)
    .map((file) => file.path);
}

function getContextText(skillId) {
  return getFilesForSkill(skillId)
    .filter((file) => file.include)
    .map((file) => `File: ${file.path}\n${file.content}`)
    .join('\n\n---\n\n');
}

function getFileByPath(skillId, filePath) {
  return getFilesForSkill(skillId).find((file) => file.path === filePath);
}

async function persistContextSelection(skillId) {
  const nextSelection = getSelectedCatalogPaths(skillId);
  const merged = {
    ...state.settings.contextSelectionBySkill,
    [skillId]: nextSelection
  };
  state.settings.contextSelectionBySkill = merged;
  queueSettingsPatch({ contextSelectionBySkill: merged, activeSkillId: skillId });
}

async function ensureFilesLoaded(skillId) {
  const files = getFilesForSkill(skillId);
  if (files.length) return;

  if (skillId === 'crawler') {
    state.filesBySkill[skillId] = [];
    return;
  }

  let loaded = [];
  const selectedPaths = state.settings.contextSelectionBySkill[skillId];

  if (Array.isArray(selectedPaths) && selectedPaths.length) {
    loaded = await window.api.loadContextFiles(selectedPaths);
  } else {
    loaded = await window.api.loadDefaultContextForSkill(skillId);
    if (!loaded.length) {
      loaded = await window.api.loadDefaultContext();
    }
  }

  state.filesBySkill[skillId] = loaded.map((file) => ({
    ...file,
    include: true,
    source: 'catalog',
    skillId
  }));
}

async function loadDefaultContextForSkill(skillId) {
  const loaded = await window.api.loadDefaultContextForSkill(skillId);
  const nextFiles = getFilesForSkill(skillId);

  nextFiles.forEach((file) => {
    if (file.source === 'catalog') {
      file.include = false;
    }
  });

  loaded.forEach((file) => {
    const existing = getFileByPath(skillId, file.path);
    if (existing) {
      existing.include = true;
      existing.content = file.content;
      return;
    }

    nextFiles.push({
      ...file,
      include: true,
      source: 'catalog',
      skillId
    });
  });

  await persistContextSelection(skillId);
}

async function addManualFiles(skillId) {
  const selected = await window.api.openFiles();
  if (!Array.isArray(selected) || !selected.length) return;

  const files = getFilesForSkill(skillId);
  selected.forEach((file) => {
    const existing = getFileByPath(skillId, file.path);
    if (existing) {
      existing.include = true;
      existing.content = file.content;
      return;
    }

    files.push({
      ...file,
      include: true,
      source: 'manual',
      skillId
    });
  });
}

function splitDraftList(value, options = {}) {
  const allowPunctuation = options.allowPunctuation !== false;
  const text = String(value || '').replace(/\r\n/g, '\n');
  if (!text.trim()) return [];
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

  if (!allowPunctuation) {
    return lines;
  }

  const list = [];
  lines.forEach((line) => {
    line
      .split(/[，,;；]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach((item) => list.push(item));
  });
  return list;
}

function parseDraftBool(value) {
  const text = String(value || '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'y', 'on', '是'].includes(text);
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value || '').trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function buildCrawlerPayloadFromWizard(skillId, brief) {
  const draft = getWizardDraft(skillId);
  const mode = String(draft['输入模式'] || draft.mode || getWizardMode(skillId) || 'URL抓取').trim() || 'URL抓取';

  const dedupRaw = String(draft.dedup_key || 'url').trim().toLowerCase();
  const dedupKey = dedupRaw === 'title' ? 'title' : 'url';

  return {
    mode,
    brief: String(brief || ''),
    urls: splitDraftList(draft['URL列表'], { allowPunctuation: false }),
    keywords: splitDraftList(draft['关键词列表']),
    platforms: splitDraftList(draft['平台列表'] || 'wechat,xhs,x'),
    since: String(draft['时间窗口起'] || '').trim(),
    until: String(draft['时间窗口止'] || '').trim(),
    max_results: parsePositiveInt(draft.max_results, 30),
    dedup_key: dedupKey,
    dry_run: parseDraftBool(draft.dry_run)
  };
}

function formatCrawlerRunOutput(result) {
  const artifacts = Array.isArray(result && result.artifacts) ? result.artifacts : [];
  const errors = Array.isArray(result && result.errors) ? result.errors : [];
  const partialFailures = Array.isArray(result && result.partialFailures) ? result.partialFailures : [];
  const lines = [
    `状态：${result && result.status ? result.status : 'unknown'}`,
    `运行ID：${result && result.run_id ? result.run_id : '-'}`,
    `模式：${result && result.modeText ? result.modeText : (result && result.mode ? result.mode : '-')}`
  ];

  if (artifacts.length) {
    lines.push('', '产物：');
    artifacts.forEach((item) => {
      lines.push(`- ${item}`);
    });
  }

  if (errors.length) {
    lines.push('', '错误：');
    errors.forEach((item) => {
      lines.push(`- ${item}`);
    });
  }

  if (partialFailures.length) {
    lines.push('', '部分失败：');
    partialFailures.forEach((item) => {
      const component = item && item.component ? item.component : 'unknown';
      const errText = Array.isArray(item && item.errors) ? item.errors.join(' | ') : '';
      lines.push(`- ${component}${errText ? `: ${errText}` : ''}`);
    });
  }

  return lines.join('\n');
}

async function refreshContextCatalogState() {
  const catalog = await window.api.getContextCatalog();
  state.contextCatalog = catalog && catalog.groups ? catalog : { groups: [] };
}

async function reloadIncludedCatalogFiles() {
  const skillIds = Object.keys(state.filesBySkill || {});
  for (const skillId of skillIds) {
    const files = getFilesForSkill(skillId);
    const includeCatalog = files.filter((file) => file.source === 'catalog' && file.include);
    if (!includeCatalog.length) continue;

    const loaded = await window.api.loadContextFiles(includeCatalog.map((file) => file.path));
    const byPath = new Map((loaded || []).map((file) => [file.path, file]));
    includeCatalog.forEach((file) => {
      const latest = byPath.get(file.path);
      if (!latest) return;
      file.content = latest.content || '';
      file.error = latest.error || null;
    });
  }
}

function getCatalogGroupsForSkill(skillId, query = '') {
  const groups = Array.isArray(state.contextCatalog.groups) ? state.contextCatalog.groups : [];
  const q = query.trim().toLowerCase();

  const filtered = groups
    .filter((group) => !Array.isArray(group.skills) || !group.skills.length || group.skills.includes(skillId))
    .map((group) => {
      const items = (Array.isArray(group.items) ? group.items : [])
        .filter((item) => {
          const itemSkills = Array.isArray(item.skills) && item.skills.length ? item.skills : group.skills;
          if (Array.isArray(itemSkills) && itemSkills.length && !itemSkills.includes(skillId)) {
            return false;
          }
          if (!q) return true;
          const source = `${item.label || ''} ${item.path || ''} ${(item.tags || []).join(' ')}`.toLowerCase();
          return source.includes(q);
        });
      return { ...group, items };
    })
    .filter((group) => group.items.length > 0)
    .sort((a, b) => {
      const aRecommended = a.items.some((item) => item.recommendedForWizard);
      const bRecommended = b.items.some((item) => item.recommendedForWizard);
      if (aRecommended !== bRecommended) {
        return aRecommended ? -1 : 1;
      }
      return String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN');
    });

  return filtered;
}

function updateNavButtons() {
  const view = state.ui.view;
  if (el.navHome) {
    el.navHome.classList.toggle('active', view === 'home' || view === 'wizard');
  }
  if (el.navExecution) {
    el.navExecution.classList.toggle('active', view === 'execution');
  }
  if (el.navPro) {
    el.navPro.classList.toggle('active', view === 'pro');
  }
}

function switchView(view) {
  state.ui.view = view;
  const views = [el.viewHome, el.viewWizard, el.viewExecution, el.viewPro];
  views.forEach((node) => {
    if (!node) return;
    const isActive = node.dataset.view === view;
    node.classList.toggle('active', isActive);
  });
  updateNavButtons();
}

function getWizardDraft(skillId) {
  if (!isObject(state.settings.wizardDraftBySkill[skillId])) {
    state.settings.wizardDraftBySkill[skillId] = {};
  }
  return state.settings.wizardDraftBySkill[skillId];
}

function ensureWizardDraftDefaults(skillId) {
  const schema = getWizardSkillSchema(skillId);
  if (!schema) return;

  const draft = getWizardDraft(skillId);
  const mode = state.settings.lastModeBySkill[skillId] || schema.defaultMode || (schema.modeOptions && schema.modeOptions[0] && schema.modeOptions[0].value) || '';

  if (!draft.mode) {
    draft.mode = mode;
  }

  (schema.step2Groups || []).forEach((group) => {
    (group.fields || []).forEach((field) => {
      if (draft[field.id] === undefined || draft[field.id] === null) {
        draft[field.id] = field.default !== undefined ? field.default : '';
      }
    });
  });

  state.settings.lastModeBySkill = {
    ...state.settings.lastModeBySkill,
    [skillId]: draft.mode
  };
}

function queueSaveWizardDraft(skillId) {
  queueSettingsPatch({
    wizardDraftBySkill: state.settings.wizardDraftBySkill,
    lastModeBySkill: state.settings.lastModeBySkill,
    activeSkillId: skillId
  });
}

function setWizardField(skillId, key, value) {
  const draft = getWizardDraft(skillId);
  draft[key] = value;
  queueSaveWizardDraft(skillId);
}

function getWizardSkillId() {
  return state.ui.wizard.skillId || state.activeSkillId || 'xhs';
}

function getWizardMode(skillId) {
  const draft = getWizardDraft(skillId);
  return draft.mode || state.settings.lastModeBySkill[skillId] || '';
}

function buildBriefFromDraft(skillId) {
  const schema = getWizardSkillSchema(skillId);
  const draft = getWizardDraft(skillId);
  if (!schema) return '';

  const lines = [];
  const mode = draft.mode || schema.defaultMode || '';
  if (mode) {
    lines.push(`模式：${mode}`);
  }

  const ordered = Array.isArray(schema.briefOrder) ? schema.briefOrder : [];
  ordered.forEach((key) => {
    if (key === '模式') return;
    const value = draft[key];
    if (value === undefined || value === null) return;
    const text = String(value).trim();
    if (!text) return;
    lines.push(`${key}：${text}`);
  });

  return lines.join('\n');
}

function buildWizardSummary(skillId) {
  const draft = getWizardDraft(skillId);
  const skill = getSkill(skillId);
  const key = skill && skill.wizard ? skill.wizard.deliverySummaryTemplateKey : '';
  const template = key ? state.wizardSummaryTemplates[key] : '';
  const mode = getWizardMode(skillId);

  if (template) {
    return renderTemplate(template, {
      mode,
      ...draft
    });
  }

  return buildBriefFromDraft(skillId);
}

function getStageStatusLabel(status) {
  switch (status) {
    case 'running':
      return '执行中';
    case 'done':
      return '完成';
    case 'partial':
      return '部分成功';
    case 'error':
      return '错误';
    default:
      return '待执行';
  }
}

function getProOutputs(skillId) {
  if (!isObject(state.proOutputsBySkill[skillId])) {
    state.proOutputsBySkill[skillId] = {};
  }
  return state.proOutputsBySkill[skillId];
}

function getProStageStatus(skillId) {
  if (!isObject(state.proStageStatusBySkill[skillId])) {
    state.proStageStatusBySkill[skillId] = {};
  }
  return state.proStageStatusBySkill[skillId];
}

function setProStageStatus(skillId, stageId, status) {
  const map = getProStageStatus(skillId);
  map[stageId] = status;
}

function renderWizardStepNav() {
  const currentStep = state.ui.wizard.step;
  el.wizardStepNav.innerHTML = '';

  STEP_DEFS.forEach((item) => {
    const btn = document.createElement('button');
    btn.className = 'wizard-step-btn';
    if (currentStep === item.step) {
      btn.classList.add('active');
    }
    if (item.step < currentStep) {
      btn.classList.add('done');
    }
    btn.textContent = `${item.step}. ${item.title}`;
    btn.addEventListener('click', () => {
      state.ui.wizard.step = item.step;
      renderWizard();
    });
    el.wizardStepNav.appendChild(btn);
  });
}

function renderWizardExample() {
  const currentStep = state.ui.wizard.step;
  const step = (state.guide.steps || [])[currentStep - 1];

  if (!step) {
    el.wizardExample.textContent = '暂无示例。';
    return;
  }

  el.wizardExample.innerHTML = `
    <h4>${escapeHtml(step.title || '')}</h4>
    <div><strong>说明：</strong>${escapeHtml(step.description || '')}</div>
    <div><strong>示例输入：</strong><pre>${escapeHtml(step.exampleInput || '')}</pre></div>
    <div><strong>示例输出：</strong><pre>${escapeHtml(step.exampleOutput || '')}</pre></div>
    <div><strong>常见错误：</strong></div>
    <div>${(step.commonErrors || []).map((item) => `<div>• ${escapeHtml(item)}</div>`).join('')}</div>
  `;
}

function renderWizardStepVisibility() {
  document.querySelectorAll('.wizard-step').forEach((node) => {
    const isActive = Number(node.dataset.step) === state.ui.wizard.step;
    node.classList.toggle('active', isActive);
  });

  if (el.wizardPrev) {
    el.wizardPrev.disabled = state.ui.wizard.step <= 1;
  }

  if (el.wizardNext) {
    if (state.ui.wizard.step >= 5) {
      el.wizardNext.textContent = '返回首页';
    } else {
      el.wizardNext.textContent = '下一步';
    }
  }
}

function renderWizardStep1(skillId) {
  const schema = getWizardSkillSchema(skillId);
  const currentMode = getWizardMode(skillId);

  el.wizardSkillCards.innerHTML = '';
  state.skills.forEach((skill) => {
    const card = document.createElement('button');
    card.className = 'option-card';
    if (skill.id === skillId) {
      card.classList.add('active');
    }

    const wizardInfo = skill.wizard || {};
    card.innerHTML = `
      <div class="option-card-title">${escapeHtml(wizardInfo.homeTitle || skill.name)}</div>
      <div class="option-card-desc">${escapeHtml(wizardInfo.homeDescription || skill.description || '')}</div>
    `;
    card.addEventListener('click', async () => {
      await startWizard(skill.id);
    });
    el.wizardSkillCards.appendChild(card);
  });

  el.wizardModeCards.innerHTML = '';
  (schema && Array.isArray(schema.modeOptions) ? schema.modeOptions : []).forEach((mode) => {
    const card = document.createElement('button');
    card.className = 'option-card';
    if (mode.value === currentMode) {
      card.classList.add('active');
    }
    card.innerHTML = `
      <div class="option-card-title">${escapeHtml(mode.label || mode.value)}</div>
      <div class="option-card-desc">${escapeHtml(mode.description || '')}</div>
    `;
    card.addEventListener('click', () => {
      setWizardField(skillId, 'mode', mode.value);
      if (skillId === 'crawler') {
        setWizardField(skillId, '输入模式', mode.value);
      }
      state.settings.lastModeBySkill[skillId] = mode.value;
      queueSaveWizardDraft(skillId);
      renderWizardStep1(skillId);
      renderWizardStep4(skillId);
    });
    el.wizardModeCards.appendChild(card);
  });
}

function renderWizardStep2(skillId) {
  const schema = getWizardSkillSchema(skillId);
  const draft = getWizardDraft(skillId);
  el.wizardBriefAccordion.innerHTML = '';

  if (!schema) {
    el.wizardBriefAccordion.innerHTML = '<div class="empty">未找到向导字段定义。</div>';
    return;
  }

  (schema.step2Groups || []).forEach((group, index) => {
    const details = document.createElement('details');
    details.className = 'accordion-item';
    details.open = index === 0;

    const summary = document.createElement('summary');
    summary.textContent = group.title || '未命名类目';

    const body = document.createElement('div');
    body.className = 'accordion-body';

    if (group.description) {
      const desc = document.createElement('div');
      desc.className = 'accordion-desc';
      desc.textContent = group.description;
      body.appendChild(desc);
    }

    const fieldGrid = document.createElement('div');
    fieldGrid.className = 'field-grid';

    (group.fields || []).forEach((field) => {
      const wrapper = document.createElement('label');
      wrapper.className = 'field-wrap';
      wrapper.innerHTML = `<span>${escapeHtml(field.label)}${field.required ? ' *' : ''}</span>`;

      let inputNode;
      const value = draft[field.id] !== undefined && draft[field.id] !== null ? draft[field.id] : (field.default !== undefined ? field.default : '');

      if (field.type === 'textarea') {
        inputNode = document.createElement('textarea');
        inputNode.value = value;
      } else if (field.type === 'select') {
        inputNode = document.createElement('select');
        (field.options || []).forEach((optionValue) => {
          const opt = document.createElement('option');
          opt.value = optionValue;
          opt.textContent = optionValue;
          inputNode.appendChild(opt);
        });
        inputNode.value = value;
      } else {
        inputNode = document.createElement('input');
        inputNode.type = 'text';
        inputNode.value = value;
      }

      if (field.placeholder) {
        inputNode.placeholder = field.placeholder;
      }

      inputNode.addEventListener('input', (event) => {
        setWizardField(skillId, field.id, event.target.value);
        if (field.id === 'mode' || (skillId === 'crawler' && field.id === '输入模式')) {
          if (field.id === '输入模式') {
            setWizardField(skillId, 'mode', event.target.value);
          }
          state.settings.lastModeBySkill[skillId] = event.target.value;
          renderWizardStep1(skillId);
        }
        renderWizardStep4(skillId);
      });

      wrapper.appendChild(inputNode);
      fieldGrid.appendChild(wrapper);
    });

    body.appendChild(fieldGrid);
    details.appendChild(summary);
    details.appendChild(body);
    el.wizardBriefAccordion.appendChild(details);
  });
}

async function toggleCatalogItem(skillId, item, checked) {
  const files = getFilesForSkill(skillId);
  const existing = getFileByPath(skillId, item.path);

  if (!checked) {
    if (existing) {
      existing.include = false;
    }
    await persistContextSelection(skillId);
    return;
  }

  if (existing) {
    existing.include = true;
    await persistContextSelection(skillId);
    return;
  }

  const loaded = await window.api.loadContextFiles([item.path]);
  loaded.forEach((file) => {
    files.push({
      ...file,
      include: true,
      source: 'catalog',
      skillId
    });
  });
  await persistContextSelection(skillId);
}

function renderWizardStep3(skillId) {
  if (el.wizardLoadDefaults) {
    el.wizardLoadDefaults.disabled = skillId === 'crawler';
  }
  if (el.wizardAddFiles) {
    el.wizardAddFiles.disabled = skillId === 'crawler';
  }

  if (skillId === 'crawler') {
    el.wizardContextSummary.textContent = '抓取台无需预选素材。运行完成后会自动沉淀到“抓取结果素材”分组。';
    el.wizardContextAccordion.innerHTML = `
      <div class="empty">
        本技能第3步不需要勾选上下文。<br/>
        产物会写入：内容抓取/抓取内容/contexts/latest-*.md，并在写公众号/写小红书中可直接勾选。
      </div>
    `;
    return;
  }

  const groups = getCatalogGroupsForSkill(skillId);
  const files = getFilesForSkill(skillId);
  const selectedCount = files.filter((file) => file.include).length;

  el.wizardContextSummary.textContent = `已选 ${selectedCount} 个素材（默认推荐已标注）`;
  el.wizardContextAccordion.innerHTML = '';

  if (!groups.length) {
    el.wizardContextAccordion.innerHTML = '<div class="empty">当前技能暂无可选素材。</div>';
    return;
  }

  groups.forEach((group, index) => {
    const details = document.createElement('details');
    details.className = 'accordion-item';
    details.open = index === 0;

    const summary = document.createElement('summary');
    summary.textContent = group.title || '未命名分组';

    const body = document.createElement('div');
    body.className = 'accordion-body';

    group.items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'context-item';

      const top = document.createElement('div');
      top.className = 'context-item-top';

      const title = document.createElement('label');
      title.className = 'context-item-title';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.disabled = item.exists === false;

      const selectedFile = getFileByPath(skillId, item.path);
      checkbox.checked = Boolean(selectedFile && selectedFile.include);
      checkbox.addEventListener('change', async () => {
        await toggleCatalogItem(skillId, item, checkbox.checked);
        renderWizardStep3(skillId);
        renderProFilters();
        renderProFiles();
      });

      const nameNode = document.createElement('span');
      nameNode.textContent = item.label || '未命名素材';
      title.appendChild(checkbox);
      title.appendChild(nameNode);

      top.appendChild(title);
      if (item.recommendedForWizard) {
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = '推荐';
        top.appendChild(badge);
      }

      const pathNode = document.createElement('div');
      pathNode.className = 'context-item-path';
      pathNode.textContent = item.path;

      row.appendChild(top);
      row.appendChild(pathNode);
      body.appendChild(row);
    });

    details.appendChild(summary);
    details.appendChild(body);
    el.wizardContextAccordion.appendChild(details);
  });

  const manualFiles = files.filter((file) => file.source === 'manual');
  if (manualFiles.length) {
    const details = document.createElement('details');
    details.className = 'accordion-item';
    details.open = false;

    const summary = document.createElement('summary');
    summary.textContent = `本地文件（${manualFiles.length}）`;

    const body = document.createElement('div');
    body.className = 'accordion-body';

    manualFiles.forEach((file, index) => {
      const row = document.createElement('div');
      row.className = 'context-item';
      row.innerHTML = `
        <div class="context-item-top">
          <div class="context-item-title">${escapeHtml(file.path.split('\\').pop())}</div>
          <div>
            <button class="ghost" data-action="toggle">${file.include ? '已启用' : '已停用'}</button>
            <button class="ghost" data-action="remove">移除</button>
          </div>
        </div>
        <div class="context-item-path">${escapeHtml(file.path)}</div>
      `;

      const toggleBtn = row.querySelector('[data-action="toggle"]');
      const removeBtn = row.querySelector('[data-action="remove"]');

      toggleBtn.addEventListener('click', () => {
        file.include = !file.include;
        renderWizardStep3(skillId);
        renderProFiles();
      });

      removeBtn.addEventListener('click', () => {
        const list = getFilesForSkill(skillId).filter((item) => item.path !== file.path);
        state.filesBySkill[skillId] = list;
        renderWizardStep3(skillId);
        renderProFiles();
      });

      body.appendChild(row);
    });

    details.appendChild(summary);
    details.appendChild(body);
    el.wizardContextAccordion.appendChild(details);
  }
}

function renderWizardStep4(skillId) {
  el.wizardBriefPreview.textContent = buildWizardSummary(skillId);
  syncWizardParamFields();
}

function renderWizardLive() {
  const liveStages = state.ui.wizard.liveStages || {};
  const entries = Object.values(liveStages);

  if (!entries.length) {
    el.wizardLive.innerHTML = '<div class="empty">点击“开始执行”后，这里会显示阶段流式输出。</div>';
    return;
  }

  el.wizardLive.innerHTML = '';
  entries.forEach((item) => {
    const block = document.createElement('div');
    block.className = 'live-stage';
    block.innerHTML = `
      <div class="live-stage-title">${escapeHtml(item.stageName)}</div>
      <div class="live-stage-status">状态：${escapeHtml(getStageStatusLabel(item.status))}</div>
      <div class="live-stage-output">${escapeHtml(item.output || '')}</div>
    `;
    el.wizardLive.appendChild(block);
  });
}

function renderWizard() {
  const skillId = getWizardSkillId();
  const skill = getSkill(skillId);
  const currentStep = state.ui.wizard.step;

  if (!skill) {
    setStatus('未找到技能配置');
    return;
  }

  ensureWizardDraftDefaults(skillId);

  el.wizardSkillTitle.textContent = (skill.wizard && skill.wizard.homeTitle) || skill.name;
  el.wizardBreadcrumb.textContent = `首页 / 向导 / ${skill.name} / 第${currentStep}步`;

  renderWizardStepNav();
  renderWizardExample();
  renderWizardStepVisibility();

  renderWizardStep1(skillId);
  renderWizardStep2(skillId);
  renderWizardStep3(skillId);
  renderWizardStep4(skillId);
  renderWizardLive();
  if (el.wizardOpenRunDetail) {
    el.wizardOpenRunDetail.disabled = !state.ui.wizard.lastRunId;
  }
}

function validateWizardStep(skillId, step) {
  if (step === 1) {
    const mode = getWizardMode(skillId);
    if (!mode) {
      return '请先选择模式。';
    }
  }

  if (step === 2) {
    const schema = getWizardSkillSchema(skillId);
    const draft = getWizardDraft(skillId);
    const missing = [];
    const pushMissing = (name) => {
      if (!missing.includes(name)) {
        missing.push(name);
      }
    };

    (schema && schema.step2Groups ? schema.step2Groups : []).forEach((group) => {
      (group.fields || []).forEach((field) => {
        if (!field.required) return;
        const value = draft[field.id];
        if (!String(value || '').trim()) {
          pushMissing(field.label || field.id);
        }
      });
    });

    if (skillId === 'crawler') {
      const inputMode = String(draft['输入模式'] || draft.mode || '').trim();
      const urls = splitDraftList(draft['URL列表'], { allowPunctuation: false });
      const keywords = splitDraftList(draft['关键词列表']);
      const requireUrl = inputMode.includes('URL') || inputMode.includes('双');
      const requireKeyword = inputMode.includes('关键词') || inputMode.includes('双');

      if (requireUrl && !urls.length) {
        pushMissing('URL列表');
      }
      if (requireKeyword && !keywords.length) {
        pushMissing('关键词列表');
      }
    }

    if (missing.length) {
      return `请补全必填项：${missing.join('、')}`;
    }
  }

  return '';
}

async function startWizard(skillId) {
  state.ui.wizard.skillId = skillId;
  state.ui.wizard.step = 1;
  state.ui.wizard.liveStages = {};
  state.settings.activeSkillId = skillId;
  state.activeSkillId = skillId;

  ensureWizardDraftDefaults(skillId);
  await ensureFilesLoaded(skillId);
  queueSettingsPatch({ activeSkillId: skillId });
  switchView('wizard');
  renderWizard();
  renderProSkillButtons();
  renderPro();
}

function getStageInput(stage, brief, contextText, prevText) {
  const { today, todayCompact } = getTodayStrings();
  return renderTemplate(stage.template || '', {
    input: brief,
    context: contextText,
    prev: prevText || '',
    today,
    todayCompact
  });
}

function createRunId() {
  return `run_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
}

function briefToSummary(brief) {
  const compact = String(brief || '').replace(/\s+/g, ' ').trim();
  return compact.slice(0, 180);
}

function parseCodexPrompt(instructions, input) {
  const parts = [];
  if (String(instructions || '').trim()) {
    parts.push(`【指令】\n${instructions}`);
  }
  if (String(input || '').trim()) {
    parts.push(`【输入】\n${input}`);
  }
  return parts.join('\n\n').trim();
}

function handleStreamEvent(event) {
  const handler = state.streamHandlers[event.id];
  if (!handler) return;

  if (event.type === 'delta') {
    handler.onDelta(event.delta || '');
    return;
  }

  if (event.type === 'done') {
    delete state.streamHandlers[event.id];
    handler.onDone(event.text || '');
    return;
  }

  if (event.type === 'error') {
    delete state.streamHandlers[event.id];
    handler.onError(event.message || '执行失败');
  }
}

async function executeStageStream({ skillId, stage, brief, contextText, prevText, onDelta }) {
  if (stage && stage.executor === 'crawler') {
    if (onDelta) {
      onDelta('正在检查抓取运行环境...');
    }

    const payload = buildCrawlerPayloadFromWizard(skillId, brief);
    const runtime = await window.api.getCrawlerRuntime();
    const modeText = String(payload.mode || '');
    const needKeyword = modeText.includes('关键词') || modeText.includes('双');

    if (runtime && runtime.bridgeExists === false) {
      throw new Error(`抓取桥接脚本不存在：${runtime.bridgeScript || 'scripts/crawl_bridge.py'}`);
    }
    if (runtime && runtime.pythonAvailable === false) {
      const hints = Array.isArray(runtime.hints) ? runtime.hints.filter(Boolean) : [];
      throw new Error(hints[0] || '未检测到可用 Python，请先安装并配置 Python 3.10+。');
    }
    if (runtime && needKeyword && runtime.keywordScriptSource === 'missing') {
      const hints = Array.isArray(runtime.hints) ? runtime.hints.filter(Boolean) : [];
      throw new Error(
        hints.find((item) => String(item).includes('关键词抓取脚本')) ||
        '关键词抓取脚本缺失：请补齐内置脚本或系统技能回退脚本。'
      );
    }
    if (onDelta) {
      onDelta('抓取执行中，请稍候...');
    }

    const result = await window.api.runCrawler(payload);
    const output = formatCrawlerRunOutput(result);
    if (onDelta) {
      onDelta(output);
    }

    return {
      text: output,
      meta: {
        executor: 'crawler',
        result: {
          run_id: result.run_id || '',
          status: result.status || 'done',
          mode: result.mode || '',
          modeText: result.modeText || '',
          artifacts: Array.isArray(result.artifacts) ? result.artifacts : [],
          contexts_latest: result.contexts_latest || {},
          errors: Array.isArray(result.errors) ? result.errors : [],
          partialFailures: Array.isArray(result.partialFailures) ? result.partialFailures : [],
          run_file: result.run_file || ''
        }
      }
    };
  }

  const engine = getEngine();
  const stageInput = getStageInput(stage, brief, contextText, prevText);

  let stream;
  if (engine === 'codex') {
    const prompt = parseCodexPrompt(stage.instructions || '', stageInput);
    stream = await window.api.runCodexStream({
      prompt,
      codexPath: state.settings.codexPath,
      model: stage.model || state.settings.defaultModel || DEFAULT_CODEX_MODEL,
      modelReasoningEffort: normalizeReasoningEffort(state.settings.modelReasoningEffort)
    });
  } else {
    if (!state.settings.apiKey) {
      throw new Error('当前为 OpenAI 模式，请先在设置中填写 API Key。');
    }

    const stageMax = Number.parseInt(stage.maxOutputTokens, 10);
    const maxOutputTokens = Number.isFinite(stageMax) && stageMax > 0 ? stageMax : state.settings.maxOutputTokens;

    stream = await window.api.runOpenAIStream({
      apiKey: state.settings.apiKey,
      model: stage.model || state.settings.defaultModel,
      instructions: stage.instructions || '',
      input: stageInput,
      temperature: Number.isFinite(stage.temperature) ? stage.temperature : state.settings.temperature,
      maxOutputTokens
    });
  }

  const streamId = stream && stream.id;
  if (!streamId) {
    throw new Error('执行失败：未收到流式任务标识。');
  }

  return await new Promise((resolve, reject) => {
    let collected = '';

    state.streamHandlers[streamId] = {
      onDelta: (delta) => {
        collected += delta;
        if (onDelta) {
          onDelta(collected);
        }
      },
      onDone: (text) => {
        if (String(text || '').trim()) {
          collected = text;
        }
        if (onDelta) {
          onDelta(collected);
        }
        resolve(collected);
      },
      onError: (message) => {
        reject(new Error(message));
      }
    };
  });
}

function extractBetween(text, startMarker, endMarker) {
  if (typeof text !== 'string') return null;
  const startIndex = text.indexOf(startMarker);
  if (startIndex < 0) return null;
  const endIndex = text.indexOf(endMarker, startIndex + startMarker.length);
  if (endIndex < 0) return null;
  return text.slice(startIndex + startMarker.length, endIndex);
}

function tryParseFilesJson(text) {
  const raw = extractBetween(text, '<!--FILES_JSON_START-->', '<!--FILES_JSON_END-->');
  if (!raw) return null;
  try {
    return JSON.parse(raw.trim());
  } catch (err) {
    return null;
  }
}

function extractFileBlocks(text) {
  const list = [];
  if (typeof text !== 'string') return list;

  let cursor = 0;
  const startMarker = '<!--FILE_START-->';
  const endMarker = '<!--FILE_END-->';

  while (true) {
    const startIndex = text.indexOf(startMarker, cursor);
    if (startIndex < 0) break;
    const endIndex = text.indexOf(endMarker, startIndex + startMarker.length);
    if (endIndex < 0) break;

    const chunk = text.slice(startIndex + startMarker.length, endIndex).replace(/^\s*\r?\n/, '');
    const match = chunk.match(/^([^\r\n]+)\r?\n([\s\S]*)$/);
    if (match) {
      list.push({
        path: match[1].trim(),
        content: match[2].trimEnd()
      });
    }

    cursor = endIndex + endMarker.length;
  }

  return list;
}

async function maybeSaveOutputsFromText(text) {
  const ordered = tryParseFilesJson(text);
  const blocks = extractFileBlocks(text);

  if (!ordered && !blocks.length) {
    return null;
  }

  const files = [];
  const blockMap = new Map();
  blocks.forEach((item) => {
    blockMap.set(item.path, item.content);
  });

  const orderedFiles = ordered && Array.isArray(ordered.files) ? ordered.files : [];
  if (orderedFiles.length) {
    orderedFiles.forEach((item) => {
      if (!item || typeof item.path !== 'string') return;
      const path = item.path.trim();
      if (!path) return;

      const blockContent = blockMap.get(path);
      const content = typeof blockContent === 'string'
        ? blockContent
        : (typeof item.content === 'string' ? item.content : '');

      if (!content) return;
      files.push({ path, content });
    });
  } else {
    blocks.forEach((item) => {
      files.push({ path: item.path, content: item.content });
    });
  }

  if (!files.length) return null;
  return await window.api.saveOutputs({ files });
}

async function saveRunRecord(run) {
  await window.api.saveRun(run);
}

async function executePipelineRun({
  skillId,
  brief,
  mode,
  source,
  stageIndices,
  existingOutputs,
  onStageStatus,
  onStageOutput
}) {
  const skill = getSkill(skillId);
  const pipeline = ensurePipelineForSkill(skillId);
  if (!pipeline || !Array.isArray(pipeline.stages) || !pipeline.stages.length) {
    throw new Error('当前技能没有可执行的步骤。');
  }

  const indices = Array.isArray(stageIndices) && stageIndices.length
    ? stageIndices
    : pipeline.stages.map((_, index) => index);

  const runId = createRunId();
  const hasExecutorStage = pipeline.stages.some((item) => item && item.executor);
  const runType = hasExecutorStage ? 'crawler' : 'llm';
  const run = {
    id: runId,
    skillId,
    skillName: skill ? skill.name : skillId,
    runType,
    mode: mode || '',
    source: source || 'wizard',
    status: 'running',
    startedAt: new Date().toISOString(),
    endedAt: null,
    brief,
    briefSummary: briefToSummary(brief),
    stages: [],
    files: [],
    artifacts: [],
    partialFailures: []
  };

  await saveRunRecord(run);

  const contextText = getContextText(skillId);
  const stageOutputs = { ...(isObject(existingOutputs) ? existingOutputs : {}) };

  setProgress(0, indices.length);

  for (let i = 0; i < indices.length; i += 1) {
    const stageIndex = indices[i];
    const stage = pipeline.stages[stageIndex];
    if (!stage) continue;

    const previousStage = stageIndex > 0 ? pipeline.stages[stageIndex - 1] : null;
    const prevText = previousStage ? (stageOutputs[previousStage.id] || '') : '';

    if (onStageStatus) {
      onStageStatus(stage, 'running');
    }

    let stageText = '';
    let stageMeta = null;

    try {
      const stageResult = await executeStageStream({
        skillId,
        stage,
        brief,
        contextText,
        prevText,
        onDelta: (fullText) => {
          stageOutputs[stage.id] = fullText;
          if (onStageOutput) {
            onStageOutput(stage, fullText);
          }
        }
      });

      if (typeof stageResult === 'string') {
        stageText = stageResult;
      } else if (stageResult && typeof stageResult === 'object') {
        stageText = stageResult.text || '';
        stageMeta = stageResult.meta || null;
      }

      stageOutputs[stage.id] = stageText;

      let stageStatus = 'done';
      if (
        stageMeta &&
        stageMeta.executor === 'crawler' &&
        stageMeta.result &&
        (stageMeta.result.status === 'partial' || stageMeta.result.status === 'error')
      ) {
        stageStatus = stageMeta.result.status;
      }

      const stageRecord = {
        stageId: stage.id,
        stageName: stage.name,
        status: stageStatus,
        output: stageText
      };
      if (stageMeta) {
        stageRecord.meta = stageMeta;
      }

      run.stages.push(stageRecord);

      if (onStageStatus) {
        onStageStatus(stage, stageStatus);
      }

      await saveRunRecord(run);
      setProgress(i + 1, indices.length);
    } catch (err) {
      const message = err && err.message ? err.message : '执行失败';

      run.stages.push({
        stageId: stage.id,
        stageName: stage.name,
        status: 'error',
        output: stageText,
        error: message
      });

      run.status = 'error';
      run.error = message;
      run.endedAt = new Date().toISOString();
      await saveRunRecord(run);

      if (onStageStatus) {
        onStageStatus(stage, 'error');
      }

      setProgress(i + 1, indices.length);
      throw new Error(message);
    }
  }

  run.endedAt = new Date().toISOString();

  if (runType === 'crawler') {
    const crawlerStage = run.stages.find((item) => item && item.meta && item.meta.executor === 'crawler');
    const crawlerResult = crawlerStage && crawlerStage.meta ? crawlerStage.meta.result : null;

    if (crawlerResult) {
      run.status = crawlerResult.status || 'done';
      run.crawlerRunId = crawlerResult.run_id || '';
      run.artifacts = Array.isArray(crawlerResult.artifacts) ? crawlerResult.artifacts : [];
      run.partialFailures = Array.isArray(crawlerResult.partialFailures) ? crawlerResult.partialFailures : [];
      run.errors = Array.isArray(crawlerResult.errors) ? crawlerResult.errors : [];

      const contextList = crawlerResult.contexts_latest && typeof crawlerResult.contexts_latest === 'object'
        ? Object.values(crawlerResult.contexts_latest).filter(Boolean)
        : [];
      const extraFiles = crawlerResult.run_file ? [crawlerResult.run_file] : [];
      run.files = Array.from(new Set([...contextList, ...extraFiles, ...run.artifacts]));
    } else {
      run.status = 'done';
    }
  } else {
    run.status = 'done';
    const latestStage = run.stages.length ? run.stages[run.stages.length - 1] : null;
    if (latestStage && latestStage.output && latestStage.output.includes('<!--FILES_JSON_START-->')) {
      const writeResult = await maybeSaveOutputsFromText(latestStage.output).catch((err) => {
        return { ok: false, error: err.message || '落盘失败' };
      });

      run.writeResult = writeResult;
      if (writeResult && writeResult.ok && Array.isArray(writeResult.written)) {
        run.files = writeResult.written;
      }
    }
  }

  await saveRunRecord(run);
  await refreshRuns();

  return {
    run,
    stageOutputs
  };
}

async function refreshRuns() {
  const list = await window.api.listRuns();
  state.runs = Array.isArray(list) ? list : [];
  renderRunsList();
}

function renderRunsList() {
  el.runsList.innerHTML = '';

  if (!state.runs.length) {
    el.runsList.innerHTML = '<div class="empty">还没有运行记录。</div>';
    return;
  }

  state.runs.forEach((run) => {
    const row = document.createElement('button');
    row.className = 'run-item';
    if (run.id === state.ui.selectedRunId) {
      row.classList.add('active');
    }

    row.innerHTML = `
      <div class="run-item-title">${escapeHtml(run.skillName || run.skillId || '未知技能')}</div>
      <div class="run-item-meta">${escapeHtml(run.runType || 'llm')} · ${escapeHtml(run.mode || '默认模式')} · ${escapeHtml(run.status || 'unknown')}</div>
      <div class="run-item-meta">${escapeHtml(run.startedAt || '')}</div>
      <div class="run-item-meta">文件：${escapeHtml(String(run.fileCount || 0))}</div>
    `;

    row.addEventListener('click', () => selectRun(run.id));
    el.runsList.appendChild(row);
  });
}

async function selectRun(runId) {
  if (!runId) return;
  state.ui.selectedRunId = runId;
  renderRunsList();

  let detail = state.runDetails[runId];
  if (!detail) {
    detail = await window.api.getRun(runId);
    if (detail) {
      state.runDetails[runId] = detail;
    }
  }

  renderRunDetail(detail);
}

function renderRunDetail(run) {
  if (!run) {
    el.runDetail.innerHTML = '<div class="empty">请选择一条运行记录查看详情</div>';
    return;
  }

  const files = Array.isArray(run.files) ? run.files : [];
  const artifacts = Array.isArray(run.artifacts) ? run.artifacts : [];
  const artifactsOnly = artifacts.filter((item) => !files.includes(item));
  const stages = Array.isArray(run.stages) ? run.stages : [];
  const partialFailures = Array.isArray(run.partialFailures) ? run.partialFailures : [];
  const errors = Array.isArray(run.errors) ? run.errors : [];

  el.runDetail.innerHTML = `
    <div class="run-block">
      <h3>运行概览</h3>
      <div class="run-grid">
        <div><div class="run-label">技能</div><div class="run-value">${escapeHtml(run.skillName || run.skillId || '')}</div></div>
        <div><div class="run-label">运行类型</div><div class="run-value">${escapeHtml(run.runType || 'llm')}</div></div>
        <div><div class="run-label">模式</div><div class="run-value">${escapeHtml(run.mode || '')}</div></div>
        <div><div class="run-label">状态</div><div class="run-value">${escapeHtml(run.status || '')}</div></div>
        <div><div class="run-label">来源</div><div class="run-value">${escapeHtml(run.source || '')}</div></div>
        <div><div class="run-label">开始时间</div><div class="run-value">${escapeHtml(run.startedAt || '')}</div></div>
        <div><div class="run-label">结束时间</div><div class="run-value">${escapeHtml(run.endedAt || '')}</div></div>
      </div>
    </div>
    <div class="run-block">
      <h3>Brief 摘要</h3>
      <pre>${escapeHtml(run.brief || run.briefSummary || '')}</pre>
    </div>
    <div class="run-block" id="runStagesContainer">
      <h3>阶段输出</h3>
    </div>
    <div class="run-block" id="runArtifactsContainer">
      <h3>产物文件</h3>
    </div>
    <div class="run-block" id="runFilesContainer">
      <h3>入口文件</h3>
    </div>
    <div class="run-block" id="runErrorsContainer">
      <h3>错误与部分成功</h3>
    </div>
  `;

  const stagesContainer = document.getElementById('runStagesContainer');
  if (!stages.length) {
    stagesContainer.innerHTML += '<div class="empty">无阶段输出。</div>';
  } else {
    stages.forEach((stage) => {
      const block = document.createElement('div');
      block.className = 'run-stage';
      block.innerHTML = `
        <div class="run-value">${escapeHtml(stage.stageName || stage.stageId || '')}</div>
        <div class="run-label">状态：${escapeHtml(stage.status || '')}${stage.error ? ` · ${escapeHtml(stage.error)}` : ''}</div>
        <pre>${escapeHtml(stage.output || '')}</pre>
      `;
      stagesContainer.appendChild(block);
    });
  }

  const artifactsContainer = document.getElementById('runArtifactsContainer');
  if (!artifactsOnly.length) {
    artifactsContainer.innerHTML += artifacts.length
      ? '<div class="empty">产物路径已合并在“入口文件”。</div>'
      : '<div class="empty">本次没有产物路径。</div>';
  } else {
    const list = document.createElement('div');
    list.className = 'file-list';

    artifactsOnly.forEach((filePath) => {
      const row = document.createElement('div');
      row.className = 'file-row';

      const pathNode = document.createElement('span');
      pathNode.textContent = filePath;

      const openBtn = document.createElement('button');
      openBtn.className = 'ghost';
      openBtn.textContent = '打开';
      openBtn.addEventListener('click', async () => {
        const result = await window.api.openPath(filePath);
        if (!result || !result.ok) {
          setStatus(`打开失败：${(result && result.error) || '未知错误'}`);
        }
      });

      row.appendChild(pathNode);
      row.appendChild(openBtn);
      list.appendChild(row);
    });

    artifactsContainer.appendChild(list);
  }

  const filesContainer = document.getElementById('runFilesContainer');
  if (!files.length) {
    filesContainer.innerHTML += '<div class="empty">本次没有入口文件。</div>';
  } else {
    const list = document.createElement('div');
    list.className = 'file-list';

    files.forEach((filePath) => {
      const row = document.createElement('div');
      row.className = 'file-row';

      const pathNode = document.createElement('span');
      pathNode.textContent = filePath;

      const openBtn = document.createElement('button');
      openBtn.className = 'ghost';
      openBtn.textContent = '打开';
      openBtn.addEventListener('click', async () => {
        const result = await window.api.openPath(filePath);
        if (!result || !result.ok) {
          setStatus(`打开失败：${(result && result.error) || '未知错误'}`);
        }
      });

      row.appendChild(pathNode);
      row.appendChild(openBtn);
      list.appendChild(row);
    });

    filesContainer.appendChild(list);
  }

  const errorsContainer = document.getElementById('runErrorsContainer');
  if (!errors.length && !partialFailures.length) {
    errorsContainer.innerHTML += '<div class="empty">无错误，或无部分失败记录。</div>';
    return;
  }

  const list = document.createElement('div');
  list.className = 'guide-errors';

  errors.forEach((item) => {
    const row = document.createElement('div');
    row.textContent = `错误：${item}`;
    list.appendChild(row);
  });

  partialFailures.forEach((item) => {
    const row = document.createElement('div');
    const component = item && item.component ? item.component : 'unknown';
    const detail = Array.isArray(item && item.errors) ? item.errors.join(' | ') : '';
    row.textContent = `部分失败[${component}]：${detail || '未知错误'}`;
    list.appendChild(row);
  });

  errorsContainer.appendChild(list);
}

function buildFilterItem(skillId, item, onChanged) {
  const row = document.createElement('div');
  row.className = 'filter-item';

  const label = document.createElement('label');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.disabled = item.exists === false;

  const file = getFileByPath(skillId, item.path);
  checkbox.checked = Boolean(file && file.include);

  checkbox.addEventListener('change', async () => {
    await toggleCatalogItem(skillId, item, checkbox.checked);
    onChanged();
  });

  const title = document.createElement('span');
  title.textContent = item.label || '未命名素材';

  label.appendChild(checkbox);
  label.appendChild(title);

  row.appendChild(label);

  if (item.recommendedForWizard) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = '推荐';
    row.appendChild(badge);
  }

  const pathNode = document.createElement('div');
  pathNode.className = 'file-path';
  pathNode.textContent = item.path;
  row.appendChild(pathNode);

  return row;
}

function renderProSkillButtons() {
  el.proSkillButtons.innerHTML = '';

  state.skills.forEach((skill) => {
    const btn = document.createElement('button');
    btn.className = 'ghost';
    if (skill.id === state.activeSkillId) {
      btn.classList.add('active');
    }
    btn.textContent = skill.name;
    btn.addEventListener('click', async () => {
      state.activeSkillId = skill.id;
      state.settings.activeSkillId = skill.id;
      queueSettingsPatch({ activeSkillId: skill.id });
      await ensureFilesLoaded(skill.id);
      renderPro();
    });
    el.proSkillButtons.appendChild(btn);
  });

  const activeSkill = getSkill(state.activeSkillId);
  el.proSkillDesc.textContent = activeSkill ? activeSkill.description || '' : '';
}

function renderProFiles() {
  const skillId = state.activeSkillId;
  const files = getFilesForSkill(skillId);

  el.proFilesList.innerHTML = '';
  if (!files.length) {
    el.proFilesList.innerHTML = '<div class="empty">暂无上下文文件。</div>';
    return;
  }

  files.forEach((file, index) => {
    const card = document.createElement('div');
    card.className = 'file-card';

    const info = document.createElement('div');
    info.className = 'file-info';
    info.innerHTML = `
      <div class="file-name">${escapeHtml(file.path.split('\\').pop())}</div>
      <div class="file-path">${escapeHtml(file.path)}</div>
    `;

    const actions = document.createElement('div');
    actions.className = 'section-actions';

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'ghost';
    toggleBtn.textContent = file.include ? '已启用' : '已停用';
    toggleBtn.addEventListener('click', async () => {
      file.include = !file.include;
      if (file.source === 'catalog') {
        await persistContextSelection(skillId);
      }
      renderProFiles();
      renderProFilters();
    });

    const removeBtn = document.createElement('button');
    removeBtn.className = 'ghost';
    removeBtn.textContent = '移除';
    removeBtn.addEventListener('click', async () => {
      files.splice(index, 1);
      if (file.source === 'catalog') {
        await persistContextSelection(skillId);
      }
      renderProFiles();
      renderProFilters();
    });

    actions.appendChild(toggleBtn);
    actions.appendChild(removeBtn);

    card.appendChild(info);
    card.appendChild(actions);
    el.proFilesList.appendChild(card);
  });
}

function createStage() {
  return {
    id: `stage_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
    name: '新步骤',
    model: '',
    instructions: '你是严谨的结构化写作助手。',
    template: '【参考资料】\n{{context}}\n\n【用户Brief】\n{{input}}\n\n请输出下一步可直接执行的交付内容。'
  };
}

function renderProPipeline() {
  const skillId = state.activeSkillId;
  const pipeline = ensurePipelineForSkill(skillId);
  const statuses = getProStageStatus(skillId);

  el.proPipelineList.innerHTML = '';

  if (!pipeline || !Array.isArray(pipeline.stages) || !pipeline.stages.length) {
    el.proPipelineList.innerHTML = '<div class="empty">当前没有步骤，先新增一步。</div>';
    return;
  }

  pipeline.stages.forEach((stage, index) => {
    const card = document.createElement('div');
    card.className = 'stage-card';

    const head = document.createElement('div');
    head.className = 'stage-head';

    const titleInput = document.createElement('input');
    titleInput.className = 'stage-title-input';
    titleInput.value = stage.name || '';
    titleInput.addEventListener('input', (event) => {
      stage.name = event.target.value;
      queueSavePrompts();
      renderProOutputs();
    });

    const actions = document.createElement('div');
    actions.className = 'stage-actions';

    const status = document.createElement('span');
    status.className = `stage-status ${statuses[stage.id] || 'idle'}`;
    status.textContent = getStageStatusLabel(statuses[stage.id] || 'idle');

    const runBtn = document.createElement('button');
    runBtn.className = 'primary';
    runBtn.textContent = '执行';
    runBtn.disabled = statuses[stage.id] === 'running';
    runBtn.addEventListener('click', () => handleProRunStage(index));

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'ghost';
    deleteBtn.textContent = '删除';
    deleteBtn.addEventListener('click', () => {
      pipeline.stages.splice(index, 1);
      delete getProOutputs(skillId)[stage.id];
      delete getProStageStatus(skillId)[stage.id];
      queueSavePrompts();
      renderProPipeline();
      renderProOutputs();
    });

    actions.appendChild(status);
    actions.appendChild(runBtn);
    actions.appendChild(deleteBtn);

    head.appendChild(titleInput);
    head.appendChild(actions);

    const meta = document.createElement('div');
    meta.className = 'stage-meta';

    const modelInput = document.createElement('input');
    modelInput.placeholder = `模型（默认 ${state.settings.defaultModel}）`;
    modelInput.value = stage.model || '';
    modelInput.addEventListener('input', (event) => {
      stage.model = event.target.value;
      queueSavePrompts();
    });

    const tempInput = document.createElement('input');
    tempInput.type = 'number';
    tempInput.min = '0';
    tempInput.max = '2';
    tempInput.step = '0.1';
    tempInput.placeholder = `温度（默认 ${state.settings.temperature}）`;
    tempInput.value = Number.isFinite(stage.temperature) ? stage.temperature : '';
    tempInput.addEventListener('input', (event) => {
      const value = parseFloat(event.target.value);
      stage.temperature = Number.isFinite(value) ? value : null;
      queueSavePrompts();
    });

    meta.appendChild(modelInput);
    meta.appendChild(tempInput);

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

    const hint = document.createElement('div');
    hint.className = 'stage-hint';
    hint.textContent = '模板变量：{{input}} {{context}} {{prev}} {{today}} {{todayCompact}}';

    card.appendChild(head);
    card.appendChild(meta);
    card.appendChild(instructions);
    card.appendChild(template);
    card.appendChild(hint);

    el.proPipelineList.appendChild(card);
  });
}

function renderProOutputs() {
  const skillId = state.activeSkillId;
  const pipeline = ensurePipelineForSkill(skillId);
  const outputs = getProOutputs(skillId);

  el.proOutputList.innerHTML = '';

  if (!pipeline || !Array.isArray(pipeline.stages) || !pipeline.stages.length) {
    el.proOutputList.innerHTML = '<div class="empty">输出会显示在这里。</div>';
    return;
  }

  pipeline.stages.forEach((stage) => {
    const card = document.createElement('div');
    card.className = 'output-card';
    card.innerHTML = `
      <h4>${escapeHtml(stage.name || stage.id)}</h4>
      <pre>${escapeHtml(outputs[stage.id] || '暂无输出。')}</pre>
    `;
    el.proOutputList.appendChild(card);
  });
}

function renderProFilters() {
  const skillId = state.activeSkillId;
  const query = el.proFilterSearch ? el.proFilterSearch.value : '';
  const groups = getCatalogGroupsForSkill(skillId, query || '');

  el.proFilterList.innerHTML = '';

  groups.forEach((group) => {
    const block = document.createElement('details');
    block.className = 'accordion-item';
    block.open = true;

    const summary = document.createElement('summary');
    summary.textContent = group.title || '分组';

    const body = document.createElement('div');
    body.className = 'accordion-body';

    group.items.forEach((item) => {
      const row = buildFilterItem(skillId, item, () => {
        renderProFilters();
        renderProFiles();
        renderWizardStep3(skillId);
      });
      body.appendChild(row);
    });

    block.appendChild(summary);
    block.appendChild(body);
    el.proFilterList.appendChild(block);
  });

  const selectedCount = getFilesForSkill(skillId).filter((file) => file.include).length;
  if (el.proFilterCount) {
    el.proFilterCount.textContent = `已选 ${selectedCount}`;
  }
}

async function handleProRunStage(index) {
  const skillId = state.activeSkillId;
  const pipeline = ensurePipelineForSkill(skillId);
  const stage = pipeline && pipeline.stages ? pipeline.stages[index] : null;
  if (!stage) return;

  const outputs = getProOutputs(skillId);
  const statuses = getProStageStatus(skillId);

  try {
    setStatus(`执行中：${stage.name}`);

    const result = await executePipelineRun({
      skillId,
      brief: el.proBriefInput.value,
      mode: state.settings.lastModeBySkill[skillId] || '',
      source: 'pro-single',
      stageIndices: [index],
      existingOutputs: outputs,
      onStageStatus: (stageRef, status) => {
        statuses[stageRef.id] = status;
        renderProPipeline();
      },
      onStageOutput: (stageRef, output) => {
        outputs[stageRef.id] = output;
        renderProOutputs();
      }
    });

    if (result.run.runType === 'crawler' && ['done', 'partial'].includes(result.run.status)) {
      await refreshContextCatalogState();
      await reloadIncludedCatalogFiles();
      renderProFilters();
      renderProFiles();
    }

    setStatus(result.run.status === 'partial' ? '步骤执行完成（部分成功）' : '步骤执行完成');
  } catch (err) {
    setStatus(`执行失败：${err.message}`);
  }
}

async function handleProRunAll() {
  const skillId = state.activeSkillId;
  const pipeline = ensurePipelineForSkill(skillId);
  if (!pipeline || !pipeline.stages || !pipeline.stages.length) return;

  const outputs = getProOutputs(skillId);
  const statuses = getProStageStatus(skillId);

  try {
    setStatus('正在执行全部步骤...');

    const result = await executePipelineRun({
      skillId,
      brief: el.proBriefInput.value,
      mode: state.settings.lastModeBySkill[skillId] || '',
      source: 'pro-all',
      existingOutputs: outputs,
      onStageStatus: (stageRef, status) => {
        statuses[stageRef.id] = status;
        renderProPipeline();
      },
      onStageOutput: (stageRef, output) => {
        outputs[stageRef.id] = output;
        renderProOutputs();
      }
    });

    if (result.run.runType === 'crawler' && ['done', 'partial'].includes(result.run.status)) {
      await refreshContextCatalogState();
      await reloadIncludedCatalogFiles();
      renderProFilters();
      renderProFiles();
    }

    state.ui.selectedRunId = result.run.id;
    setStatus(result.run.status === 'partial' ? '全部步骤执行完成（部分成功）' : '全部步骤执行完成');
  } catch (err) {
    setStatus(`执行失败：${err.message}`);
  }
}

function handleProPreviewPrompt() {
  const skillId = state.activeSkillId;
  const pipeline = ensurePipelineForSkill(skillId);
  if (!pipeline || !pipeline.stages || !pipeline.stages.length) {
    return;
  }

  const stage = pipeline.stages[0];
  const contextText = getContextText(skillId);
  const input = getStageInput(stage, el.proBriefInput.value, contextText, '');

  getProOutputs(skillId)[stage.id] = `--- 指令 ---\n${stage.instructions || ''}\n\n--- 输入 ---\n${input}`;
  renderProOutputs();
  setStatus('已预览首步提示词');
}

function renderPro() {
  renderProSkillButtons();
  renderProFiles();
  renderProPipeline();
  renderProFilters();
  renderProOutputs();
}

function renderGuideList() {
  const steps = Array.isArray(state.guide.steps) ? state.guide.steps : [];
  el.guideStepList.innerHTML = '';

  steps.forEach((step, index) => {
    const btn = document.createElement('button');
    btn.className = 'guide-step-btn';
    if (index === state.ui.guideIndex) {
      btn.classList.add('active');
    }
    btn.textContent = step.title || `步骤 ${index + 1}`;
    btn.addEventListener('click', () => {
      state.ui.guideIndex = index;
      renderGuide();
    });
    el.guideStepList.appendChild(btn);
  });
}

function renderGuideDetail() {
  const steps = Array.isArray(state.guide.steps) ? state.guide.steps : [];
  const step = steps[state.ui.guideIndex];
  if (!step) {
    el.guideStepDetail.innerHTML = '<div class="empty">暂无引导内容。</div>';
    return;
  }

  el.guideStepDetail.innerHTML = `
    <div class="guide-title">${escapeHtml(step.title || '')}</div>
    <div>${escapeHtml(step.description || '')}</div>
    <div class="guide-section">
      <h4>示例输入</h4>
      <pre>${escapeHtml(step.exampleInput || '')}</pre>
    </div>
    <div class="guide-section">
      <h4>示例输出片段</h4>
      <pre>${escapeHtml(step.exampleOutput || '')}</pre>
    </div>
    <div class="guide-section">
      <h4>常见错误</h4>
      <div class="guide-errors">${(step.commonErrors || []).map((item) => `<div>• ${escapeHtml(item)}</div>`).join('')}</div>
    </div>
  `;
}

function renderGuide() {
  if (el.guideTitle) {
    el.guideTitle.textContent = state.guide.title || '使用引导';
  }
  if (el.guideIntro) {
    el.guideIntro.textContent = state.guide.intro || '';
  }
  renderGuideList();
  renderGuideDetail();

  const steps = Array.isArray(state.guide.steps) ? state.guide.steps : [];
  if (el.guidePrev) {
    el.guidePrev.disabled = state.ui.guideIndex <= 0;
  }
  if (el.guideNext) {
    el.guideNext.textContent = state.ui.guideIndex >= steps.length - 1 ? '完成' : '下一步';
  }
}

async function openGuide(auto = false) {
  showModal(el.guideModal, true);
  renderGuide();

  if (auto && state.settings.onboardingSeenVersion < state.settings.uiVersion) {
    state.settings.onboardingSeenVersion = state.settings.uiVersion;
    queueSettingsPatch({ onboardingSeenVersion: state.settings.onboardingSeenVersion });
  }
}

function closeGuide() {
  showModal(el.guideModal, false);
}

function openSettings() {
  syncSettingsForm();
  showModal(el.settingsModal, true);
}

function closeSettings() {
  showModal(el.settingsModal, false);
}

function renderAll() {
  updateNavButtons();
  renderWizard();
  renderPro();
  renderRunsList();
}

async function runWizard() {
  const skillId = getWizardSkillId();
  const brief = buildBriefFromDraft(skillId);
  const mode = getWizardMode(skillId);

  if (!String(brief || '').trim()) {
    setStatus('请先在第2步填写 Brief。');
    state.ui.wizard.step = 2;
    renderWizard();
    return;
  }

  const pipeline = ensurePipelineForSkill(skillId);
  if (!pipeline || !pipeline.stages || !pipeline.stages.length) {
    setStatus('当前技能没有可执行步骤。');
    return;
  }

  state.ui.wizard.liveStages = {};
  renderWizardLive();

  try {
    setStatus('开始执行，请稍候...');

    const result = await executePipelineRun({
      skillId,
      brief,
      mode,
      source: 'wizard',
      onStageStatus: (stage, status) => {
        if (!state.ui.wizard.liveStages[stage.id]) {
          state.ui.wizard.liveStages[stage.id] = {
            stageId: stage.id,
            stageName: stage.name,
            status,
            output: ''
          };
        } else {
          state.ui.wizard.liveStages[stage.id].status = status;
        }
        renderWizardLive();
      },
      onStageOutput: (stage, output) => {
        if (!state.ui.wizard.liveStages[stage.id]) {
          state.ui.wizard.liveStages[stage.id] = {
            stageId: stage.id,
            stageName: stage.name,
            status: 'running',
            output
          };
        } else {
          state.ui.wizard.liveStages[stage.id].output = output;
        }
        renderWizardLive();
      }
    });

    if (result.run.runType === 'crawler' && ['done', 'partial'].includes(result.run.status)) {
      await refreshContextCatalogState();
      await reloadIncludedCatalogFiles();
      renderProFilters();
      renderProFiles();
    }

    state.ui.wizard.lastRunId = result.run.id;
    state.ui.selectedRunId = result.run.id;

    if (result.run.status === 'partial') {
      setStatus('执行完成（部分成功），请在运行详情查看失败项。');
    } else {
      setStatus('执行完成，已写入运行记录。');
    }
    switchView('execution');
    await selectRun(result.run.id);
  } catch (err) {
    setStatus(`执行失败：${err.message}`);
  }
}

async function openWizardRunDetail() {
  if (!state.ui.wizard.lastRunId) {
    setStatus('当前没有可查看的运行记录。');
    return;
  }

  switchView('execution');
  await selectRun(state.ui.wizard.lastRunId);
}

function bindWizardNavigation() {
  el.wizardPrev.addEventListener('click', () => {
    if (state.ui.wizard.step <= 1) return;
    state.ui.wizard.step -= 1;
    renderWizard();
  });

  el.wizardNext.addEventListener('click', () => {
    const skillId = getWizardSkillId();

    if (state.ui.wizard.step >= 5) {
      switchView('home');
      return;
    }

    const message = validateWizardStep(skillId, state.ui.wizard.step);
    if (message) {
      setStatus(message);
      return;
    }

    state.ui.wizard.step += 1;
    renderWizard();
  });
}

function bindEvents() {
  if (el.navHome) {
    el.navHome.addEventListener('click', () => {
      switchView('home');
    });
  }

  if (el.navExecution) {
    el.navExecution.addEventListener('click', async () => {
      switchView('execution');
      await refreshRuns();
      if (state.ui.selectedRunId) {
        await selectRun(state.ui.selectedRunId);
      }
    });
  }

  if (el.navPro) {
    el.navPro.addEventListener('click', async () => {
      await ensureFilesLoaded(state.activeSkillId);
      switchView('pro');
      renderPro();
    });
  }

  if (el.openGuide) {
    el.openGuide.addEventListener('click', () => openGuide(false));
  }

  if (el.openSettings) {
    el.openSettings.addEventListener('click', openSettings);
  }

  if (el.homeXhs) {
    el.homeXhs.addEventListener('click', () => startWizard('xhs'));
  }

  if (el.homeWechat) {
    el.homeWechat.addEventListener('click', () => startWizard('wechat'));
  }

  if (el.homeCrawler) {
    el.homeCrawler.addEventListener('click', () => startWizard('crawler'));
  }

  if (el.homeRuns) {
    el.homeRuns.addEventListener('click', async () => {
      switchView('execution');
      await refreshRuns();
    });
  }

  if (el.homeSettings) {
    el.homeSettings.addEventListener('click', openSettings);
  }

  if (el.wizardBackHome) {
    el.wizardBackHome.addEventListener('click', () => switchView('home'));
  }

  if (el.wizardLoadDefaults) {
    el.wizardLoadDefaults.addEventListener('click', async () => {
      const skillId = getWizardSkillId();
      await loadDefaultContextForSkill(skillId);
      renderWizardStep3(skillId);
      renderProFiles();
      renderProFilters();
      setStatus('已加载默认素材包。');
    });
  }

  if (el.wizardAddFiles) {
    el.wizardAddFiles.addEventListener('click', async () => {
      const skillId = getWizardSkillId();
      await addManualFiles(skillId);
      renderWizardStep3(skillId);
      renderProFiles();
      setStatus('已添加本地文件。');
    });
  }

  if (el.wizardSaveParams) {
    el.wizardSaveParams.addEventListener('click', async () => {
      const payload = {
        engine: el.wizardEngine.value,
        defaultModel: el.wizardModel.value || DEFAULT_CODEX_MODEL,
        modelReasoningEffort: normalizeReasoningEffort(el.wizardReasoningEffort ? el.wizardReasoningEffort.value : DEFAULT_REASONING_EFFORT),
        temperature: parseFloat(el.wizardTemperature.value),
        maxOutputTokens: parseInt(el.wizardMaxTokens.value, 10)
      };
      try {
        await saveSettingsNow(payload);
        setStatus('执行参数已保存。');
      } catch (err) {
        setStatus(`保存失败：${err.message}`);
      }
    });
  }

  if (el.wizardRun) {
    el.wizardRun.addEventListener('click', runWizard);
  }

  if (el.wizardOpenRunDetail) {
    el.wizardOpenRunDetail.addEventListener('click', openWizardRunDetail);
  }

  bindWizardNavigation();

  if (el.refreshRuns) {
    el.refreshRuns.addEventListener('click', refreshRuns);
  }

  if (el.proFilterSearch) {
    el.proFilterSearch.addEventListener('input', renderProFilters);
  }

  if (el.proClearFilters) {
    el.proClearFilters.addEventListener('click', async () => {
      const skillId = state.activeSkillId;
      getFilesForSkill(skillId).forEach((file) => {
        if (file.source === 'catalog') {
          file.include = false;
        }
      });
      await persistContextSelection(skillId);
      renderProFilters();
      renderProFiles();
      renderWizardStep3(skillId);
    });
  }

  if (el.proLoadDefaults) {
    el.proLoadDefaults.addEventListener('click', async () => {
      await loadDefaultContextForSkill(state.activeSkillId);
      renderPro();
      renderWizardStep3(state.activeSkillId);
      setStatus('已加载默认素材包。');
    });
  }

  if (el.proAddFiles) {
    el.proAddFiles.addEventListener('click', async () => {
      await addManualFiles(state.activeSkillId);
      renderProFiles();
      renderWizardStep3(state.activeSkillId);
      setStatus('已添加本地文件。');
    });
  }

  if (el.proAddStage) {
    el.proAddStage.addEventListener('click', () => {
      const pipeline = ensurePipelineForSkill(state.activeSkillId);
      pipeline.stages.push(createStage());
      queueSavePrompts();
      renderProPipeline();
      renderProOutputs();
    });
  }

  if (el.proRunAll) {
    el.proRunAll.addEventListener('click', handleProRunAll);
  }

  if (el.proPreviewPrompt) {
    el.proPreviewPrompt.addEventListener('click', handleProPreviewPrompt);
  }

  if (el.closeSettings) {
    el.closeSettings.addEventListener('click', closeSettings);
  }

  if (el.engineSelect) {
    el.engineSelect.addEventListener('change', () => {
      state.settings.engine = el.engineSelect.value;
      updateEngineVisibility();
    });
  }

  if (el.saveSettings) {
    el.saveSettings.addEventListener('click', async () => {
      const modelFromCodex = el.codexModelInput ? el.codexModelInput.value.trim() : '';
      const modelFromOpenAI = el.defaultModel ? el.defaultModel.value.trim() : '';
      const payload = {
        engine: el.engineSelect.value,
        codexPath: el.codexPathInput.value.trim(),
        apiKey: el.apiKeyInput.value.trim(),
        defaultModel: modelFromCodex || modelFromOpenAI || DEFAULT_CODEX_MODEL,
        modelReasoningEffort: normalizeReasoningEffort(el.codexReasoningEffort ? el.codexReasoningEffort.value : DEFAULT_REASONING_EFFORT),
        temperature: parseFloat(el.temperatureInput.value),
        maxOutputTokens: parseInt(el.maxTokensInput.value, 10),
        activeSkillId: state.activeSkillId,
        contextSelectionBySkill: state.settings.contextSelectionBySkill,
        lastModeBySkill: state.settings.lastModeBySkill,
        wizardDraftBySkill: state.settings.wizardDraftBySkill
      };

      try {
        await saveSettingsNow(payload);
        syncWizardParamFields();
        closeSettings();
        setStatus('设置已保存。');
      } catch (err) {
        setStatus(`设置保存失败：${err.message}`);
      }
    });
  }

  if (el.syncPrompts) {
    el.syncPrompts.addEventListener('click', async () => {
      const confirmed = window.confirm('同步默认提示词会覆盖本机 prompts.json，是否继续？');
      if (!confirmed) return;

      const defaults = await window.api.getDefaultPrompts();
      await window.api.savePrompts(defaults);
      await loadPrompts();
      renderProPipeline();
      renderProOutputs();
      setStatus('已同步默认提示词。');
    });
  }

  if (el.closeGuide) {
    el.closeGuide.addEventListener('click', closeGuide);
  }

  if (el.guidePrev) {
    el.guidePrev.addEventListener('click', () => {
      if (state.ui.guideIndex <= 0) return;
      state.ui.guideIndex -= 1;
      renderGuide();
    });
  }

  if (el.guideNext) {
    el.guideNext.addEventListener('click', () => {
      const steps = Array.isArray(state.guide.steps) ? state.guide.steps : [];
      if (state.ui.guideIndex >= steps.length - 1) {
        closeGuide();
        return;
      }
      state.ui.guideIndex += 1;
      renderGuide();
    });
  }
}

async function loadPrompts() {
  const userPrompts = await window.api.listPrompts();
  const defaultPrompts = await window.api.getDefaultPrompts();

  const userPipelines = Array.isArray(userPrompts.pipelines) ? userPrompts.pipelines : [];
  const defaultPipelines = Array.isArray(defaultPrompts.pipelines) ? defaultPrompts.pipelines : [];

  const map = new Map();
  userPipelines.forEach((item) => {
    if (item && item.id) {
      map.set(item.id, item);
    }
  });

  defaultPipelines.forEach((item) => {
    if (item && item.id && !map.has(item.id)) {
      map.set(item.id, item);
    }
  });

  state.pipelines = Array.from(map.values());

  state.wizardSummaryTemplates = {
    ...(isObject(defaultPrompts.wizardSummaryTemplates) ? defaultPrompts.wizardSummaryTemplates : {}),
    ...(isObject(userPrompts.wizardSummaryTemplates) ? userPrompts.wizardSummaryTemplates : {})
  };
}

async function initializeData() {
  const [rawSettings, skills, catalog, schema, guide] = await Promise.all([
    window.api.getSettings(),
    window.api.getSkills(),
    window.api.getContextCatalog(),
    window.api.getWizardSchema(),
    window.api.getGuide()
  ]);

  applySettingsState(rawSettings, true);
  state.skills = Array.isArray(skills) ? skills : [];
  state.contextCatalog = catalog && catalog.groups ? catalog : { groups: [] };
  state.wizardSchema = schema && schema.skills ? schema : { version: 2, skills: {} };
  state.guide = guide && Array.isArray(guide.steps) ? guide : { title: '使用引导', intro: '', steps: [] };

  await loadPrompts();

  if (!state.skills.find((skill) => skill.id === state.activeSkillId) && state.skills.length) {
    state.activeSkillId = state.skills[0].id;
  }

  state.ui.wizard.skillId = state.activeSkillId;

  await ensureFilesLoaded(state.activeSkillId);

  const activeSchema = getWizardSkillSchema(state.activeSkillId);
  if (activeSchema) {
    ensureWizardDraftDefaults(state.activeSkillId);
  }

  if (el.proBriefInput) {
    const quickSop = getSkill(state.activeSkillId) && getSkill(state.activeSkillId).quickTemplates
      ? getSkill(state.activeSkillId).quickTemplates.sop
      : '';
    el.proBriefInput.value = quickSop || '';
  }

  await refreshRuns();
  if (state.runs.length) {
    state.ui.selectedRunId = state.runs[0].id;
  }

  window.api.onStreamEvent(handleStreamEvent);
  window.api.onCodexEvent(handleStreamEvent);

  bindEvents();
  renderAll();

  switchView('home');
  setStatus('就绪');
  setProgress(0, 0);

  if (state.settings.onboardingSeenVersion < state.settings.uiVersion) {
    openGuide(true);
  }
}

initializeData().catch((err) => {
  setStatus(`初始化失败：${err.message}`);
  console.error(err);
});

