// ========== 脚本引擎 ==========
// 解析执行 JSON 脚本，实现固定流程自动化

class ScriptEngine {
  constructor() {
    this.script = null;
    this.data = {};
    this.variables = {};
    this.currentStepIndex = 0;
    this.status = 'idle'; // idle | running | paused | completed | failed
    this.onProgress = null; // 进度回调
    this.onComplete = null; // 完成回调
    this.logs = [];
  }

  // 加载脚本
  load(script, data = {}) {
    this.script = script;
    this.data = data;
    this.variables = { ...data };
    this.currentStepIndex = 0;
    this.status = 'idle';
    this.logs = [];
    this.log('info', `脚本加载: ${script.name || script.id}`);
  }

  // 执行脚本
  async run() {
    if (!this.script || !this.script.steps) {
      throw new Error('没有加载脚本');
    }

    this.status = 'running';
    this.log('info', '开始执行脚本');

    try {
      while (this.currentStepIndex < this.script.steps.length) {
        if (this.status === 'paused') {
          this.log('info', '脚本已暂停');
          return { success: true, status: 'paused', stepIndex: this.currentStepIndex };
        }

        const step = this.script.steps[this.currentStepIndex];
        const result = await this.executeStep(step);

        // 处理 goto 跳转
        if (result.goto) {
          const targetIndex = this.findStepIndex(result.goto);
          if (targetIndex >= 0) {
            this.log('info', `跳转到: ${result.goto}`);
            this.currentStepIndex = targetIndex;
            continue;
          } else {
            this.log('warn', `跳转目标不存在: ${result.goto}`);
          }
        }

        // 步骤失败处理
        if (!result.success) {
          const errorHandled = await this.handleError(step, result);
          if (!errorHandled) {
            this.status = 'failed';
            this.log('error', `脚本执行失败: ${result.error}`);
            return { success: false, error: result.error, stepIndex: this.currentStepIndex };
          }
        }

        this.currentStepIndex++;
      }

      this.status = 'completed';
      this.log('info', '脚本执行完成');
      return { success: true, status: 'completed' };

    } catch (e) {
      this.status = 'failed';
      this.log('error', `脚本异常: ${e.message}`);
      return { success: false, error: e.message };
    }
  }

