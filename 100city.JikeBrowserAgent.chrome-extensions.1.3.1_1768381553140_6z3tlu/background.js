// Background Service Worker - V8.0
// 极客增长浏览器助手 - Jike Browser Agent

// ========== 引入配置 ==========
importScripts('config.js');

// ========== 脚本引擎（内联） ==========
class ScriptEngine {
  constructor() {
    this.script = null;
    this.data = {};
    this.variables = {};
    this.currentStepIndex = 0;
    this.status = 'idle';
    this.onProgress = null;
    this.logs = [];
  }

  load(script, data = {}) {
    this.script = script;
    this.data = data;
    this.variables = { ...data };
    this.currentStepIndex = 0;
    this.status = 'idle';
    this.logs = [];
    this._log('info', `脚本加载: ${script.name || script.id}`);
  }

  async run() {
    if (!this.script || !this.script.steps) throw new Error('没有加载脚本');
    this.status = 'running';
    this._log('info', '开始执行脚本');

    try {
      while (this.currentStepIndex < this.script.steps.length) {
        // 检查是否被暂停或停止
        if (this.status === 'paused') return { success: true, status: 'paused', stepIndex: this.currentStepIndex };
        if (this.status === 'stopped' || this.status === 'idle') {
          this._log('info', '脚本被停止');
          return { success: false, status: 'stopped', stepIndex: this.currentStepIndex };
        }
        const step = this.script.steps[this.currentStepIndex];
        const result = await this._executeStep(step);
        this._log('info', `步骤结果: success=${result.success}, goto=${result.goto}`);
        if (result.goto) {
          const idx = this.script.steps.findIndex(s => s.id === result.goto);
          this._log('info', `跳转: goto=${result.goto}, 目标索引=${idx}`);
          if (idx >= 0) { this.currentStepIndex = idx; continue; }
        }
        // end 结束脚本
        if (result.end) {
          this.status = 'completed';
          return { success: true, status: 'completed' };
        }
        if (!result.success) {
          this.status = 'failed';
          return { success: false, error: result.error, stepIndex: this.currentStepIndex };
        }
        this.currentStepIndex++;
      }
      this.status = 'completed';
      return { success: true, status: 'completed' };
    } catch (e) {
      this.status = 'failed';
      return { success: false, error: e.message };
    }
  }

