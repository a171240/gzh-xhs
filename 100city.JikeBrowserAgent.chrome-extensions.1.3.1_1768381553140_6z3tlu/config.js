// ========== 统一配置文件 ==========
// Browser Agent V8.0 配置

// ========== 固定配置（不从接口获取） ==========
const STATIC_CONFIG = {
  // VERSION 从 manifest.json 读取: chrome.runtime.getManifest().version
  
  // 主站配置
  SITE_URL: 'https://100.city',
  SITE_DOMAINS: ['100.city', 'web-test.100.city', 'localhost', '127.0.0.1'],
  
  // API 配置
  API_BASE_PROD: 'https://gin.100.city/api',
  API_BASE_TEST: 'https://gin-test.100.city/api',
  CONFIG_API: 'https://py-api-v2.dso100.com/api/public/chrome_tool/browser_agent_config',
  
  // 日志配置
  LOG_LEVEL: {
    ACTION: '🎯',
    SUCCESS: '✅',
    ERROR: '❌',
    WARN: '⚠️',
    INFO: 'ℹ️',
    DEBUG: '🔍',
  },
};

// ========== 默认动态配置（接口挂了时的兜底） ==========
const DEFAULT_DYNAMIC_CONFIG = {
  DEBUG_MODE: false,
  AUTO_SWITCH_BACK: true,
  AUTO_CLOSE_OLD_TAB: true,
  ARTIFACTS: {
    ENABLE_CLEAN_SCREENSHOT: true,
    ENABLE_MARKED_SCREENSHOT: false,
    MAX_ELEMENTS: 100,
    SCREENSHOT_QUALITY: 70,    // 截图质量 (1-100)
    VIEWPORT_WIDTH: 1280,      // 固定视口宽度
    VIEWPORT_HEIGHT: 800,      // 固定视口高度
    OPTIMIZE_ELEMENTS: false,  // 是否启用简化版元素数据
    TEXT_MAX_LENGTH: 25,       // 文本截断长度（优化模式）
  },
  // 页面预处理配置
  SMART_RULES: {
    ENABLED: true,              // 总开关
    AUTO_HANDLE_ERROR: true,    // 自动处理错误页（返回失败）
    AUTO_ACCEPT_COOKIE: true,   // 自动点击 Cookie 同意按钮
    AUTO_CLOSE_POPUP: true,     // 自动关闭遮罩层弹窗
  },
  MAX_STEPS: 30,
  ACTION_TIMEOUT: 30000,
  SCREENSHOT_TIMEOUT: 30000,
  MASK: {
    GROUP_NAME: 'Agent 任务',
    GROUP_COLOR: 'blue',
    STATUS_TEXT: 'Agent 控制中...',
  },
};

// ========== 运行时配置 ==========
let runtimeConfig = null;
let configFetchedAt = 0;
const CONFIG_CACHE_TTL = 60000; // 缓存 1 分钟

// 下划线转驼峰
function snakeToCamel(obj) {
  if (obj === null || typeof obj !== 'object') return obj;
  if (Array.isArray(obj)) return obj.map(snakeToCamel);
  
  const result = {};
  for (const key in obj) {
    const camelKey = key.toUpperCase(); // 转大写，匹配原有风格
    result[camelKey] = snakeToCamel(obj[key]);
  }
  return result;
}

// 获取动态配置（带缓存）
async function fetchDynamicConfig() {
  const now = Date.now();
  
  // 缓存有效，直接返回
  if (runtimeConfig && (now - configFetchedAt) < CONFIG_CACHE_TTL) {
    console.log('[Agent] ⏱️ 使用缓存配置，剩余', Math.round((CONFIG_CACHE_TTL - (now - configFetchedAt)) / 1000), '秒');
    return runtimeConfig;
  }
  
  try {
    console.log('[Agent] 🌐 正在从接口获取配置...');
    const response = await fetch(STATIC_CONFIG.CONFIG_API, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await response.json();
    
    if (data.code === 200 && data.data_dict) {
      // 转换命名风格并合并
      const remoteConfig = snakeToCamel(data.data_dict);
      // 深度合并 ARTIFACTS
      runtimeConfig = { 
        ...DEFAULT_DYNAMIC_CONFIG, 
        ...remoteConfig,
        ARTIFACTS: {
          ...DEFAULT_DYNAMIC_CONFIG.ARTIFACTS,
          ...(remoteConfig.ARTIFACTS || {})
        },
        SMART_RULES: {
          ...DEFAULT_DYNAMIC_CONFIG.SMART_RULES,
          ...(remoteConfig.SMART_RULES || {})
        }
      };
      configFetchedAt = now;
      console.log('[Agent] ✅ 配置已从接口获取:', runtimeConfig);
    } else {
      throw new Error('接口返回异常: code=' + data.code);
    }
  } catch (e) {
    console.warn('[Agent] ❌ 获取远程配置失败，使用默认配置:', e.message);
    if (!runtimeConfig) {
      runtimeConfig = { ...DEFAULT_DYNAMIC_CONFIG };
      console.log('[Agent] 📦 使用默认兜底配置:', runtimeConfig);
    }
  }
  
  return runtimeConfig;
}

// 同步获取配置（用于不能 await 的地方）
function getDynamicConfig() {
  return runtimeConfig || DEFAULT_DYNAMIC_CONFIG;
}

// 合并后的完整配置对象（兼容旧代码）
const CONFIG = new Proxy({}, {
  get(target, prop) {
    // 静态配置优先
    if (prop in STATIC_CONFIG) {
      return STATIC_CONFIG[prop];
    }
    // 动态配置
    const dynamic = getDynamicConfig();
    if (prop in dynamic) {
      return dynamic[prop];
    }
    return undefined;
  }
});

// 判断是否是控制页面
function isControlPage(hostname) {
  return STATIC_CONFIG.SITE_DOMAINS.some(domain => 
    hostname === domain || hostname.endsWith('.' + domain)
  );
}

// 根据环境获取 API 地址
function getApiBase(hostname) {
  if (hostname?.includes('test') || hostname === 'localhost' || hostname === '127.0.0.1') {
    return STATIC_CONFIG.API_BASE_TEST;
  }
  return STATIC_CONFIG.API_BASE_PROD;
}