  // 执行单个步骤
  async executeStep(step) {
    const stepId = step.id || `step_${this.currentStepIndex}`;
    this.log('info', `执行步骤: ${stepId} (${step.action})`);

    // 通知进度
    if (this.onProgress) {
      this.onProgress({
        stepIndex: this.currentStepIndex,
        totalSteps: this.script.steps.length,
        stepId,
        action: step.action,
        status: 'running'
      });
    }

    try {
      // 替换参数中的变量
      const params = this.replaceVariables(step.params || {});

      // 根据 action 类型执行
      let result;
      switch (step.action) {
        case 'waitForAny':
          result = await this.executeWaitForAny(step);
          break;
        case 'pauseForUser':
          result = await this.executePauseForUser(step);
          break;
        case 'setVariable':
          result = this.executeSetVariable(params);
          break;
        case 'loop':
          result = await this.executeLoop(step);
          break;
        default:
          // 调用 background.js 的标准操作
          result = await this.callAction(step.action, params);
      }

      // 保存返回值到变量
      if (step.saveAs && result.success) {
        this.variables[step.saveAs] = result;
      }

      // 通知进度
      if (this.onProgress) {
        this.onProgress({
          stepIndex: this.currentStepIndex,
          totalSteps: this.script.steps.length,
          stepId,
          action: step.action,
          status: result.success ? 'completed' : 'failed',
          result
        });
      }

      return result;

    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  // 调用 background.js 的操作
  async callAction(action, params) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ action, params }, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ success: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response || { success: false, error: '无响应' });
        }
      });
    });
  }

  // waitForAny 特殊处理（带 goto）
  async executeWaitForAny(step) {
    const result = await this.callAction('waitForAny', {
      conditions: step.conditions,
      timeout: step.timeout || 10000
    });

    if (result.success && result.goto) {
      return { success: true, goto: result.goto };
    }

    if (!result.success && step.onTimeout === 'fail') {
      return { success: false, error: '等待超时' };
    }

    return result;
  }

  // pauseForUser - 暂停等待用户操作
  async executePauseForUser(step) {
    this.log('info', `等待用户: ${step.message}`);
    this.status = 'paused';

    // 通知前端需要用户操作
    if (this.onProgress) {
      this.onProgress({
        stepIndex: this.currentStepIndex,
        totalSteps: this.script.steps.length,
        stepId: step.id,
        action: 'pauseForUser',
        status: 'waiting',
        message: step.message,
        waitFor: step.waitFor
      });
    }

    // 如果有 waitFor，轮询等待元素出现
    if (step.waitFor) {
      const timeout = step.timeout || 300000; // 默认 5 分钟
      const result = await this.callAction('waitForSelector', {
        selector: step.waitFor,
        timeout
      });

      if (result.success) {
        this.status = 'running';
        return { success: true };
      } else {
        return { success: false, error: '等待用户操作超时' };
      }
    }

    // 没有 waitFor，需要手动 resume
    return { success: true, paused: true };
  }

  // 设置变量
  executeSetVariable(params) {
    if (params.name && params.value !== undefined) {
      this.variables[params.name] = params.value;
      return { success: true };
    }
    return { success: false, error: '缺少 name 或 value' };
  }

  // 循环执行
  async executeLoop(step) {
    const items = this.getVariable(step.items);
    if (!Array.isArray(items)) {
      return { success: false, error: `${step.items} 不是数组` };
    }

    for (let i = 0; i < items.length; i++) {
      this.variables[step.itemVar || 'item'] = items[i];
      this.variables[step.indexVar || 'index'] = i;

      for (const subStep of step.steps) {
        const result = await this.executeStep(subStep);
        if (!result.success) {
          return result;
        }
      }
    }

    return { success: true };
  }

  // 错误处理
  async handleError(step, result) {
    const onError = step.onError || this.script.onError || { type: 'fail' };

    switch (onError.type) {
      case 'retry':
        const maxRetries = onError.maxRetries || 3;
        for (let i = 0; i < maxRetries; i++) {
          this.log('warn', `重试 ${i + 1}/${maxRetries}`);
          await this.sleep(onError.delay || 1000);
          const retryResult = await this.executeStep(step);
          if (retryResult.success) return true;
        }
        return false;

      case 'skip':
        this.log('warn', `跳过失败步骤: ${step.id}`);
        return true;

      case 'goto':
        if (onError.target) {
          const targetIndex = this.findStepIndex(onError.target);
          if (targetIndex >= 0) {
            this.currentStepIndex = targetIndex - 1; // -1 因为循环会 +1
            return true;
          }
        }
        return false;

      case 'fail':
      default:
        return false;
    }
  }

  // 变量替换 {{data.xxx}} 或 {{xxx}}
  replaceVariables(obj) {
    if (typeof obj === 'string') {
      return obj.replace(/\{\{([^}]+)\}\}/g, (match, path) => {
        const value = this.getVariable(path.trim());
        return value !== undefined ? value : match;
      });
    }

    if (Array.isArray(obj)) {
      return obj.map(item => this.replaceVariables(item));
    }

    if (typeof obj === 'object' && obj !== null) {
      const result = {};
      for (const key in obj) {
        result[key] = this.replaceVariables(obj[key]);
      }
      return result;
    }

    return obj;
  }

  // 获取变量值（支持 data.xxx 路径）
  getVariable(path) {
    const parts = path.split('.');
    let value = this.variables;

    for (const part of parts) {
      if (value === undefined || value === null) return undefined;
      // 支持数组索引 items[0]
      const match = part.match(/^(\w+)\[(\d+)\]$/);
      if (match) {
        value = value[match[1]];
        if (Array.isArray(value)) {
          value = value[parseInt(match[2])];
        }
      } else {
        value = value[part];
      }
    }

    return value;
  }

  // 查找步骤索引
  findStepIndex(stepId) {
    return this.script.steps.findIndex(s => s.id === stepId);
  }

  // 暂停
  pause() {
    if (this.status === 'running') {
      this.status = 'paused';
      this.log('info', '脚本暂停');
    }
  }

  // 继续
  resume() {
    if (this.status === 'paused') {
      this.status = 'running';
      this.log('info', '脚本继续');
      return this.run();
    }
  }

  // 停止
  stop() {
    this.status = 'idle';
    this.log('info', '脚本停止');
  }

  // 日志
  log(level, message) {
    const entry = { time: new Date().toISOString(), level, message };
    this.logs.push(entry);
    console.log(`[ScriptEngine] ${level.toUpperCase()}: ${message}`);
  }

  // 工具函数
  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// 导出单例
const scriptEngine = new ScriptEngine();