  async _executeStep(step) {
    const stepId = step.id || `step_${this.currentStepIndex}`;
    this._log('info', `执行: ${stepId} (${step.action})`);
    if (this.onProgress) this.onProgress({ stepIndex: this.currentStepIndex, totalSteps: this.script.steps.length, stepId, action: step.action, status: 'running' });

    try {
      const params = this._replaceVars(step.params || {});
      let result;
      
      // 特殊 action 处理
      if (step.action === 'waitForAny') {
        result = await this._callAction('waitForAny', { conditions: step.conditions, timeout: step.timeout || 10000 });
        this._log('info', `waitForAny 结果: success=${result.success}, goto=${result.goto}`);
        if (result.success && result.goto) {
          this._log('info', `waitForAny 返回 goto: ${result.goto}`);
          return { success: true, goto: result.goto };
        }
      } 
      // if 条件判断
      else if (step.action === 'if') {
        const condResult = this._evaluateCondition(step.condition);
        this._log('info', `if 条件: ${step.condition} → ${condResult}`);
        if (condResult && step.goto) {
          return { success: true, goto: step.goto };
        } else if (!condResult && step.else) {
          return { success: true, goto: step.else };
        }
        result = { success: true };
      }
      // goto 无条件跳转
      else if (step.action === 'goto') {
        const target = step.target || step.params?.target;
        if (target) {
          this._log('info', `goto 跳转: ${target}`);
          return { success: true, goto: target };
        }
        result = { success: false, error: 'goto 缺少 target' };
      }
      // end 结束脚本
      else if (step.action === 'end') {
        this._log('info', '脚本正常结束');
        this.status = 'completed';
        return { success: true, end: true };
      }
      else {
        result = await this._callAction(step.action, params);
      }

      // 步骤间等待（支持数组范围随机，如 [3000, 6000]）
      let waitTime;
      if (Array.isArray(step.wait)) {
        const [minWait, maxWait] = step.wait;
        waitTime = minWait + Math.floor(Math.random() * (maxWait - minWait));
        this._log('info', `随机等待: ${waitTime}ms (范围 ${minWait}-${maxWait})`);
      } else {
        waitTime = step.wait || (800 + Math.floor(Math.random() * 1200));
      }
      await new Promise(r => setTimeout(r, waitTime));

      if (this.onProgress) this.onProgress({ stepIndex: this.currentStepIndex, totalSteps: this.script.steps.length, stepId, action: step.action, status: result.success ? 'completed' : 'failed' });
      return result;
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async _callAction(action, params) {
    // 直接调用 executeAction，不通过消息
    try {
      return await executeAction(action, params, null);
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  _replaceVars(obj) {
    if (typeof obj === 'string') {
      // 如果整个字符串就是一个变量引用，直接返回变量值（保持类型）
      const fullMatch = obj.match(/^\{\{([^}]+)\}\}$/);
      if (fullMatch) {
        const val = this._getVar(fullMatch[1].trim());
        return val !== undefined ? val : obj;
      }
      // 否则做字符串替换
      return obj.replace(/\{\{([^}]+)\}\}/g, (m, path) => {
        const val = this._getVar(path.trim());
        return val !== undefined ? val : m;
      });
    }
    if (Array.isArray(obj)) return obj.map(i => this._replaceVars(i));
    if (typeof obj === 'object' && obj !== null) {
      const r = {};
      for (const k in obj) r[k] = this._replaceVars(obj[k]);
      return r;
    }
    return obj;
  }

  _getVar(path) {
    const parts = path.split('.');
    let v = this.variables;
    for (const p of parts) {
      if (v == null) return undefined;
      const m = p.match(/^(\w+)\[(\d+)\]$/);
      if (m) { v = v[m[1]]; if (Array.isArray(v)) v = v[parseInt(m[2])]; }
      else v = v[p];
    }
    return v;
  }

  // 条件表达式求值
  _evaluateCondition(condition) {
    if (!condition) return false;
    
    // 先替换变量
    let expr = condition.replace(/\{\{([^}]+)\}\}/g, (m, path) => {
      const val = this._getVar(path.trim());
      if (val === undefined || val === null) return 'null';
      if (typeof val === 'string') return `"${val}"`;
      return String(val);
    });
    
    this._log('info', `条件求值: ${condition} → ${expr}`);
    
    // 简单表达式解析（支持 ==, !=, >, <, >=, <=）
    const match = expr.match(/^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$/);
    if (match) {
      let [, left, op, right] = match;
      left = left.trim().replace(/^["']|["']$/g, '');
      right = right.trim().replace(/^["']|["']$/g, '');
      
      switch (op) {
        case '==': return left == right;
        case '!=': return left != right;
        case '>': return Number(left) > Number(right);
        case '<': return Number(left) < Number(right);
        case '>=': return Number(left) >= Number(right);
        case '<=': return Number(left) <= Number(right);
      }
    }
    
    // 布尔值
    if (expr === 'true') return true;
    if (expr === 'false' || expr === 'null' || expr === '""') return false;
    
    return !!expr;
  }

  pause() { if (this.status === 'running') this.status = 'paused'; }
  stop() { this.status = 'stopped'; }

  _log(level, msg) {
    this.logs.push({ time: new Date().toISOString(), level, message: msg });
    console.log(`[ScriptEngine] ${msg}`);
  }
}

const scriptEngine = new ScriptEngine();

// ========== 日志系统 ==========
const LOG_LEVEL = CONFIG.LOG_LEVEL;
const DEBUG_MODE = CONFIG.DEBUG_MODE;

function log(level, ...args) {
  const prefix = `[Agent]`;
  if (level === LOG_LEVEL.DEBUG && !DEBUG_MODE) return;
  console.log(prefix, level, ...args);
}

// ========== 状态变量 ==========
let controlledTabId = null;
let controlledGroupId = null;
let debuggerAttached = false;
let maskStates = new Map();  // key: tabId, value: { visible, status, blocking }
let extensionStatus = 'idle';  // 'idle' | 'running'
let currentSessionId = null;
let senderWindowId = null;
let userActiveTabId = null;  // 用户原来的标签页 ID（用于焦点切回）
let taskTabIds = new Set();  // 所有任务相关的标签页 ID

// 功能开关（从配置读取）
const AUTO_SWITCH_BACK = CONFIG.AUTO_SWITCH_BACK;
const AUTO_CLOSE_OLD_TAB = CONFIG.AUTO_CLOSE_OLD_TAB;

// 会话配置
let sessionConfig = {
  apiBase: null,
  session_id: null,
  execution_id: null,
  interrupt_id: null,
  xToken: null
};

// ========== 消息监听 ==========
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 内部消息静默处理
  if (message.action === 'contentScriptReady' || message.action === 'getMaskState') {
    if (message.action === 'getMaskState') {
      const tabId = sender.tab?.id;
      if (tabId && maskStates.has(tabId)) {
        sendResponse({ success: true, ...maskStates.get(tabId) });
      } else {
        sendResponse({ success: true, visible: false, status: '', blocking: false });
      }
    }
    return true;
  }
  
  // popup 状态查询
  if (message.type === 'getStatus') {
    sendResponse({ success: true, status: extensionStatus, sessionId: currentSessionId });
    return true;
  }
  
  // 兼容 content-bridge 消息格式
  let normalizedMessage = message;
  if (message.type === 'execute' && message.action) {
    normalizedMessage = { action: message.action, params: message.params || {} };
  }
  
  // 记录关键操作
  const action = normalizedMessage.action;
  
  // 忽略空 action（后端可能发送格式错误的消息）
  if (!action) {
    log(LOG_LEVEL.WARN, '收到空 action，忽略');
    sendResponse({ success: true, ignored: true });
    return true;
  }
  
  if (!['getMaskState', 'setInterruptId'].includes(action)) {
    log(LOG_LEVEL.ACTION, `${action}`, normalizedMessage.params || {});
  }
  
  handleCommand(normalizedMessage, sender)
    .then(async (result) => {
      // 执行成功后上报
      if (action && !['ping', 'getStatus', 'startSession', 'endSession', 'setStatus', 'setInterruptId'].includes(action)) {
        // 区分成功和失败的日志
        if (result.success === false) {
          log(LOG_LEVEL.ERROR, `${action} 失败:`, result.error);
        }
        await postResultToBackend(action, result);
      }
      sendResponse(result);
    })
    .catch(async (error) => {
      log(LOG_LEVEL.ERROR, `${action} 异常:`, error.message);
      const errorResult = { success: false, error: error.message };
      if (action && !['ping', 'getStatus', 'startSession', 'endSession', 'setStatus', 'setInterruptId'].includes(action)) {
        await postResultToBackend(action, errorResult);
      }
      sendResponse(errorResult);
    });
  
  return true;
});

// 需要自动收集页面信息的操作
const ACTIONS_NEED_ARTIFACTS = ['createTab', 'navigate', 'click', 'type', 'scroll', 'scrollToTop', 'scrollToBottom', 'goBack', 'goForward', 'refresh'];

// 可能触发页面跳转的操作
const ACTIONS_MAY_NAVIGATE = ['click', 'goBack', 'goForward', 'refresh', 'navigateToLink', 'navigateWithToken'];

// 需要重试的操作（网络或页面加载问题可能导致失败）
const ACTIONS_NEED_RETRY = ['click', 'type', 'scroll'];

// 操作名称映射（用于进度反馈）
const ACTION_NAMES = {
  createTab: '正在打开页面...',
  navigate: '正在跳转...',
  click: '正在点击...',
  type: '正在输入...',
  scroll: '正在滚动...',
  scrollToTop: '正在滚动到顶部...',
  scrollToBottom: '正在滚动到底部...',
  goBack: '正在返回...',
  goForward: '正在前进...',
  refresh: '正在刷新...',
  screenshot: '正在截图...',
  collectArtifacts: '正在分析页面...',
};

// 更新遮罩状态
async function updateMaskStatus(status) {
  if (controlledTabId) {
    try {
      await chrome.tabs.sendMessage(controlledTabId, { action: 'updateStatus', params: { status } });
    } catch (e) {}
  }
}

async function handleCommand(message, sender) {
  const { action, params = {} } = message;
  
  // 记录发送消息的窗口 ID
  if (sender?.tab?.windowId) {
    senderWindowId = sender.tab.windowId;
  }
  
  // 记录操作前的 URL
  let urlBefore = null;
  if (ACTIONS_MAY_NAVIGATE.includes(action) && controlledTabId) {
    try {
      const tab = await chrome.tabs.get(controlledTabId);
      urlBefore = tab.url;
    } catch (e) {}
  }
  
  // 标记开始执行操作
  isExecutingAction = true;
  
  // 显示操作进度
  const actionName = ACTION_NAMES[action];
  if (actionName && controlledTabId) {
    await updateMaskStatus(actionName);
  }
  
  try {
    // 执行操作（带重试机制）
    let result;
    const maxRetries = ACTIONS_NEED_RETRY.includes(action) ? 2 : 0;
    let lastError = null;
    
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        result = await executeAction(action, params, sender);
        
        // 成功或明确失败（不是异常），不重试
        if (result.success !== false) break;
        
        // 失败但可重试
        if (attempt < maxRetries) {
          log(LOG_LEVEL.WARN, `${action} 失败，重试 ${attempt + 1}/${maxRetries}:`, result.error);
          await sleep(300);  // 等待 300ms 后重试
        }
        lastError = result.error;
      } catch (e) {
        lastError = e.message;
        if (attempt < maxRetries) {
          log(LOG_LEVEL.WARN, `${action} 异常，重试 ${attempt + 1}/${maxRetries}:`, e.message);
          await sleep(300);
        } else {
          throw e;
        }
      }
    }
    
    // 如果重试后仍失败，记录日志
    if (result?.success === false && maxRetries > 0) {
      log(LOG_LEVEL.WARN, `${action} 重试 ${maxRetries} 次后仍失败`);
    }
    
    // 如果是可能触发跳转的操作，等待页面加载
    if (ACTIONS_MAY_NAVIGATE.includes(action) && result.success !== false && controlledTabId) {
      await sleep(500);
      try {
        const tab = await chrome.tabs.get(controlledTabId);
        if (tab.url !== urlBefore) {
          log(LOG_LEVEL.DEBUG, `页面跳转: ${urlBefore} → ${tab.url}`);
          await waitForTabLoad(controlledTabId);
        }
      } catch (e) {}
    }
    
    // 操作完成，检查是否有待切换的新标签页
    isExecutingAction = false;
    if (pendingNewTab) {
      const tab = pendingNewTab;
      pendingNewTab = null;
      await switchToNewTab(tab);
      // 等待新标签页加载
      await waitForTabLoad(controlledTabId);
    }
    
    // 自动收集页面信息
    if (ACTIONS_NEED_ARTIFACTS.includes(action) && result.success !== false) {
      await updateMaskStatus('正在分析页面...');
      await sleep(500);  // 等待页面图片加载
      try {
        const artifacts = await collectArtifactsInternal();
        result.screenshots = artifacts.screenshots;
        result.elements = artifacts.elements;
        result.pageInfo = artifacts.pageInfo;
        if (artifacts.hints) {
          result.hints = artifacts.hints;
        }
      } catch (e) {
        log(LOG_LEVEL.WARN, '收集页面信息失败:', e.message);
      }
    }
    
    // 恢复默认状态
    if (controlledTabId) {
      await updateMaskStatus('Agent 控制中...');
    }
    
    return result;
  } catch (e) {
    isExecutingAction = false;
    pendingNewTab = null;
    // 恢复默认状态
    if (controlledTabId) {
      await updateMaskStatus('Agent 控制中...');
    }
    throw e;
  }
}

async function executeAction(action, params, sender) {
  switch (action) {
    case 'ping':
      return { success: true, message: 'pong' };
    
    case 'wait':
      // 支持数组范围随机，如 { ms: [3000, 6000] }
      let waitMs;
      if (Array.isArray(params.ms)) {
        const [minMs, maxMs] = params.ms;
        waitMs = minMs + Math.floor(Math.random() * (maxMs - minMs));
        log(LOG_LEVEL.INFO, `随机等待: ${waitMs}ms (范围 ${minMs}-${maxMs})`);
      } else {
        waitMs = params.ms || 1000;
      }
      await new Promise(r => setTimeout(r, waitMs));
      return { success: true, waited: waitMs };
    
    case 'mouseMove':
      // 模拟鼠标移动轨迹（反风控）
      return await mouseMove(params);
    
    case 'setStatus':
      extensionStatus = params.status || 'idle';
      currentSessionId = params.sessionId || null;
      chrome.runtime.sendMessage({ type: 'statusChanged', status: extensionStatus }).catch(() => {});
      return { success: true, status: extensionStatus };
    
    case 'startSession':
      sessionConfig = {
        apiBase: params.apiBase,
        session_id: params.session_id,
        execution_id: params.execution_id,
        interrupt_id: params.interrupt_id || null,
        xToken: params.xToken || null
      };
      extensionStatus = 'running';
      currentSessionId = params.session_id;
      // 记录用户当前的标签页（用于焦点切回）
      if (sender?.tab?.id) {
        userActiveTabId = sender.tab.id;
        log(LOG_LEVEL.DEBUG, `记录用户标签页(from sender): ${userActiveTabId}`);
      } else {
        try {
          const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (activeTab) {
            userActiveTabId = activeTab.id;
            log(LOG_LEVEL.DEBUG, `记录用户标签页(from query): ${userActiveTabId}`);
          }
        } catch (e) {
          log(LOG_LEVEL.WARN, '获取用户标签页失败:', e.message);
        }
      }
      log(LOG_LEVEL.SUCCESS, '会话开始:', params.session_id);
      chrome.runtime.sendMessage({ type: 'statusChanged', status: 'running' }).catch(() => {});
      return { success: true, sessionConfig };
    
    case 'endSession':
      log(LOG_LEVEL.INFO, '会话结束:', sessionConfig.session_id);
      sessionConfig = { apiBase: null, session_id: null, execution_id: null, interrupt_id: null, xToken: null };
      extensionStatus = 'idle';
      currentSessionId = null;
      userActiveTabId = null;
      taskTabIds.clear();
      if (controlledTabId) {
        maskStates.delete(controlledTabId);
        try { await chrome.tabs.sendMessage(controlledTabId, { action: 'hideMask', params: {} }); } catch (e) {}
      }
      controlledTabId = null;
      controlledGroupId = null;
      debuggerAttached = false;
      chrome.runtime.sendMessage({ type: 'statusChanged', status: 'idle' }).catch(() => {});
      return { success: true };
    
    case 'setInterruptId':
      sessionConfig.interrupt_id = params.interrupt_id;
      return { success: true, interrupt_id: params.interrupt_id };
    
    case 'createTab':
      return await createControlledTab(params.url);
    case 'navigate':
      return await navigateTo(params.url);
    case 'navigateToLink':
      // 获取链接元素的 href 并在当前页面跳转
      await sendToContentScript('navigateToLink', params);
      await waitForTabLoad(controlledTabId);
      return { success: true };
    case 'navigateWithToken':
      // 跳转到固定 URL，自动拼接当前页面的 token
      await sendToContentScript('navigateWithToken', params);
      await waitForTabLoad(controlledTabId);
      return { success: true };
    case 'scroll':
    case 'scrollToTop':
    case 'scrollToBottom':
    case 'click':
    case 'clickButtonByText':
    case 'getPageInfo':
    case 'getElements':
    case 'executeSearch':
    case 'uploadImageFromUrl':
    case 'fillRichEditor':
      return await sendToContentScript(action, params);
    case 'setCodeMirrorValue':
      console.log('[Agent] setCodeMirrorValue params:', params);
      
      // 拼接完整的 Markdown 内容
      let cmContent = params?.content || params;
      
      // 添加图片（如果有）
      if (params?.images && Array.isArray(params.images) && params.images.length > 0) {
        cmContent += '\n\n';
        for (const imgUrl of params.images) {
          cmContent += `![图片](${imgUrl})\n\n`;
        }
      }
      
      // 添加视频（如果有）
      if (params?.video) {
        cmContent += `\n\n<video src="${params.video}" controls></video>\n`;
      }
      
      console.log('[Agent] setCodeMirrorValue content:', cmContent);
      
      // 直接在页面上下文中执行脚本
      if (!controlledTabId) {
        return { success: false, error: '没有受控标签页' };
      }
      
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: controlledTabId },
          world: 'MAIN',  // 在页面的主世界中执行，可以访问页面的 JS 对象
          func: (content) => {
            console.log('[Agent-MAIN] setCodeMirrorValue 开始执行');
            
            // 掘金使用 ByteMD 编辑器，内容编辑器在 .bytemd-editor 内
            // 优先查找 bytemd-editor 内的 CodeMirror
            const allCodeMirrors = document.querySelectorAll('.CodeMirror');
            console.log('[Agent-MAIN] 找到 CodeMirror 数量:', allCodeMirrors.length);
            
            let targetCM = null;
            
            // 方法1: 查找 bytemd-editor 内的 CodeMirror（掘金内容编辑器）
            for (const cm of allCodeMirrors) {
              if (cm.closest('.bytemd-editor')) {
                targetCM = cm;
                console.log('[Agent-MAIN] ✅ 找到 bytemd-editor 内的 CodeMirror');
                break;
              }
            }
            
            // 方法2: 如果没找到，查找 bytemd 内的 CodeMirror
            if (!targetCM) {
              for (const cm of allCodeMirrors) {
                if (cm.closest('.bytemd')) {
                  targetCM = cm;
                  console.log('[Agent-MAIN] ✅ 找到 bytemd 内的 CodeMirror');
                  break;
                }
              }
            }
            
            // 方法3: 使用第一个有实例的 CodeMirror
            if (!targetCM) {
              for (const cm of allCodeMirrors) {
                if (cm.CodeMirror) {
                  targetCM = cm;
                  console.log('[Agent-MAIN] ⚠️ 使用第一个可用的 CodeMirror');
                  break;
                }
              }
            }
            
            if (targetCM && targetCM.CodeMirror) {
              targetCM.CodeMirror.setValue(content);
              console.log('[Agent-MAIN] 🎉 内容设置成功，长度:', content.length);
              return { success: true, contentLength: content.length };
            } else {
              console.log('[Agent-MAIN] ❌ 找不到 CodeMirror 实例');
              return { success: false, error: '找不到 CodeMirror 实例' };
            }
          },
          args: [cmContent]
        });
        
        console.log('[Agent] executeScript 结果:', results);
        return results[0]?.result || { success: false, error: '脚本执行失败' };
      } catch (error) {
        console.error('[Agent] executeScript 错误:', error);
        return { success: false, error: error.message };
      }
    case 'addXhsTopic':
    case 'addDouyinTopic':
      return await sendToContentScript(action, params);
    case 'uploadMultipleImages':
      // 批量上传图片（一次性上传所有图片）
      if (!params.images || !Array.isArray(params.images)) {
        return { success: false, error: '缺少 images 数组' };
      }
      log(LOG_LEVEL.INFO, `一次性上传 ${params.images.length} 张图片`);
      return await sendToContentScript('uploadMultipleImagesFromUrls', { 
        imageUrls: params.images, 
        selector: params.selector || 'input.upload-input' 
      });
    case 'uploadImages':
      // uploadImages 别名（兼容新 JSON 格式）
      if (!params.images || !Array.isArray(params.images)) {
        return { success: false, error: '缺少 images 数组' };
      }
      log(LOG_LEVEL.INFO, `uploadImages: ${params.images.length} 张图片`);
      return await sendToContentScript('uploadMultipleImagesFromUrls', { 
        imageUrls: params.images, 
        selector: params.selector || 'input.upload-input' 
      });
    
    case 'uploadImagesCDP':
      // 使用 CDP 方式上传图片（适用于 React 等框架的受控组件）
      if (!params.images || !Array.isArray(params.images)) {
        return { success: false, error: '缺少 images 数组' };
      }
      log(LOG_LEVEL.INFO, `uploadImagesCDP: ${params.images.length} 张图片`);
      return await uploadImagesCDP(params.images, params.selector || "input[type='file']");
    
    case 'clickWechatPublish':
      // 点击微信视频号发表按钮
      log(LOG_LEVEL.INFO, '点击微信视频号发表按钮');
      return await sendToContentScript('clickWechatPublish', {});
    
    case 'uploadVideo':
      // 上传视频
      if (!params.videoUrl) {
        return { success: false, error: '缺少 videoUrl' };
      }
      log(LOG_LEVEL.INFO, `上传视频: ${params.videoUrl}`);
      return await sendToContentScript('uploadVideoFromUrl', { 
        videoUrl: params.videoUrl, 
        selector: params.selector || 'input.upload-input' 
      });
    case 'fillXhsVideoTitle':
      // 填写视频标题（选择器不同于图文）
      return await sendToContentScript('fillXhsVideoTitle', params);
    case 'addMultipleTopics':
      // 批量添加话题（可选，没有就跳过）
      if (!params.topics || !Array.isArray(params.topics) || params.topics.length === 0) {
        log(LOG_LEVEL.INFO, '没有话题需要添加，跳过');
        return { success: true, count: 0 };
      }
      // 根据平台选择不同的话题添加函数
      let topicAction, topicDelay;
      if (params.platform === 'douyin') {
        topicAction = 'addDouyinTopic';
        topicDelay = 800;
      } else if (params.platform === 'bilibili') {
        topicAction = 'addBilibiliTag';
        topicDelay = 500;
      } else {
        topicAction = 'addXhsTopic';
        topicDelay = 300;
      }
      
      for (let i = 0; i < params.topics.length; i++) {
        log(LOG_LEVEL.INFO, `添加话题 ${i + 1}/${params.topics.length}: ${params.topics[i]} (${topicAction})`);
        const result = await sendToContentScript(topicAction, { topic: params.topics[i] });
        if (!result.success) {
          log(LOG_LEVEL.WARN, `话题 ${i + 1} 添加失败: ${result.error}`);
        }
        await sleep(topicDelay);
      }
      return { success: true, count: params.topics.length };
    case 'waitForSelector':
      return await sendToContentScript(action, params);
    case 'assertSelector':
      return await sendToContentScript(action, params);
    case 'waitForAny':
      return await waitForAny(params.conditions, params.timeout);
    case 'fail':
      return { success: false, error: params.reason || '脚本主动失败' };
    case 'type':
      return await typeWithCDP(params);
    case 'pasteText':
      return await pasteText(params);
    case 'pasteMarkdown':
      // 粘贴 Markdown 内容（自动拼接图片）
      let mdContent = params.content || '';
      if (params.images && Array.isArray(params.images) && params.images.length > 0) {
        const imagesMd = params.images.map(url => `![图片](${url})`).join('\n\n');
        mdContent = mdContent + '\n\n' + imagesMd;
      }
      return await pasteText({ selector: params.selector, text: mdContent });
    
    case 'uploadMarkdownFile':
      // 上传 Markdown 文件
      log(LOG_LEVEL.INFO, `uploadMarkdownFile: 内容长度 ${params.content?.length}, 图片 ${params.images?.length || 0} 张`);
      return await sendToContentScript('uploadMarkdownFile', {
        content: params.content,
        images: params.images,
        selector: params.selector || "input[type='file'][accept*='.md']"
      });
    
    case 'pressKey':
      return await pressKeyWithCDP(params.key || 'Enter');
    case 'screenshot':
      return await captureAndUploadScreenshot();
    case 'collectArtifacts':
      return await collectArtifacts(params);
    case 'markElements':
    case 'clearMarks':
      return await sendToContentScript(action, params);
    case 'getTabInfo':
      return await getTabInfo();
    case 'goBack':
      return await goBack();
    case 'goForward':
      return await goForward();
    case 'refresh':
      return await refreshTab();
    case 'closeTab':
      return await closeTab();
    case 'focusTab':
      // 将受控标签页切到前台（解决后台标签页不渲染的问题）
      if (controlledTabId) {
        try {
          await chrome.tabs.update(controlledTabId, { active: true });
          log(LOG_LEVEL.INFO, '标签页已切到前台');
          return { success: true };
        } catch (e) {
          return { success: false, error: e.message };
        }
      }
      return { success: false, error: '没有受控标签页' };
    case 'createTabGroup':
      return await createTabGroup(params.title);
    case 'showMask':
      if (controlledTabId) {
        maskStates.set(controlledTabId, { visible: true, status: params.status || 'Agent 控制中...', blocking: params.blocking || false });
      }
      return await sendToContentScript(action, params);
    case 'hideMask':
      if (controlledTabId) {
        maskStates.set(controlledTabId, { visible: false, status: '', blocking: false });
      }
      return await sendToContentScript(action, params);
    case 'updateStatus':
      if (controlledTabId && maskStates.has(controlledTabId)) {
        maskStates.get(controlledTabId).status = params.status;
      }
      return await sendToContentScript(action, params);
    case 'userTakeover':
      if (controlledTabId) {
        maskStates.set(controlledTabId, { visible: false, status: '', blocking: false });
      }
      return { success: true, message: '用户已接管' };
    case 'runScript':
      return await runScript(params.script, params.data);
    case 'pauseScript':
      scriptEngine.pause();
      return { success: true };
    case 'resumeScript':
      return await scriptEngine.resume();
    case 'stopScript':
      scriptEngine.stop();
      return { success: true };
    case 'connectTask':
      // 修复 URL 中的双斜杠问题
      let wsUrl = params.wsUrl;
      if (wsUrl) {
        wsUrl = wsUrl.replace(/([^:])\/\//g, '$1/');
      }
      return await connectTaskWs(wsUrl);
    case 'disconnectTask':
      return disconnectTaskWs();
    case 'getTaskStatus':
      return { success: true, ...getTaskStatus() };
    default:
      throw new Error(`未知指令: ${action}`);
  }
}

// ========== 标签页管理 ==========
async function createControlledTab(url = 'about:blank') {
  extensionStatus = 'running';
  chrome.runtime.sendMessage({ type: 'statusChanged', status: 'running' }).catch(() => {});
  
  // 确定目标窗口
  let targetWindowId = senderWindowId;
  if (!targetWindowId) {
    try {
      const normalWindows = await chrome.windows.getAll({ windowTypes: ['normal'] });
      if (normalWindows.length > 0) {
        const focusedWindow = normalWindows.find(w => w.focused);
        targetWindowId = focusedWindow ? focusedWindow.id : normalWindows[0].id;
      }
    } catch (e) {}
  }
  
  // 获取固定标签页数量（用于确定插入位置）
  let pinnedCount = 0;
  try {
    const pinnedTabs = await chrome.tabs.query({ pinned: true, windowId: targetWindowId });
    pinnedCount = pinnedTabs.length;
  } catch (e) {}
  
  // 创建标签页（active: false 让用户留在当前页面）
  const createOptions = { url: 'about:blank', active: false };
  if (targetWindowId) createOptions.windowId = targetWindowId;
  
  let tab;
  try {
    tab = await chrome.tabs.create(createOptions);
  } catch (e) {
    tab = await chrome.tabs.create({ url: 'about:blank', active: false });
  }
  
  controlledTabId = tab.id;
  taskTabIds.add(tab.id);
  log(LOG_LEVEL.SUCCESS, `标签页创建: ${tab.id}`);
  
  // 设置遮罩状态
  maskStates.set(controlledTabId, { visible: true, status: 'Agent 控制中...', blocking: false });
  
  // 立即移动标签页到最左边（固定标签页之后）
  try {
    await chrome.tabs.move(controlledTabId, { index: pinnedCount });
  } catch (e) {}
  
  // 设置 autoDiscardable
  try { await chrome.tabs.update(controlledTabId, { autoDiscardable: false }); } catch (e) {}
  
  // 检查是否可以创建分组
  let canCreateGroup = false;
  try {
    const tabWindow = await chrome.windows.get(tab.windowId);
    canCreateGroup = tabWindow.type === 'normal';
  } catch (e) {}
  
  // 创建分组（不需要额外等待，标签页已经在正确位置）
  if (canCreateGroup) {
    try {
      const tabInfo = await chrome.tabs.get(controlledTabId);
      controlledGroupId = await chrome.tabs.group({ 
        tabIds: [controlledTabId],
        createProperties: { windowId: tabInfo.windowId }
      });
      await chrome.tabGroups.update(controlledGroupId, { title: '🤖 Agent 任务', color: 'blue' });
      log(LOG_LEVEL.SUCCESS, `分组创建: ${controlledGroupId}`);
    } catch (e) {
      log(LOG_LEVEL.WARN, '分组创建失败:', e.message);
      controlledGroupId = null;
    }
  }
  
  // 附加 debugger
  await attachDebugger(tab.id);
  
  // 设置固定 viewport（可选，配置为 0 则不设置）
  try {
    const artifactsConfig = CONFIG.ARTIFACTS;
    if (artifactsConfig.VIEWPORT_WIDTH > 0 && artifactsConfig.VIEWPORT_HEIGHT > 0) {
      await chrome.debugger.sendCommand({ tabId: tab.id }, 'Emulation.setDeviceMetricsOverride', {
        width: artifactsConfig.VIEWPORT_WIDTH,
        height: artifactsConfig.VIEWPORT_HEIGHT,
        deviceScaleFactor: 1,
        mobile: false
      });
      log(LOG_LEVEL.DEBUG, `Viewport 已设置: ${artifactsConfig.VIEWPORT_WIDTH}x${artifactsConfig.VIEWPORT_HEIGHT}`);
    }
  } catch (e) {
    log(LOG_LEVEL.WARN, 'Viewport 设置失败:', e.message);
  }
  
  // 导航到目标 URL（如果不是 about:blank）
  if (url && url !== 'about:blank') {
    await chrome.tabs.update(controlledTabId, { url });
    await waitForTabLoad(controlledTabId);
    log(LOG_LEVEL.SUCCESS, `导航完成: ${url}`);
  }
  
  return { success: true, tabId: tab.id, groupId: controlledGroupId, url };
}

// ========== Content Script 通信 ==========
async function sendToContentScript(action, params) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  
  try {
    const response = await chrome.tabs.sendMessage(controlledTabId, { action, params });
    log(LOG_LEVEL.DEBUG, `CS响应 ${action}:`, response?.success);
    return response;
  } catch (error) {
    if (error.message.includes('Receiving end does not exist')) {
      await injectContentScript(controlledTabId);
      await sleep(500);
      return await chrome.tabs.sendMessage(controlledTabId, { action, params });
    }
    throw error;
  }
}

async function injectContentScript(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content-script.js'] });
}

// ========== Debugger ==========
async function attachDebugger(tabId) {
  if (debuggerAttached) {
    try { await chrome.debugger.detach({ tabId: controlledTabId }); } catch (e) {}
  }
  
  try {
    await chrome.debugger.attach({ tabId }, '1.3');
    debuggerAttached = true;
  } catch (e) {
    log(LOG_LEVEL.WARN, 'Debugger 附加失败:', e.message);
  }
}

// ========== 鼠标移动（反风控）==========
async function mouseMove(params) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (!debuggerAttached) await attachDebugger(controlledTabId);
  
  const { fromX = 100, fromY = 100, toX = 200, toY = 200, duration = 1000 } = params;
  
  try {
    // 添加5秒超时保护
    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => reject(new Error('鼠标移动超时(5秒)')), 5000);
    });
    
    const mouseMovePromise = (async () => {
      // 生成贝塞尔曲线轨迹点
      const points = generateBezierPath(fromX, fromY, toX, toY, duration);
      
      log(LOG_LEVEL.DEBUG, `鼠标移动: (${fromX},${fromY}) → (${toX},${toY}), ${points.length}个点, 耗时${duration}ms`);
      
      // 逐点移动鼠标
      for (let i = 0; i < points.length; i++) {
        const { x, y, delay } = points[i];
        
        await chrome.debugger.sendCommand({ tabId: controlledTabId }, 'Input.dispatchMouseEvent', {
          type: 'mouseMoved',
          x: Math.round(x),
          y: Math.round(y)
        });
        
        if (delay > 0) {
          await sleep(delay);
        }
      }
      
      return { success: true, path: points.length, duration };
    })();
    
    // 使用Promise.race实现超时
    return await Promise.race([mouseMovePromise, timeoutPromise]);
    
  } catch (error) {
    if (error.message.includes('Debugger') || error.message.includes('detached')) {
      debuggerAttached = false;
    }
    
    // 如果是超时错误，返回成功但记录警告
    if (error.message.includes('超时')) {
      log(LOG_LEVEL.WARN, '鼠标移动超时，跳过此操作');
      return { success: true, skipped: true, reason: 'timeout' };
    }
    
    throw new Error('鼠标移动失败: ' + error.message);
  }
}

// 生成贝塞尔曲线轨迹
function generateBezierPath(x1, y1, x2, y2, duration) {
  const points = [];
  const steps = 2; // 固定2个点：起点和终点
  
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    
    // 简单线性插值（2个点不需要贝塞尔曲线）
    const x = x1 + (x2 - x1) * t;
    const y = y1 + (y2 - y1) * t;
    
    // 简单延迟：总时间平分
    let delay = i < steps ? Math.floor(duration / steps) : 0;
    
    points.push({ x, y, delay });
  }
  
  return points;
}

// ========== CDP 图片上传（适用于 React 等框架）==========
async function uploadImagesCDP(imageUrls, selector) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (!debuggerAttached) await attachDebugger(controlledTabId);
  
  try {
    // 1. 下载所有图片到临时文件
    const tempFiles = [];
    for (let i = 0; i < imageUrls.length; i++) {
      const imageUrl = imageUrls[i];
      log(LOG_LEVEL.INFO, `下载图片 ${i + 1}/${imageUrls.length}: ${imageUrl}`);
      
      // 通过 content-script 下载图片并获取 base64
      const downloadResult = await sendToContentScript('downloadImageAsBase64', { imageUrl });
      if (!downloadResult.success) {
        return { success: false, error: `图片 ${i + 1} 下载失败: ${downloadResult.error}` };
      }
      
      // 将 base64 转为临时文件路径（通过 CDP）
      tempFiles.push(downloadResult.base64);
      log(LOG_LEVEL.INFO, `图片 ${i + 1} 下载完成: ${downloadResult.fileName}`);
    }
    
    // 2. 获取 input 元素的 nodeId
    const docResult = await chrome.debugger.sendCommand(
      { tabId: controlledTabId },
      'DOM.getDocument',
      { depth: 0 }
    );
    
    const queryResult = await chrome.debugger.sendCommand(
      { tabId: controlledTabId },
      'DOM.querySelector',
      { nodeId: docResult.root.nodeId, selector }
    );
    
    if (!queryResult.nodeId) {
      return { success: false, error: `找不到元素: ${selector}` };
    }
    
    // 3. 使用 DOM.setFileInputFiles 设置文件
    // 注意：CDP 需要真实文件路径，但我们可以用 base64 创建 Blob URL
    // 这里改用另一种方式：通过 Runtime.evaluate 在页面上下文中操作
    
    const script = `
      (async function() {
        const input = document.querySelector('${selector.replace(/'/g, "\\'")}');
        if (!input) return { success: false, error: '找不到 input 元素' };
        
        const base64List = ${JSON.stringify(tempFiles)};
        const files = [];
        
        for (let i = 0; i < base64List.length; i++) {
          const base64 = base64List[i];
          const response = await fetch(base64);
          const blob = await response.blob();
          const file = new File([blob], 'image_' + (i + 1) + '.jpg', { type: blob.type || 'image/jpeg' });
          files.push(file);
        }
        
        const dt = new DataTransfer();
        files.forEach(f => dt.items.add(f));
        
        // 直接设置 files 属性
        Object.defineProperty(input, 'files', {
          value: dt.files,
          writable: true
        });
        
        // 触发原生事件
        input.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
        input.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
        
        // 尝试触发 React 的合成事件
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'files');
        if (nativeInputValueSetter && nativeInputValueSetter.set) {
          nativeInputValueSetter.set.call(input, dt.files);
        }
        
        // 再次触发事件
        const evt = new Event('change', { bubbles: true });
        Object.defineProperty(evt, 'target', { value: input, writable: false });
        input.dispatchEvent(evt);
        
        return { success: true, count: files.length };
      })()
    `;
    
    const evalResult = await chrome.debugger.sendCommand(
      { tabId: controlledTabId },
      'Runtime.evaluate',
      { expression: script, awaitPromise: true, returnByValue: true }
    );
    
    if (evalResult.exceptionDetails) {
      return { success: false, error: evalResult.exceptionDetails.text };
    }
    
    return evalResult.result.value || { success: true, count: imageUrls.length };
    
  } catch (error) {
    log(LOG_LEVEL.ERROR, `uploadImagesCDP 错误: ${error.message}`);
    return { success: false, error: error.message };
  }
}

// ========== CDP 输入 ==========
async function typeWithCDP(params) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  
  const { index, selector, text, pressEnter } = params;
  log(LOG_LEVEL.DEBUG, `输入: ${index !== undefined ? 'index=' + index : selector} "${text}"`);
  
  // 使用 content script 输入（传递完整参数，支持 index）
  const result = await sendToContentScript('type', { index, selector, text, pressEnter: false });
  
  // 需要回车时用 CDP
  if (pressEnter) {
    await sleep(100);
    await pressKeyWithCDP('Enter');
    await sleep(300);
    await waitForTabLoad(controlledTabId);
  }
  
  return result;
}

// 粘贴文本（用于触发 Markdown 检测等场景）
async function pasteText(params) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (!debuggerAttached) await attachDebugger(controlledTabId);
  
  const { selector, text } = params;
  log(LOG_LEVEL.INFO, `pasteText: ${selector}, 内容长度: ${text.length}`);
  
  try {
    // 1. 先聚焦元素
    if (selector) {
      await sendToContentScript('focusElement', { selector });
      await sleep(200);
    }
    
    // 2. 使用 CDP 的 insertText 命令（模拟粘贴效果）
    await chrome.debugger.sendCommand(
      { tabId: controlledTabId },
      'Input.insertText',
      { text }
    );
    
    log(LOG_LEVEL.SUCCESS, 'pasteText 完成');
    return { success: true, length: text.length };
    
  } catch (error) {
    log(LOG_LEVEL.ERROR, `pasteText 错误: ${error.message}`);
    return { success: false, error: error.message };
  }
}

// CDP 按键
async function pressKeyWithCDP(key = 'Enter') {
  if (!controlledTabId) throw new Error('没有受控标签页');
  
  if (!debuggerAttached) await attachDebugger(controlledTabId);
  
  const keyMap = {
    'Enter': { key: 'Enter', code: 'Enter', keyCode: 13 },
    'Tab': { key: 'Tab', code: 'Tab', keyCode: 9 },
    'Escape': { key: 'Escape', code: 'Escape', keyCode: 27 },
    'Backspace': { key: 'Backspace', code: 'Backspace', keyCode: 8 },
    'ArrowUp': { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38 },
    'ArrowDown': { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40 },
    'ArrowLeft': { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37 },
    'ArrowRight': { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
  };
  
  const keyInfo = keyMap[key] || { key, code: key, keyCode: 0 };
  
  try {
    await chrome.debugger.sendCommand({ tabId: controlledTabId }, 'Input.dispatchKeyEvent', 
      { type: 'keyDown', key: keyInfo.key, code: keyInfo.code, windowsVirtualKeyCode: keyInfo.keyCode, nativeVirtualKeyCode: keyInfo.keyCode });
    
    if (key === 'Enter') {
      await chrome.debugger.sendCommand({ tabId: controlledTabId }, 'Input.dispatchKeyEvent', 
        { type: 'char', text: '\r', key: keyInfo.key, code: keyInfo.code });
    }
    
    await chrome.debugger.sendCommand({ tabId: controlledTabId }, 'Input.dispatchKeyEvent', 
      { type: 'keyUp', key: keyInfo.key, code: keyInfo.code, windowsVirtualKeyCode: keyInfo.keyCode, nativeVirtualKeyCode: keyInfo.keyCode });
    
    log(LOG_LEVEL.DEBUG, `CDP按键: ${key}`);
    return { success: true, key };
  } catch (error) {
    if (error.message.includes('Debugger') || error.message.includes('detached')) {
      debuggerAttached = false;
    }
    throw new Error('CDP 按键失败: ' + error.message);
  }
}

// ========== 截图 ==========
async function captureScreenshot() {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (!debuggerAttached) await attachDebugger(controlledTabId);
  
  try {
    const quality = CONFIG.ARTIFACTS.SCREENSHOT_QUALITY || 70;
    const result = await chrome.debugger.sendCommand({ tabId: controlledTabId }, 'Page.captureScreenshot', { format: 'jpeg', quality });
    return { success: true, screenshot: 'data:image/jpeg;base64,' + result.data };
  } catch (error) {
    throw new Error('截图失败: ' + error.message);
  }
}

async function captureAndUploadScreenshot() {
  const result = await captureScreenshot();
  const url = await uploadScreenshot(result.screenshot, `screenshot_${Date.now()}.jpg`);
  return { success: true, screenshot: url };
}

// ========== 带标记截图 ==========
async function loadImage(dataUrl) {
  const response = await fetch(dataUrl);
  const blob = await response.blob();
  return await createImageBitmap(blob);
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function getColorByTag(tagName) {
  const colors = { button: '#FF6B6B', input: '#4ECDC4', a: '#96CEB4', select: '#45B7D1', textarea: '#FF8C42', img: '#DDA0DD' };
  return colors[tagName] || '#00d4ff';
}

async function drawElementMarks(screenshotDataUrl, elements) {
  try {
    const img = await loadImage(screenshotDataUrl);
    const canvas = new OffscreenCanvas(img.width, img.height);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    
    for (const el of elements) {
      const { index, rect, tagName } = el;
      const color = getColorByTag(tagName);
      
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
      
      const labelWidth = index >= 10 ? 28 : 22;
      ctx.fillStyle = color;
      ctx.setLineDash([]);
      ctx.fillRect(rect.x, rect.y - 18, labelWidth, 18);
      
      ctx.fillStyle = 'white';
      ctx.font = 'bold 12px sans-serif';
      ctx.fillText(String(index), rect.x + 4, rect.y - 5);
    }
    
    const blob = await canvas.convertToBlob({ type: 'image/png' });
    return await blobToDataUrl(blob);
  } catch (error) {
    log(LOG_LEVEL.WARN, '绘制标记失败:', error.message);
    return screenshotDataUrl;
  }
}

// ========== 截图上传 ==========
async function uploadScreenshot(base64Data, fileName) {
  if (!sessionConfig.apiBase || !sessionConfig.xToken) return '';

  try {
    const mimeMatch = base64Data.match(/^data:(image\/\w+);base64,/);
    const mimeType = mimeMatch ? mimeMatch[1] : 'image/jpeg';
    const base64 = base64Data.replace(/^data:image\/\w+;base64,/, '');
    const binary = atob(base64);
    const array = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) array[i] = binary.charCodeAt(i);
    const blob = new Blob([array], { type: mimeType });

    const formData = new FormData();
    formData.append('file', blob, fileName);
    formData.append('scene', 'content');

    const response = await fetch(`${sessionConfig.apiBase}/member/upload/file`, {
      method: 'POST',
      headers: { 'x-token': sessionConfig.xToken },
      body: formData
    });

    const result = await response.json();
    if (result.code === 0 && result.data?.url) {
      log(LOG_LEVEL.DEBUG, `截图上传: ${Math.round(blob.size / 1024)}KB`);
      return result.data.url;
    }
    return '';
  } catch (error) {
    log(LOG_LEVEL.WARN, '截图上传失败:', error.message);
    return '';
  }
}

// ========== Artifacts 收集 ==========
async function collectArtifactsInternal() {
  return await collectArtifacts();
}

async function collectArtifacts(params = {}) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  
  // 允许调用方强制指定 optimize 参数
  const forceOptimize = params.optimize;
  
  const artifactsConfig = CONFIG.ARTIFACTS;
  const smartRulesConfig = CONFIG.SMART_RULES;
  
  // 检测提示（验证码、登录弹窗等）
  let hints = {};
  
  // ========== 页面预处理 ==========
  if (smartRulesConfig && smartRulesConfig.ENABLED) {
    try {
      const smartResult = await sendToContentScript('runSmartRules', smartRulesConfig);
      
      // 如果是错误页，直接返回失败
      if (smartResult.isErrorPage) {
        log(LOG_LEVEL.WARN, `页面预处理: 检测到错误页 (${smartResult.errorType})`);
        return {
          success: false,
          error: '页面错误',
          errorType: smartResult.errorType,
          isErrorPage: true,
          screenshots: { clean: '', marked: '' },
          elements: [],
          pageInfo: { url: '', title: '错误页' }
        };
      }
      
      // 记录自动处理的操作
      if (smartResult.actions && smartResult.actions.length > 0) {
        log(LOG_LEVEL.SUCCESS, `页面预处理: ${smartResult.actions.map(a => a.type).join(', ')}`);
      }
      
      // 保存检测提示
      if (smartResult.hints) {
        hints = smartResult.hints;
        if (hints.hasCaptcha) {
          log(LOG_LEVEL.WARN, `检测到验证码: ${hints.captchaType}`);
        }
        if (hints.hasLoginPopup) {
          log(LOG_LEVEL.WARN, `检测到登录弹窗, 可关闭: ${hints.loginCanClose}`);
        }
      }
    } catch (e) {
      log(LOG_LEVEL.DEBUG, '页面预处理跳过:', e.message);
    }
  }
  
  // 调试：打印配置
  log(LOG_LEVEL.DEBUG, 'artifactsConfig:', JSON.stringify(artifactsConfig));
  
  // 标记元素（传递优化配置）
  // 如果调用方指定了 optimize，使用调用方的值；否则用配置
  const useOptimize = forceOptimize !== undefined ? forceOptimize : (artifactsConfig.OPTIMIZE_ELEMENTS || false);
  
  const markResult = await sendToContentScript('markElements', { 
    maxElements: artifactsConfig.MAX_ELEMENTS,
    optimize: useOptimize,
    textMaxLength: artifactsConfig.TEXT_MAX_LENGTH || 25
  });
  const elements = markResult.elements || [];
  
  // 页面信息
  const pageInfo = await sendToContentScript('getPageInfo', {});
  
  // 截图处理
  let cleanUrl = '';
  let markedUrl = '';
  const timestamp = Date.now();
  
  if (artifactsConfig.ENABLE_CLEAN_SCREENSHOT || artifactsConfig.ENABLE_MARKED_SCREENSHOT) {
    const screenshotResult = await captureScreenshot();
    const cleanBase64 = screenshotResult.screenshot;
    
    // 上传干净截图
    if (artifactsConfig.ENABLE_CLEAN_SCREENSHOT) {
      cleanUrl = await uploadScreenshot(cleanBase64, `screenshot_clean_${timestamp}.jpg`);
    }
    
    // 画框并上传标记截图（仅当开启时）
    if (artifactsConfig.ENABLE_MARKED_SCREENSHOT) {
      const markedBase64 = await drawElementMarks(cleanBase64, elements);
      markedUrl = await uploadScreenshot(markedBase64, `screenshot_marked_${timestamp}.jpg`);
    }
  }
  
  log(LOG_LEVEL.SUCCESS, `Artifacts: ${elements.length}元素, optimize:${artifactsConfig.OPTIMIZE_ELEMENTS || false}`);
  
  // 构建返回结果
  const result = {
    success: true,
    screenshots: { clean: cleanUrl, marked: markedUrl },
    elements,
    pageInfo
  };
  
  // 如果有检测提示，加到结果里
  if (Object.keys(hints).length > 0) {
    result.hints = hints;
  }
  
  return result;
}

// ========== 导航操作 ==========
async function navigateTo(url) {
  if (!controlledTabId) return await createControlledTab(url);
  await chrome.tabs.update(controlledTabId, { url });
  await waitForTabLoad(controlledTabId);
  return { success: true, url };
}

async function getTabInfo() {
  if (!controlledTabId) return { success: false, error: '没有受控标签页' };
  try {
    const tab = await chrome.tabs.get(controlledTabId);
    return { success: true, tabId: tab.id, url: tab.url, title: tab.title };
  } catch {
    controlledTabId = null;
    debuggerAttached = false;
    return { success: false, error: '标签页已关闭' };
  }
}

async function goBack() {
  if (!controlledTabId) throw new Error('没有受控标签页');
  await chrome.tabs.goBack(controlledTabId);
  return { success: true };
}

async function goForward() {
  if (!controlledTabId) throw new Error('没有受控标签页');
  await chrome.tabs.goForward(controlledTabId);
  return { success: true };
}

async function refreshTab() {
  if (!controlledTabId) throw new Error('没有受控标签页');
  await chrome.tabs.reload(controlledTabId);
  await waitForTabLoad(controlledTabId);
  return { success: true };
}

async function closeTab() {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (debuggerAttached) try { await chrome.debugger.detach({ tabId: controlledTabId }); } catch (e) {}
  await chrome.tabs.remove(controlledTabId);
  controlledTabId = null;
  controlledGroupId = null;
  debuggerAttached = false;
  extensionStatus = 'idle';
  currentSessionId = null;
  chrome.runtime.sendMessage({ type: 'statusChanged', status: 'idle' }).catch(() => {});
  return { success: true };
}

async function createTabGroup(title) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (controlledGroupId) {
    await chrome.tabGroups.update(controlledGroupId, { title, color: 'blue' });
    return { success: true, groupId: controlledGroupId, title };
  }
  controlledGroupId = await chrome.tabs.group({ tabIds: [controlledTabId] });
  await chrome.tabGroups.update(controlledGroupId, { title, color: 'blue' });
  return { success: true, groupId: controlledGroupId, title };
}

// ========== 工具函数 ==========
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// 等待多个条件中的任意一个满足
async function waitForAny(conditions, timeout = 10000) {
  if (!controlledTabId) throw new Error('没有受控标签页');
  if (!conditions || conditions.length === 0) {
    return { success: false, error: '没有提供条件' };
  }
  
  const startTime = Date.now();
  
  let checkCount = 0;
  while (Date.now() - startTime < timeout) {
    checkCount++;
    for (const cond of conditions) {
      try {
        const result = await sendToContentScript('assertSelector', { selector: cond.selector });
        log(LOG_LEVEL.INFO, `waitForAny 检测 [${checkCount}]: ${cond.selector} → exists=${result.exists}, visible=${result.visible}`);
        if (result.exists && result.visible !== false) {
          log(LOG_LEVEL.SUCCESS, `waitForAny 匹配成功: ${cond.selector} → goto=${cond.goto}`);
          return { 
            success: true, 
            matched: cond.selector, 
            goto: cond.goto,
            result 
          };
        }
      } catch (e) {
        log(LOG_LEVEL.WARN, `waitForAny 检测异常: ${cond.selector} → ${e.message}`);
      }
    }
    await sleep(200);
  }
  
  return { 
    success: false, 
    error: '等待超时，没有条件满足', 
    timeout,
    conditions: conditions.map(c => c.selector)
  };
}

function waitForTabLoad(tabId, timeout = 10000) {
  return new Promise((resolve) => {
    const startTime = Date.now();
    
    const checkReady = async () => {
      // 检查是否超时
      if (Date.now() - startTime > timeout) {
        log(LOG_LEVEL.WARN, '页面加载超时');
        resolve();
        return;
      }
      
      try {
        // 先检查 tab 状态
        const tab = await chrome.tabs.get(tabId);
        if (tab.status !== 'complete') {
          setTimeout(checkReady, 200);
          return;
        }
        
        // 向 content script 发送就绪检查
        try {
          const response = await chrome.tabs.sendMessage(tabId, { action: 'checkReady' });
          if (response?.ready && response?.sinceLoadMs > 1000) {
            // 页面已加载超过 1 秒，认为稳定
            resolve();
            return;
          }
        } catch (e) {
          // content script 可能还没注入，继续等待
        }
        
        setTimeout(checkReady, 300);
      } catch (e) {
        resolve();
      }
    };
    
    // 先等 500ms 让跳转开始
    setTimeout(checkReady, 500);
  });
}

// ========== 结果上报 ==========
async function postResultToBackend(action, result) {
  if (!sessionConfig.apiBase || !sessionConfig.session_id || !sessionConfig.interrupt_id) return;

  const payload = {
    session_id: sessionConfig.session_id,
    execution_id: sessionConfig.execution_id,
    interrupt_id: sessionConfig.interrupt_id,
    data: result,
    xToken: sessionConfig.xToken
  };

  try {
    const response = await fetch(`${sessionConfig.apiBase}/agent_adk/chat/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-token': sessionConfig.xToken },
      credentials: 'include',
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    if (data.code === 0) {
      log(LOG_LEVEL.SUCCESS, `上报成功: ${action}`);
    } else {
      log(LOG_LEVEL.ERROR, `上报失败: ${data.msg}`);
    }
  } catch (error) {
    log(LOG_LEVEL.ERROR, `上报异常: ${error.message}`);
  }
}
// ========== 事件监听 ==========
chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId === controlledTabId) {
    debuggerAttached = false;
    log(LOG_LEVEL.DEBUG, 'Debugger 断开:', reason);
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (tabId !== controlledTabId) return;
  
  if (changeInfo.status === 'complete') {
    const state = maskStates.get(tabId);
    const url = tab?.url || '';
    
    // 跳过特殊页面
    if (url === 'about:blank' || url.startsWith('chrome://') || url.startsWith('chrome-extension://')) return;
    
    if (state && state.visible) {
      await sleep(500);  // 800ms → 500ms
      try { await injectContentScript(tabId); } catch (e) {}
      await sleep(200);  // 300ms → 200ms
      try {
        await chrome.tabs.sendMessage(tabId, { action: 'showMask', params: { status: state.status, blocking: state.blocking } });
      } catch (e) {}
    }
  }
});

// 标记：是否正在执行操作
let isExecutingAction = false;

// 待切换的新标签页
let pendingNewTab = null;

// 监听新标签页创建（检测网站打开的新标签页）
chrome.tabs.onCreated.addListener(async (tab) => {
  if (!controlledTabId || extensionStatus !== 'running') return;
  
  // 关键：先检查 groupId，如果在任务分组里，立即加入 taskTabIds
  // 必须在任何 await 之前执行，否则 onActivated 可能先触发
  if (controlledGroupId && tab.groupId === controlledGroupId) {
    taskTabIds.add(tab.id);
    log(LOG_LEVEL.DEBUG, `检测到任务分组内新标签页: ${tab.id}`);
  }
  
  // 检查新标签页是否在同一个窗口
  try {
    const controlledTab = await chrome.tabs.get(controlledTabId);
    if (tab.windowId !== controlledTab.windowId) {
      // 不在同一窗口，移除之前可能加入的记录
      taskTabIds.delete(tab.id);
      return;
    }
    
    // 判断是否是任务相关的新标签页
    const isFromTaskTab = tab.openerTabId && taskTabIds.has(tab.openerTabId);
    const isInTaskGroup = controlledGroupId && tab.groupId === controlledGroupId;
    
    log(LOG_LEVEL.DEBUG, `新标签页详情: ${tab.id}, opener: ${tab.openerTabId}, groupId: ${tab.groupId}, isFromTaskTab: ${isFromTaskTab}, isInTaskGroup: ${isInTaskGroup}`);
    
    // 只处理任务相关的新标签页
    if (!isFromTaskTab && !isInTaskGroup) {
      taskTabIds.delete(tab.id);  // 确保移除
      log(LOG_LEVEL.DEBUG, '新标签页不是任务相关的，忽略');
      return;
    }
    
    // 确保加入 taskTabIds
    taskTabIds.add(tab.id);
    
    // 如果正在执行操作，先记录下来，等操作完成后再切换
    if (isExecutingAction) {
      log(LOG_LEVEL.DEBUG, '操作进行中，记录新标签页待切换');
      pendingNewTab = tab;
    } else {
      await switchToNewTab(tab);
    }
  } catch (e) {
    log(LOG_LEVEL.DEBUG, '处理新标签页失败:', e.message);
  }
});

// 切换到新标签页
async function switchToNewTab(tab) {
  log(LOG_LEVEL.SUCCESS, `切换到新标签页: ${tab.id}`);
  
  const oldTabId = controlledTabId;
  controlledTabId = tab.id;
  taskTabIds.add(tab.id);
  
  // 转移遮罩状态
  const oldMaskState = maskStates.get(oldTabId);
  if (oldMaskState) {
    maskStates.set(tab.id, { ...oldMaskState });
  }
  
  // 加入分组
  if (controlledGroupId) {
    try {
      await chrome.tabs.group({ tabIds: [tab.id], groupId: controlledGroupId });
      log(LOG_LEVEL.DEBUG, '新标签页已加入分组');
    } catch (e) {}
  }
  
  // 重新附加 debugger
  debuggerAttached = false;
  await attachDebugger(tab.id);
  await waitForTabLoad(controlledTabId);
  
  // 向新标签页发送显示遮罩的消息
  if (oldMaskState && oldMaskState.visible) {
    try {
      await injectContentScript(tab.id);
      await sleep(200);  // 300ms → 200ms
      await chrome.tabs.sendMessage(tab.id, { 
        action: 'showMask', 
        params: { status: oldMaskState.status, blocking: oldMaskState.blocking } 
      });
      log(LOG_LEVEL.DEBUG, '新标签页遮罩已显示');
    } catch (e) {
      log(LOG_LEVEL.DEBUG, '新标签页遮罩显示失败:', e.message);
    }
  }
  
  // 关闭旧标签页（如果开启了开关，且不是最后一个任务标签页）
  if (AUTO_CLOSE_OLD_TAB && oldTabId && taskTabIds.size > 1) {
    try {
      await chrome.tabs.remove(oldTabId);
      taskTabIds.delete(oldTabId);
      maskStates.delete(oldTabId);
      log(LOG_LEVEL.DEBUG, `已关闭旧标签页: ${oldTabId}`);
    } catch (e) {
      log(LOG_LEVEL.DEBUG, '关闭旧标签页失败:', e.message);
    }
  }
  
  log(LOG_LEVEL.SUCCESS, `新标签页已就绪: ${tab.id}`);
  
  // 切回用户原来的标签页
  if (AUTO_SWITCH_BACK && userActiveTabId && userActiveTabId !== tab.id) {
    try {
      const userTab = await chrome.tabs.get(userActiveTabId);
      if (userTab) {
        await chrome.tabs.update(userActiveTabId, { active: true });
        log(LOG_LEVEL.DEBUG, `已切回用户标签页: ${userActiveTabId}`);
      }
    } catch (e) {
      log(LOG_LEVEL.DEBUG, '切回用户标签页失败:', e.message);
    }
  }
}

// 监听标签页关闭
chrome.tabs.onRemoved.addListener(async (tabId, removeInfo) => {
  if (tabId !== controlledTabId) return;
  
  log(LOG_LEVEL.WARN, `受控标签页被关闭: ${tabId}`);
  controlledTabId = null;
  controlledGroupId = null;
  debuggerAttached = false;
});

// 监听标签页激活（防止任务标签页抢焦点）
chrome.tabs.onActivated.addListener(async (activeInfo) => {
  if (!AUTO_SWITCH_BACK) return;
  if (extensionStatus !== 'running' || !controlledTabId) return;
  
  const { tabId, windowId } = activeInfo;
  
  log(LOG_LEVEL.DEBUG, `onActivated: tabId=${tabId}, userActiveTabId=${userActiveTabId}, taskTabIds=[${[...taskTabIds].join(',')}]`);
  
  // 检查是否是任务相关的标签页被激活
  let isTaskTab = taskTabIds.has(tabId);
  
  // 也检查是否在任务分组中
  if (!isTaskTab && controlledGroupId) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.groupId === controlledGroupId) {
        isTaskTab = true;
        taskTabIds.add(tabId);
        log(LOG_LEVEL.DEBUG, `通过分组识别任务标签页: ${tabId}`);
      }
    } catch (e) {}
  }
  
  log(LOG_LEVEL.DEBUG, `判断结果: isTaskTab=${isTaskTab}, userActiveTabId=${userActiveTabId}`);
  
  // 如果是任务标签页被激活，且我们记录了用户原来的标签页，切回去
  if (isTaskTab && userActiveTabId && userActiveTabId !== tabId) {
    try {
      const userTab = await chrome.tabs.get(userActiveTabId);
      if (userTab && userTab.windowId === windowId) {
        log(LOG_LEVEL.DEBUG, `任务标签页抢焦点，切回用户标签页: ${userActiveTabId}`);
        await chrome.tabs.update(userActiveTabId, { active: true });
      }
    } catch (e) {
      userActiveTabId = null;
    }
  } 
  // 如果激活的不是任务标签页，记录为用户当前标签页
  else if (!isTaskTab) {
    userActiveTabId = tabId;
    log(LOG_LEVEL.DEBUG, `更新用户标签页: ${tabId}`);
  }
});

// ========== 脚本引擎 ==========
async function runScript(script, data = {}) {
  log(LOG_LEVEL.ACTION, `运行脚本: ${script.name || script.id}`);
  
  // 加载脚本
  scriptEngine.load(script, data);
  
  // 设置进度回调（通过消息通知前端）
  scriptEngine.onProgress = (progress) => {
    chrome.runtime.sendMessage({ 
      type: 'scriptProgress', 
      ...progress 
    }).catch(() => {});
  };
  
  // 执行脚本
  const result = await scriptEngine.run();
  
  log(result.success ? LOG_LEVEL.SUCCESS : LOG_LEVEL.ERROR, 
    `脚本${result.success ? '完成' : '失败'}: ${result.error || result.status}`);
  
  return {
    success: result.success,
    status: result.status,
    error: result.error,
    logs: scriptEngine.logs
  };
}

// ========== WebSocket 任务通信 ==========
let taskWs = null;
let currentTask = null;

// 连接任务 WebSocket
async function connectTaskWs(wsUrl) {
  // 关闭已有连接
  if (taskWs) {
    taskWs.close();
    taskWs = null;
  }
  
  return new Promise((resolve, reject) => {
    log(LOG_LEVEL.INFO, `连接任务 WS: ${wsUrl}`);
    
    try {
      taskWs = new WebSocket(wsUrl);
    } catch (e) {
      reject(new Error(`WebSocket 创建失败: ${e.message}`));
      return;
    }
    
    const timeout = setTimeout(() => {
      reject(new Error('WebSocket 连接超时'));
      taskWs?.close();
    }, 10000);
    
    taskWs.onopen = () => {
      clearTimeout(timeout);
      log(LOG_LEVEL.SUCCESS, 'WS 连接成功');
      
      // 发送 connected 消息
      const connectMsg = {
        plugin_version: chrome.runtime.getManifest().version
      };
      log(LOG_LEVEL.INFO, 'WS 发送 connected 消息:', JSON.stringify(connectMsg));
      sendWsMessage('connected', connectMsg);
      
      resolve({ success: true });
    };
    
    taskWs.onmessage = async (event) => {
      try {
        log(LOG_LEVEL.INFO, `WS 原始消息: ${event.data.substring(0, 500)}`);
        const msg = JSON.parse(event.data);
        log(LOG_LEVEL.DEBUG, `WS 收到: ${msg.type}`, msg.data);
        await handleWsMessage(msg);
      } catch (e) {
        log(LOG_LEVEL.ERROR, 'WS 消息解析失败:', e.message, event.data?.substring(0, 200));
      }
    };
    
    taskWs.onerror = (error) => {
      clearTimeout(timeout);
      log(LOG_LEVEL.ERROR, 'WS 错误:', error);
      reject(new Error('WebSocket 连接错误'));
    };
    
    taskWs.onclose = (event) => {
      log(LOG_LEVEL.INFO, `WS 关闭: code=${event.code}, reason=${event.reason}, wasClean=${event.wasClean}`);
      taskWs = null;
      currentTask = null;
    };
  });
}

// 发送 WS 消息
function sendWsMessage(type, data) {
  if (!taskWs || taskWs.readyState !== WebSocket.OPEN) {
    log(LOG_LEVEL.WARN, 'WS 未连接，无法发送消息');
    return false;
  }
  
  const msg = { type, data };
  taskWs.send(JSON.stringify(msg));
  log(LOG_LEVEL.DEBUG, `WS 发送: ${type}`, data);
  return true;
}

// 处理 WS 消息
async function handleWsMessage(msg) {
  const { type, data } = msg;
  log(LOG_LEVEL.INFO, `WS 收到消息: ${type}`);
  log(LOG_LEVEL.INFO, `WS 完整数据: ${JSON.stringify(data, null, 2)}`);
  
  switch (type) {
    case 'task_info':
      // 收到任务信息，开始执行
      log(LOG_LEVEL.INFO, `task_info 详情: task_id=${data.task_id}, script=${data.script?.name}, material keys=${Object.keys(data.material || {})}`);
      log(LOG_LEVEL.INFO, `script 内容: ${JSON.stringify(data.script, null, 2)}`);
      log(LOG_LEVEL.INFO, `material 内容: ${JSON.stringify(data.material, null, 2)}`);
      currentTask = {
        taskId: data.task_id,
        script: data.script,
        data: data.material,
        status: 'running'
      };
      log(LOG_LEVEL.ACTION, `开始任务: ${data.task_id}`);
      
      // 异步执行脚本
      executeTaskScript(data.script, data.material);
      break;
      
    case 'cancel':
      // 取消任务
      log(LOG_LEVEL.WARN, `任务取消: ${data.reason}`);
      if (currentTask) {
        scriptEngine.stop();
        const stoppedAt = scriptEngine.script?.steps?.[scriptEngine.currentStepIndex]?.id || 'unknown';
        sendWsMessage('cancelled', { stopped_at: stoppedAt });
        currentTask = null;
      }
      break;
      
    default:
      log(LOG_LEVEL.WARN, `未知 WS 消息类型: ${type}`);
  }
}

// 执行任务脚本
async function executeTaskScript(script, data) {
  log(LOG_LEVEL.INFO, `开始执行脚本: ${script?.name}, 步骤数: ${script?.steps?.length}, 数据: ${JSON.stringify(data).slice(0, 100)}`);
  
  try {
    // 加载脚本（不再替换，由 JSON 脚本内部的 if 判断处理）
    scriptEngine.load(script, data);
    log(LOG_LEVEL.INFO, '脚本已加载到引擎');
    
    // 设置进度回调 - 通过 WS 上报
    scriptEngine.onProgress = (progress) => {
      log(LOG_LEVEL.INFO, `步骤进度: ${progress.stepId} (${progress.stepIndex}/${progress.totalSteps}) - ${progress.status}`);
      const sent = sendWsMessage('step_progress', {
        step_id: progress.stepId,
        step_index: progress.stepIndex,
        total_steps: progress.totalSteps,
        status: progress.status,
        message: `执行: ${progress.action}`,
        timestamp: Date.now()
      });
      log(LOG_LEVEL.DEBUG, `step_progress 发送${sent ? '成功' : '失败'}`);
    };
    
    // 执行脚本
    log(LOG_LEVEL.INFO, '开始执行 scriptEngine.run()');
    const result = await scriptEngine.run();
    log(LOG_LEVEL.INFO, `脚本执行结果: success=${result.success}, status=${result.status}, error=${result.error}`);
    
    if (result.success) {
      sendWsMessage('task_complete', {
        success: true,
        message: '任务完成'
      });
      log(LOG_LEVEL.SUCCESS, '任务完成，已发送 task_complete');
      // 任务成功，隐藏遮罩释放控制
      if (controlledTabId) {
        maskStates.set(controlledTabId, { visible: false, status: '', blocking: false });
        try { await chrome.tabs.sendMessage(controlledTabId, { action: 'hideMask', params: {} }); } catch (e) {}
      }
    } else {
      const failedStep = scriptEngine.script?.steps?.[scriptEngine.currentStepIndex]?.id || 'unknown';
      // 更新遮罩状态显示失败信息
      if (controlledTabId) {
        try {
          await chrome.tabs.sendMessage(controlledTabId, { action: 'updateStatus', params: { status: `❌ 任务失败：${result.error}` } });
        } catch (e) {}
      }
      sendWsMessage('task_failed', {
        failed_step: failedStep,
        error: result.error || '执行失败'
      });
      log(LOG_LEVEL.ERROR, `任务失败，已发送 task_failed: ${failedStep} - ${result.error}`);
      // 任务失败，隐藏遮罩释放控制
      if (controlledTabId) {
        maskStates.set(controlledTabId, { visible: false, status: '', blocking: false });
        try { await chrome.tabs.sendMessage(controlledTabId, { action: 'hideMask', params: {} }); } catch (e) {}
      }
    }
    
    currentTask = null;
    
  } catch (e) {
    log(LOG_LEVEL.ERROR, `脚本执行异常: ${e.message}`, e.stack);
    sendWsMessage('task_failed', {
      failed_step: 'unknown',
      error: e.message
    });
    currentTask = null;
  }
}

// 断开任务 WS
function disconnectTaskWs() {
  if (taskWs) {
    taskWs.close();
    taskWs = null;
  }
  currentTask = null;
  return { success: true };
}

// 获取任务状态
function getTaskStatus() {
  return {
    connected: taskWs && taskWs.readyState === WebSocket.OPEN,
    task: currentTask ? {
      taskId: currentTask.taskId,
      status: currentTask.status
    } : null
  };
}

// ========== 启动 ==========
// 初始化：加载远程配置
fetchDynamicConfig().then(() => {
  log(LOG_LEVEL.SUCCESS, `Jike Browser Agent V${chrome.runtime.getManifest().version} 已启动`);
});
