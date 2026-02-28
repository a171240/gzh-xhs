// Content Script - V8.0
// 功能：
// 1. 在控制页面：桥接网页和 background 的通信
// 2. 在被控制页面：执行滚动、点击等操作
// 3. 显示蓝光遮罩和状态栏（使用 Shadow DOM 隔离样式）
// 4. 阻止用户操作（拦截所有键盘、鼠标、触摸事件）

console.log('[Content Script] 已注入:', location.href);

// ========== 增强选择器查找：支持shadowRoot ==========
function findElementWithShadowRoot(selector) {
  // 1. 先尝试普通查找
  let el = document.querySelector(selector);
  if (el) return el;
  
  // 2. 查找所有shadowRoot并在其中搜索
  const allElements = document.querySelectorAll('*');
  for (const element of allElements) {
    if (element.shadowRoot) {
      el = element.shadowRoot.querySelector(selector);
      if (el) return el;
    }
  }
  
  // 3. 特殊处理wujie-app（微信视频号等使用的微前端框架）
  const wujieApp = document.querySelector('wujie-app');
  if (wujieApp && wujieApp.shadowRoot) {
    el = wujieApp.shadowRoot.querySelector(selector);
    if (el) return el;
  }
  
  return null;
}

// ========== 功能3: 蓝光遮罩和状态栏（Shadow DOM 隔离） ==========
let maskVisible = false;
let maskContainer = null;
let shadowRoot = null;
let overlayEl = null;
let statusBarEl = null;

// 事件拦截器
let eventBlockerActive = false;

// 只拦截用户主动触发的事件，不拦截 scroll
const blockedEvents = ['mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu', 
                       'keydown', 'keyup', 'keypress', 
                       'touchstart', 'touchend', 'touchmove',
                       'pointerdown', 'pointerup', 'pointermove'];

// 标记：当前是否是插件在执行操作
let agentOperating = false;

function blockEvent(e) {
  // 如果是插件在操作，不拦截
  if (agentOperating) return;
  
  // CDP 发送的事件 isTrusted 为 true，但它们不经过 JS 事件监听器
  // 不拦截 isTrusted 为 false 的事件
  if (!e.isTrusted) return;
  
  e.preventDefault();
  e.stopPropagation();
  e.stopImmediatePropagation();
  console.log('[Agent] 阻止用户事件:', e.type);
  return false;
}

// 开始插件操作
function startAgentOperation() {
  agentOperating = true;
}

function endAgentOperation() {
  agentOperating = false;
}

function enableEventBlocker() {
  if (eventBlockerActive) return;
  
  blockedEvents.forEach(eventType => {
    document.addEventListener(eventType, blockEvent, { capture: true, passive: false });
    window.addEventListener(eventType, blockEvent, { capture: true, passive: false });
  });
  
  eventBlockerActive = true;
  console.log('[Agent] 事件拦截器已启用');
}

function disableEventBlocker() {
  if (!eventBlockerActive) return;
  
  blockedEvents.forEach(eventType => {
    document.removeEventListener(eventType, blockEvent, { capture: true });
    window.removeEventListener(eventType, blockEvent, { capture: true });
  });
  
  eventBlockerActive = false;
  console.log('[Agent] 事件拦截器已禁用');
}

function createMaskUI() {
  const existing = document.getElementById('agent-mask-shadow-host');
  if (existing && existing.shadowRoot) {
    console.log('[Agent] 复用已存在的 Shadow DOM 遮罩');
    maskContainer = existing;
    shadowRoot = existing.shadowRoot;
    overlayEl = shadowRoot.querySelector('.overlay');
    statusBarEl = shadowRoot.querySelector('.status-bar');
    return { overlay: overlayEl, statusBar: statusBarEl };
  }
  
  if (maskContainer) {
    return { overlay: overlayEl, statusBar: statusBarEl };
  }
  
  console.log('[Agent] 创建 Shadow DOM 遮罩');
  
  // 创建 Shadow DOM 宿主
  maskContainer = document.createElement('div');
  maskContainer.id = 'agent-mask-shadow-host';
  maskContainer.style.cssText = 'position: fixed; inset: 0; z-index: 2147483647; pointer-events: none;';
  
  // 创建 Shadow Root
  shadowRoot = maskContainer.attachShadow({ mode: 'open' });
  
  // 在 Shadow DOM 内部创建样式和元素
  // :host { all: initial; } 重置所有继承的样式
  // 主色: #547CFE (84, 124, 254) 辅色: #7B9FFF (123, 159, 255)
  shadowRoot.innerHTML = `
    <style>
      /* 重置所有继承的样式 */
      :host {
        all: initial;
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        pointer-events: none;
      }
      
      *, *::before, *::after {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
      }
      
      .overlay {
        position: fixed;
        inset: 0;
        pointer-events: none;
        opacity: 0;
        transition: opacity 300ms ease;
        z-index: 1;
      }
      .overlay.visible { opacity: 1; }
      .overlay.blocking {
        pointer-events: auto;
        cursor: not-allowed;
      }
      
      /* 蓝色实线边框 + 强光效果 */
      .border-frame {
        position: absolute;
        inset: 0;
        border: 3px solid rgba(84, 124, 254, 0.8);
        border-radius: 0;
        pointer-events: none;
        box-shadow: 
          inset 0 0 80px rgba(84, 124, 254, 0.15),
          0 0 30px rgba(84, 124, 254, 0.3);
      }
      
      /* 四边渐变遮罩 - 中间透明 */
      .edge-mask {
        position: absolute;
        pointer-events: none;
      }
      .edge-mask-top {
        top: 0; left: 0; right: 0; height: 12.5%;
        background: linear-gradient(to bottom, rgba(84, 124, 254, 0.12) 0%, transparent 100%);
      }
      .edge-mask-bottom {
        bottom: 0; left: 0; right: 0; height: 12.5%;
        background: linear-gradient(to top, rgba(84, 124, 254, 0.12) 0%, transparent 100%);
      }
      .edge-mask-left {
        top: 0; bottom: 0; left: 0; width: 12.5%;
        background: linear-gradient(to right, rgba(84, 124, 254, 0.12) 0%, transparent 100%);
      }
      .edge-mask-right {
        top: 0; bottom: 0; right: 0; width: 12.5%;
        background: linear-gradient(to left, rgba(84, 124, 254, 0.12) 0%, transparent 100%);
      }
      
      /* 顶部流光条 */
      .flow-light-top {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 4px;
        background: linear-gradient(90deg, 
          transparent 0%, 
          rgba(123, 159, 255, 0.3) 25%,
          rgba(84, 124, 254, 1) 50%, 
          rgba(123, 159, 255, 0.3) 75%,
          transparent 100%);
        box-shadow: 0 0 20px rgba(84, 124, 254, 0.8);
        animation: flow-move 2s linear infinite;
      }
      
      /* 底部流光条 */
      .flow-light-bottom {
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        height: 4px;
        background: linear-gradient(90deg, 
          transparent 0%, 
          rgba(123, 159, 255, 0.3) 25%,
          rgba(84, 124, 254, 1) 50%, 
          rgba(123, 159, 255, 0.3) 75%,
          transparent 100%);
        box-shadow: 0 0 20px rgba(84, 124, 254, 0.8);
        animation: flow-move-reverse 2s linear infinite;
      }
      
      @keyframes flow-move {
        0% { transform: translateX(-100%); }
        100% { transform: translateX(100%); }
      }
      
      @keyframes flow-move-reverse {
        0% { transform: translateX(100%); }
        100% { transform: translateX(-100%); }
      }
      
      /* 四角装饰 */
      .corner {
        position: absolute;
        width: 30px;
        height: 30px;
        pointer-events: none;
      }
      .corner::before, .corner::after {
        content: '';
        position: absolute;
        background: rgba(84, 124, 254, 1);
        box-shadow: 0 0 10px rgba(84, 124, 254, 0.8);
      }
      .corner-tl { top: 0; left: 0; }
      .corner-tl::before { top: 0; left: 0; width: 30px; height: 3px; }
      .corner-tl::after { top: 0; left: 0; width: 3px; height: 30px; }
      .corner-tr { top: 0; right: 0; }
      .corner-tr::before { top: 0; right: 0; width: 30px; height: 3px; }
      .corner-tr::after { top: 0; right: 0; width: 3px; height: 30px; }
      .corner-bl { bottom: 0; left: 0; }
      .corner-bl::before { bottom: 0; left: 0; width: 30px; height: 3px; }
      .corner-bl::after { bottom: 0; left: 0; width: 3px; height: 30px; }
      .corner-br { bottom: 0; right: 0; }
      .corner-br::before { bottom: 0; right: 0; width: 30px; height: 3px; }
      .corner-br::after { bottom: 0; right: 0; width: 3px; height: 30px; }
      
      /* 四角呼吸 */
      .corner::before, .corner::after {
        animation: corner-pulse 2s ease-in-out infinite;
      }
      @keyframes corner-pulse {
        0%, 100% { opacity: 0.8; }
        50% { opacity: 1; box-shadow: 0 0 15px rgba(123, 159, 255, 1); }
      }
      
      .status-bar {
        position: fixed;
        bottom: 30px;
        left: 50%;
        transform: translateX(-50%) translateY(20px);
        display: flex;
        align-items: center;
        gap: 12px;
        width: 300px;
        padding: 10px 18px;
        background: rgba(15, 25, 50, 0.75);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(84, 124, 254, 0.5);
        border-radius: 12px;
        box-shadow: 
          0 8px 32px rgba(0, 0, 0, 0.3),
          0 0 25px rgba(84, 124, 254, 0.2);
        opacity: 0;
        transition: opacity 200ms ease, transform 200ms ease;
        pointer-events: auto;
        z-index: 3;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }
      
      .status-bar.visible {
        opacity: 1;
        transform: translateX(-50%) translateY(0);
      }
      
      .icon {
        width: 20px;
        height: 20px;
        min-width: 20px;
        min-height: 20px;
        max-width: 20px;
        max-height: 22px;
        color: #7B9FFF;
        filter: drop-shadow(0 0 4px rgba(84, 124, 254, 0.6));
        flex-shrink: 0;
        display: block;
      }
      
      /* 时钟指针旋转动画 */
      .clock-hand {
        transform-origin: 12px 12px;
        animation: clock-rotate 4s linear infinite;
      }
      .clock-hand-fast {
        transform-origin: 12px 12px;
        animation: clock-rotate 1s linear infinite;
      }
      
      @keyframes clock-rotate {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
      }
      
      /* 图标呼吸发光 */
      @keyframes icon-glow {
        0%, 100% { 
          color: #7B9FFF;
          filter: drop-shadow(0 0 3px rgba(84, 124, 254, 0.5));
        }
        50% { 
          color: #A8C0FF;
          filter: drop-shadow(0 0 8px rgba(123, 159, 255, 0.8));
        }
      }
      
      .text-container {
        flex: 1;
        overflow: hidden;
        position: relative;
      }
      
      .text {
        color: #ffffff;
        font-size: 14px;
        font-weight: 500;
        line-height: 24px;
        white-space: nowrap;
        display: inline-block;
      }
      
      .text.scrolling {
        animation: text-scroll 6s linear infinite;
      }
      
      @keyframes text-scroll {
        0% { transform: translateX(0); }
        20% { transform: translateX(0); }
        80% { transform: translateX(calc(-100% + 220px)); }
        100% { transform: translateX(calc(-100% + 220px)); }
      }
    </style>
    
    <div class="overlay">
      <div class="edge-mask edge-mask-top"></div>
      <div class="edge-mask edge-mask-bottom"></div>
      <div class="edge-mask edge-mask-left"></div>
      <div class="edge-mask edge-mask-right"></div>
      <div class="border-frame"></div>
      <div class="flow-light-top"></div>
      <div class="flow-light-bottom"></div>
      <div class="corner corner-tl"></div>
      <div class="corner corner-tr"></div>
      <div class="corner corner-bl"></div>
      <div class="corner corner-br"></div>
    </div>
    
    <div class="status-bar">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <line class="clock-hand" x1="12" y1="12" x2="12" y2="7"/>
        <line class="clock-hand-fast" x1="12" y1="12" x2="16" y2="12"/>
      </svg>
      <div class="text-container">
        <span class="text">Agent 控制中...</span>
      </div>
    </div>
  `;
  
  overlayEl = shadowRoot.querySelector('.overlay');
  statusBarEl = shadowRoot.querySelector('.status-bar');
  
  // 添加到页面
  const container = document.body || document.documentElement;
  container.appendChild(maskContainer);
  
  console.log('[Agent] Shadow DOM 遮罩已创建');
  return { overlay: overlayEl, statusBar: statusBarEl };
}

function showMask(status = 'Agent 控制中...', blocking = false) {
  console.log('[Agent] showMask:', status, 'blocking:', blocking);
  
  const { overlay, statusBar } = createMaskUI();
  
  if (!maskContainer.isConnected) {
    const container = document.body || document.documentElement;
    container.appendChild(maskContainer);
    console.log('[Agent] 重新添加 Shadow DOM 宿主到 DOM');
  }
  
  overlay.classList.add('visible');
  if (blocking) {
    overlay.classList.add('blocking');
  } else {
    overlay.classList.remove('blocking');
  }
  
  statusBar.classList.add('visible');
  statusBar.querySelector('.text').textContent = status;
  
  // 暂时禁用事件拦截器，因为会影响 CDP 和 JS 模拟的事件
  // enableEventBlocker();
  
  maskVisible = true;
  console.log('[Agent] 显示遮罩:', status, 'blocking:', blocking);
}

function hideMask() {
  if (!overlayEl || !statusBarEl) return;
  
  overlayEl.classList.remove('visible', 'blocking');
  statusBarEl.classList.remove('visible');
  disableEventBlocker();
  
  maskVisible = false;
  console.log('[Agent] 隐藏遮罩');
}

function updateMaskStatus(status) {
  if (!statusBarEl) return;
  const textEl = statusBarEl.querySelector('.text');
  textEl.textContent = status;
  
  // 检查文本是否超长，超长则添加滚动动画
  const containerWidth = 220; // 文本容器大约宽度
  textEl.classList.remove('scrolling');
  
  // 等待渲染后检查宽度
  requestAnimationFrame(() => {
    if (textEl.scrollWidth > containerWidth) {
      textEl.classList.add('scrolling');
    }
  });
}

// 页面加载时检查是否需要恢复遮罩状态
async function checkAndRestoreMask() {
  try {
    const state = await chrome.runtime.sendMessage({ action: 'getMaskState' });
    console.log('[Agent] 检查遮罩状态:', state);
    if (state && state.visible) {
      showMask(state.status, state.blocking);
    }
  } catch (e) {
    console.log('[Agent] 获取遮罩状态失败:', e.message);
  }
}

// 页面加载完成后检查遮罩状态
if (document.readyState === 'complete') {
  checkAndRestoreMask();
} else {
  window.addEventListener('load', checkAndRestoreMask);
}

// ========== 功能1: 网页 <-> Background 桥接 ==========
// 控制页面域名列表
const CONTROL_PAGE_DOMAINS = ['100.city', 'localhost', '127.0.0.1'];
const isControlPage = CONTROL_PAGE_DOMAINS.some(domain => 
  location.hostname === domain || location.hostname.endsWith('.' + domain)
);

if (!isControlPage) {
  window.addEventListener('message', async (event) => {
    if (event.source !== window) return;
    
    const message = event.data;
    if (message?.target !== 'browser-agent-extension') return;
    
    console.log('[Agent Bridge] 收到网页消息:', message);
    
    try {
      const response = await chrome.runtime.sendMessage({
        action: message.action,
        params: message.params
      });
      
      console.log('[Agent Bridge] Background 响应:', response);
      
      window.postMessage({
        target: 'browser-agent-web',
        requestId: message.requestId,
        success: response?.success !== false,
        data: response,
        error: response?.error
      }, '*');
    } catch (error) {
      console.error('[Agent Bridge] 错误:', error);
      window.postMessage({
        target: 'browser-agent-web',
        requestId: message.requestId,
        success: false,
        error: error.message
      }, '*');
    }
  });

  window.postMessage({ target: 'browser-agent-web', type: 'ready' }, '*');
  console.log('[Agent Bridge] 已通知网页 Extension 就绪');
} else {
  console.log('[Agent] 控制页面，跳过网页消息监听');
}


// ========== 功能2: 执行页面操作 ==========
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[Agent] 收到消息:', message.action, 'URL:', location.href);
  
  if (!message.action) {
    console.log('[Agent] 消息没有 action，忽略');
    return false;
  }
  
  handleAction(message)
    .then(result => {
      console.log('[Agent] 执行结果:', result);
      sendResponse(result);
    })
    .catch(error => {
      console.error('[Agent] 执行错误:', error);
      sendResponse({ success: false, error: error.message });
    });
  
  return true;
});

async function handleAction(message) {
  const { action, params = {} } = message;
  
  // 标记插件开始操作
  startAgentOperation();
  
  try {
    switch (action) {
      case 'ping':
        return { success: true, message: 'pong from content script', url: location.href };
      
      // 页面就绪检查
      case 'checkReady':
        return { 
          success: true, 
          ready: document.readyState === 'complete',
          sinceLoadMs: performance.now(),  // 页面加载后经过的时间
          url: location.href
        };
      
      case 'scroll':
        return await executeScroll(params.y || 300);
      
      case 'scrollToTop':
        return await executeScroll(-document.documentElement.scrollHeight);
      
      case 'scrollToBottom':
        return await executeScroll(document.documentElement.scrollHeight);
      
      case 'click':
        return await executeClick(params);
      
      case 'clickButtonByText':
        // 专门用于点击按钮，只在 button 元素中查找
        const btnText = params.text;
        const btnContainer = params.container;
        const btnIndex = params.index || 0;  // 默认点击第一个匹配的
        let btns;
        if (btnContainer) {
          const container = document.querySelector(btnContainer);
          btns = container ? [...container.querySelectorAll('button')] : [];
        } else {
          btns = [...document.querySelectorAll('button')];
        }
        const matchedBtns = btns.filter(b => b.textContent.includes(btnText));
        if (matchedBtns.length > btnIndex) {
          matchedBtns[btnIndex].click();
          return { success: true, text: matchedBtns[btnIndex].textContent.trim(), index: btnIndex };
        }
        return { success: false, error: `找不到包含文本 "${btnText}" 的按钮（索引 ${btnIndex}）` };
      
      case 'navigateToLink':
        // 获取链接元素的 href 并在当前页面跳转
        const linkEl = document.querySelector(params.selector);
        if (!linkEl) {
          return { success: false, error: `找不到链接元素: ${params.selector}` };
        }
        let linkHref = linkEl.href;
        if (!linkHref) {
          return { success: false, error: `元素没有 href 属性` };
        }
        // 如果需要，从当前 URL 获取 token 并拼接
        if (params.appendToken) {
          const urlParams = new URLSearchParams(window.location.search);
          const token = urlParams.get('token');
          if (token) {
            linkHref += (linkHref.includes('?') ? '&' : '?') + 'token=' + token + '&lang=zh_CN';
          }
        }
        location.href = linkHref;
        return { success: true, url: linkHref };
      
      case 'navigateWithToken':
        // 跳转到固定 URL，自动拼接当前页面的 token
        const baseUrl = params.url;
        const tokenParams = new URLSearchParams(window.location.search);
        const pageToken = tokenParams.get('token');
        let finalUrl = baseUrl;
        if (pageToken) {
          finalUrl += (baseUrl.includes('?') ? '&' : '?') + 'token=' + pageToken + '&lang=zh_CN';
        }
        location.href = finalUrl;
        return { success: true, url: finalUrl };
      
      case 'type':
        return await executeType(params);
      
      case 'focusElement':
        return await focusElement(params.selector);
      
      case 'getPageInfo':
        return getPageInfo();
      
      case 'getElements':
        return getClickableElements();
      
      // 元素标记
      case 'markElements':
        return markClickableElements(params);
      
      case 'clearMarks':
        return clearElementMarks();
      
      case 'executeSearch':
        return executeSearch(params.keyword);
      
      case 'clickWechatPublish':
        return await clickWechatPublishButton();
      
      case 'uploadImageFromUrl':
        return await uploadImageFromUrl(params.imageUrl, params.selector);
      
      case 'uploadMultipleImagesFromUrls':
        return await uploadMultipleImagesFromUrls(params.imageUrls, params.selector);
      
      case 'downloadImageAsBase64':
        return await downloadImageAsBase64(params.imageUrl);
      
      case 'uploadMarkdownFile':
        return await uploadMarkdownFile(params.content, params.images, params.selector);
      
      case 'uploadVideoFromUrl':
        return await uploadVideoFromUrl(params.videoUrl, params.selector);
      
      case 'fillRichEditor':
        return await fillRichEditor(params);
      
      case 'addXhsTopic':
        return await addXhsTopic(params.topic);
      
      // ========== 抖音专用 ==========
      case 'addDouyinTopic':
        return await addDouyinTopic(params.topic);
      
      // ========== B站专用 ==========
      case 'addBilibiliTag':
        return await addBilibiliTag(params.topic);
      
      // 等待操作
      case 'waitForSelector':
        return await waitForSelector(params.selector, params.timeout, params.visible);
      
      case 'assertSelector':
        return assertSelector(params.selector);
      
      // 遮罩控制
      case 'showMask':
        showMask(params.status || 'Agent 控制中...', params.blocking || false);
        return { success: true };
      
      case 'hideMask':
        hideMask();
        return { success: true };
      
      case 'updateStatus':
        updateMaskStatus(params.status);
        return { success: true };
      
      default:
        return { success: false, error: `未知操作: ${action}` };
    }
  } finally {
    // 操作完成，恢复拦截
    endAgentOperation();
  }
}

// 执行滚动 - 按优先级尝试，成功就停止
function executeScroll(deltaY) {
  const beforeY = window.scrollY;
  const scrollingEl = document.scrollingElement || document.documentElement;
  const beforeScrollTop = scrollingEl.scrollTop;
  
  return new Promise(resolve => {
    let method = '';
    
    // 方法1: 最标准的 scrollingElement（适用于大多数网站）
    scrollingEl.scrollTop += deltaY;
    
    setTimeout(() => {
      if (scrollingEl.scrollTop !== beforeScrollTop) {
        method = 'scrollingElement';
      } else {
        // 方法2: window.scrollBy（备用）
        window.scrollBy(0, deltaY);
        
        if (window.scrollY !== beforeY) {
          method = 'window.scrollBy';
        } else {
          // 方法3: 找特定滚动容器（某些SPA网站）
          const containers = ['.search-page', '.bili-feed4-layout', '#app', 'main', '.container'];
          for (const selector of containers) {
            const el = document.querySelector(selector);
            if (el && el.scrollHeight > el.clientHeight + 10) {
              const before = el.scrollTop;
              el.scrollTop += deltaY;
              if (el.scrollTop !== before) {
                method = `container: ${selector}`;
                break;
              }
            }
          }
        }
      }
      
      const afterY = window.scrollY || scrollingEl.scrollTop;
      
      resolve({
        success: true,
        scrolled: afterY !== beforeY || scrollingEl.scrollTop !== beforeScrollTop,
        deltaY,
        beforeY,
        afterY,
        method: method || 'unknown',
        scrollHeight: scrollingEl.scrollHeight
      });
    }, 150);  // 300ms → 150ms
  });
}

// 执行点击 - 支持五种方式：索引、坐标、选择器、多选择器、文本
function executeClick(params) {
  let el;
  let method = '';
  let usedSelector = '';
  
  // 兼容旧版调用（直接传 selector 字符串）
  if (typeof params === 'string') {
    params = { selector: params };
  }
  
  // 方式1: 按索引（AI 从截图上看到的数字）
  if (params.index !== undefined) {
    el = document.querySelector(`[data-agent-index="${params.index}"]`);
    method = 'byIndex';
    if (!el) {
      return { success: false, error: `找不到索引 ${params.index} 的元素`, method };
    }
  }
  // 方式2: 按坐标
  else if (params.x !== undefined && params.y !== undefined) {
    el = document.elementFromPoint(params.x, params.y);
    method = 'byCoordinates';
    if (!el) {
      return { success: false, error: `坐标 (${params.x}, ${params.y}) 处没有元素`, method };
    }
  }
  // 方式3: 按文本内容（精确匹配）（支持shadowRoot）
  else if (params.text) {
    method = 'byText';
    
    // 先在普通DOM中查找
    let allElements = document.querySelectorAll('a, button, div, span, li, td, th, label, [role="button"], [role="tab"]');
    
    for (const elem of allElements) {
      const elemText = (elem.textContent || '').trim();
      // 精确匹配或包含匹配
      if (elemText === params.text || (params.contains && elemText.includes(params.text))) {
        // 确保是可见元素
        const rect = elem.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          el = elem;
          break;
        }
      }
    }
    
    // 如果普通DOM中没找到，在shadowRoot中查找
    if (!el) {
      const wujieApp = document.querySelector('wujie-app');
      if (wujieApp && wujieApp.shadowRoot) {
        const shadowElements = wujieApp.shadowRoot.querySelectorAll('a, button, div, span, li, td, th, label, [role="button"], [role="tab"]');
        for (const elem of shadowElements) {
          const elemText = (elem.textContent || '').trim();
          if (elemText === params.text || (params.contains && elemText.includes(params.text))) {
            const rect = elem.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              el = elem;
              break;
            }
          }
        }
      }
    }
    
    if (!el) {
      return { success: false, error: `找不到文本为 "${params.text}" 的元素`, method };
    }
  }
  // 方式4: 多选择器容错（增强版：支持shadowRoot）
  else if (params.selectors && Array.isArray(params.selectors)) {
    method = 'bySelectors';
    for (const sel of params.selectors) {
      el = findElementWithShadowRoot(sel);
      if (el) {
        usedSelector = sel;
        break;
      }
    }
    if (!el) {
      return { success: false, error: `所有选择器都找不到元素: ${params.selectors.join(', ')}`, method };
    }
  }
  // 方式5: 单选择器（增强版：支持shadowRoot）
  else if (params.selector) {
    el = findElementWithShadowRoot(params.selector);
    method = 'bySelector';
    usedSelector = params.selector;
    if (!el) {
      return { success: false, error: `找不到元素: ${params.selector}`, method };
    }
  }
  else {
    return { success: false, error: '请提供 index、坐标(x,y)、text、selector 或 selectors' };
  }
  
  // 滚动到元素并点击
  el.scrollIntoView({ behavior: 'instant', block: 'center' });  // smooth → instant 更快
  
  return new Promise(resolve => {
    setTimeout(() => {
      // 检查元素是否被遮挡
      const rect = el.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const topEl = document.elementFromPoint(centerX, centerY);
      
      // 如果被遮挡，尝试点击实际顶层元素或强制点击目标
      if (topEl && topEl !== el && !el.contains(topEl)) {
        console.log('[Agent] 元素被遮挡，强制点击');
      }
      
      el.click();
      resolve({ 
        success: true, 
        method,
        usedSelector: usedSelector || undefined,
        tagName: el.tagName,
        text: (el.textContent || '').trim().substring(0, 30)
      });
    }, 150);  // 300ms → 150ms
  });
}

// 聚焦元素（供 CDP 输入使用）（支持shadowRoot）
async function focusElement(selector) {
  const el = findElementWithShadowRoot(selector);
  if (!el) {
    console.error('[Agent] focusElement 找不到元素:', selector);
    return { success: false, error: `找不到元素: ${selector}` };
  }
  
  console.log('[Agent] focusElement 找到元素:', selector, el.tagName, el.id, el.className);
  
  // 滚动到元素
  el.scrollIntoView({ behavior: 'instant', block: 'center' });
  
  // 等待滚动完成
  await new Promise(r => setTimeout(r, 50));  // 100ms → 50ms
  
  // 点击激活（有些输入框需要点击才能激活）
  el.click();
  await new Promise(r => setTimeout(r, 30));  // 50ms → 30ms
  
  // 聚焦
  el.focus();
  
  // 清空现有内容
  if ('value' in el) {
    el.value = '';
    // 触发 input 事件让框架知道值变了
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  
  // 确保元素是活动元素
  if (document.activeElement !== el) {
    console.warn('[Agent] 元素未成为活动元素，再次尝试聚焦');
    el.focus();
  }
  
  console.log('[Agent] 元素已聚焦:', selector, '活动元素:', document.activeElement?.tagName, document.activeElement?.id);;
  return { success: true, selector, tagName: el.tagName, isActive: document.activeElement === el };
}

// 执行输入 - 支持 index 或 selector 定位元素（支持shadowRoot）
async function executeType(params) {
  // 兼容旧版调用方式
  if (typeof params === 'string') {
    params = { selector: params };
  }
  
  const { index, selector, text, pressEnter, clear } = params;
  
  // 定位元素：优先用 index，其次用 selector
  let el;
  let usedMethod = '';
  
  if (index !== undefined) {
    el = document.querySelector(`[data-agent-index="${index}"]`);
    usedMethod = 'byIndex';
    if (!el) {
      return { success: false, error: `找不到索引 ${index} 的元素`, method: usedMethod };
    }
  } else if (selector) {
    el = findElementWithShadowRoot(selector);
    usedMethod = 'bySelector';
    if (!el) {
      return { success: false, error: `找不到元素: ${selector}`, method: usedMethod };
    }
  } else {
    return { success: false, error: '请提供 index 或 selector 参数' };
  }
  
  // 判断元素类型
  const isContentEditable = el.isContentEditable || el.contentEditable === 'true';
  const isInputLike = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA';
  
  // 聚焦
  el.focus();
  el.dispatchEvent(new Event('focus', { bubbles: true }));
  
  // 清空内容（如果指定 clear: true）
  if (clear) {
    if (isInputLike) {
      el.value = '';
    } else if (isContentEditable) {
      el.innerHTML = '';
    }
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
    await new Promise(r => setTimeout(r, 50));
  }
  
  // 批量设置内容（比逐字符快 10 倍以上）
  if (isInputLike) {
    el.value = text;
    // 触发完整事件链，兼容 React/Vue
    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
  } else if (isContentEditable) {
    if (!clear) el.innerHTML = '';  // 如果没有 clear，也要清空
    document.execCommand('insertText', false, text);
    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
  }
  
  // change 事件
  el.dispatchEvent(new Event('change', { bubbles: true }));
  
  if (pressEnter) {
    el.dispatchEvent(new KeyboardEvent('keydown', { 
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true
    }));
    el.dispatchEvent(new KeyboardEvent('keypress', { 
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true
    }));
    el.dispatchEvent(new KeyboardEvent('keyup', { 
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
    }));
    
    // form 提交
    if (isInputLike && el.form) {
      el.form.submit();
    }
    
    // 尝试点击搜索按钮
    await new Promise(r => setTimeout(r, 50));
    const searchBtn = document.querySelector('.nav-search-btn, .search-button, button[type="submit"], .search-btn');
    if (searchBtn) {
      searchBtn.click();
    }
  }
  
  const actualValue = isInputLike ? el.value : el.textContent;
  console.log('[Agent] 输入完成:', text?.substring(0, 20), '方式:', usedMethod);
  return { success: true, method: usedMethod, text: text?.substring(0, 20) + '...', actualValue };
}

// 获取页面信息
function getPageInfo() {
  return {
    success: true,
    url: location.href,
    title: document.title,
    scrollY: window.scrollY,
    scrollHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight
  };
}

// 获取可点击元素
function getClickableElements() {
  const elements = [];
  const selectors = 'a, button, input, [onclick], [role="button"], [tabindex]';
  
  document.querySelectorAll(selectors).forEach((el, index) => {
    if (index >= 50) return;
    
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    
    elements.push({
      index,
      tag: el.tagName.toLowerCase(),
      text: (el.textContent || el.value || '').trim().substring(0, 50),
      id: el.id || null,
      className: el.className || null,
      href: el.href || null
    });
  });
  
  return { success: true, elements, count: elements.length };
}

// ========== 元素索引标记 ==========

// 生成唯一 CSS 选择器
function generateSelector(el) {
  if (el.id) return `#${el.id}`;
  
  // 尝试用 class + tag
  if (el.className && typeof el.className === 'string') {
    const classes = el.className.trim().split(/\s+/).filter(c => c && !c.includes(':'));
    if (classes.length > 0) {
      const selector = `${el.tagName.toLowerCase()}.${classes.slice(0, 2).join('.')}`;
      if (document.querySelectorAll(selector).length === 1) return selector;
    }
  }
  
  // 用 nth-child
  const parent = el.parentElement;
  if (parent) {
    const siblings = Array.from(parent.children);
    const index = siblings.indexOf(el) + 1;
    const parentSelector = parent.id ? `#${parent.id}` : parent.tagName.toLowerCase();
    return `${parentSelector} > ${el.tagName.toLowerCase()}:nth-child(${index})`;
  }
  
  return el.tagName.toLowerCase();
}

// 标记所有可点击元素，返回元素列表
// params: { maxElements, optimize, textMaxLength }
function markClickableElements(params = {}) {
  const maxElements = params.maxElements || 100;
  const optimize = params.optimize || false;
  const textMaxLength = params.textMaxLength || 25;
  
  // 先清除旧标记
  document.querySelectorAll('[data-agent-index]').forEach(el => {
    el.removeAttribute('data-agent-index');
  });
  
  const elements = [];
  let index = 1;
  
  // 遍历所有元素，智能判断是否可点击（参考 Manus 实现）
  const allElements = document.querySelectorAll('*');
  
  for (const el of allElements) {
    if (index > maxElements) break;
    
    // 跳过不可见元素
    const rect = el.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5) continue;
    // 跳过视口外的元素
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
    if (rect.right < 0 || rect.left > window.innerWidth) continue;
    
    // 智能判断是否可点击
    if (!isElementClickable(el)) continue;
    
    // 添加索引属性
    el.setAttribute('data-agent-index', index);
    
    const tagName = el.tagName.toLowerCase();
    const rawText = (el.textContent || el.value || '').trim();
    const placeholder = el.placeholder || el.alt || '';
    
    if (optimize) {
      // 简化版：精简字段名，去掉 selector 和 rect
      const item = {
        i: index,
        t: tagName,
        x: rawText.substring(0, textMaxLength)
      };
      // 输入框加 placeholder
      if (!rawText && placeholder) {
        item.p = placeholder.substring(0, textMaxLength);
      }
      elements.push(item);
    } else {
      // 原版：完整字段
      elements.push({
        index,
        tagName,
        text: (rawText || placeholder).substring(0, 50),
        selector: generateSelector(el),
        rect: { 
          x: Math.round(rect.x), 
          y: Math.round(rect.y), 
          width: Math.round(rect.width), 
          height: Math.round(rect.height) 
        }
      });
    }
    
    index++;
  }
  
  console.log('[Agent] 标记了', elements.length, '个元素, optimize:', optimize);
  return { success: true, elements, count: elements.length };
}

// 智能判断元素是否可点击（参考 Manus 实现）
function isElementClickable(el) {
  const tagName = el.tagName.toLowerCase();
  
  // 跳过 body, html, script, style 等
  if (['body', 'html', 'script', 'style', 'head', 'meta', 'link', 'noscript'].includes(tagName)) {
    return false;
  }
  
  // 1. 可点击的标签名
  const clickableTagNames = ['a', 'button', 'input', 'textarea', 'select', 'label', 'details', 'summary', 'menu', 'menuitem'];
  if (clickableTagNames.includes(tagName)) {
    return true;
  }
  
  // 2. 图片（有一定尺寸的）
  if (tagName === 'img') {
    const rect = el.getBoundingClientRect();
    return rect.width >= 20 && rect.height >= 20;
  }
  
  // 3. role 属性
  const role = el.getAttribute('role');
  const clickableRoles = ['button', 'tab', 'link', 'checkbox', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'radio', 'option', 'switch'];
  if (role && clickableRoles.includes(role)) {
    return true;
  }
  
  // 4. aria-role 属性
  const ariaRole = el.getAttribute('aria-role');
  if (ariaRole && clickableRoles.includes(ariaRole)) {
    return true;
  }
  
  // 5. contenteditable
  if (el.getAttribute('contenteditable') === 'true') {
    return true;
  }
  
  // 6. 有点击事件处理器
  if (el.onclick !== null || 
      el.getAttribute('onclick') !== null ||
      el.hasAttribute('ng-click') ||
      el.hasAttribute('@click') ||
      el.hasAttribute('v-on:click')) {
    return true;
  }
  
  // 7. aria 状态属性（通常表示可交互）
  if (el.hasAttribute('aria-expanded') ||
      el.hasAttribute('aria-pressed') ||
      el.hasAttribute('aria-selected') ||
      el.hasAttribute('aria-checked')) {
    return true;
  }
  
  // 8. tabindex（可聚焦通常意味着可交互）
  if (el.hasAttribute('tabindex')) {
    return true;
  }
  
  // 9. cursor: pointer 样式（常见的可点击指示）
  const style = window.getComputedStyle(el);
  if (style.cursor === 'pointer') {
    return true;
  }
  
  // 10. class 名包含常见交互关键词
  const className = el.className?.toString?.() || '';
  const interactiveKeywords = ['click', 'btn', 'button', 'link', 'like', 'collect', 'share', 'action', 'toggle', 'switch', 'tab'];
  if (interactiveKeywords.some(kw => className.toLowerCase().includes(kw))) {
    return true;
  }
  
  return false;
}

// 清除所有索引标记
function clearElementMarks() {
  document.querySelectorAll('[data-agent-index]').forEach(el => {
    el.removeAttribute('data-agent-index');
  });
  console.log('[Agent] 已清除所有元素标记');
  return { success: true };
}

// B站搜索专用
// 注意：CDP 输入不会更新 input.value，所以需要传入 keyword 参数
function executeSearch(keyword) {
  // 如果传入了 keyword，直接使用；否则尝试从输入框获取
  if (!keyword) {
    const input = document.querySelector('input.nav-search-input');
    if (input && input.value) {
      keyword = input.value;
    }
  }
  
  if (!keyword) {
    return { success: false, error: '搜索框为空，请传入 keyword 参数' };
  }
  
  // 直接在当前页面跳转到搜索结果页（避免打开新标签页）
  // B站搜索按钮是 target="_blank"，点击会打开新标签页，所以我们用 location.href
  window.location.href = `https://search.bilibili.com/all?keyword=${encodeURIComponent(keyword)}`;
  
  return { success: true, keyword };
}

// ========== 文件上传函数 ==========

// 上传 Markdown 文件（动态生成）
async function uploadMarkdownFile(content, images, selector) {
  console.log('[Agent] uploadMarkdownFile 开始');
  
  try {
    // 1. 生成 Markdown 内容
    let mdContent = content || '';
    if (images && Array.isArray(images) && images.length > 0) {
      const imagesMd = images.map(url => `![图片](${url})`).join('\n\n');
      mdContent = mdContent + '\n\n' + imagesMd;
    }
    
    console.log('[Agent] MD 内容长度:', mdContent.length);
    
    // 2. 创建 .md 文件
    const blob = new Blob([mdContent], { type: 'text/markdown' });
    const file = new File([blob], 'content.md', { type: 'text/markdown' });
    
    // 3. 找到 file input
    const fileInput = findElementWithShadowRoot(selector);
    if (!fileInput) {
      return { success: false, error: `找不到文件输入框: ${selector}` };
    }
    
    console.log('[Agent] 找到文件输入框:', fileInput);
    
    // 4. 设置文件
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    fileInput.files = dataTransfer.files;
    
    // 5. 触发事件
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    fileInput.dispatchEvent(new Event('input', { bubbles: true }));
    
    console.log('[Agent] MD 文件上传完成');
    return { success: true, contentLength: mdContent.length };
    
  } catch (error) {
    console.error('[Agent] uploadMarkdownFile 错误:', error);
    return { success: false, error: error.message };
  }
}

// 从 URL 上传视频（支持shadowRoot）
async function uploadVideoFromUrl(videoUrl, selector = 'input.upload-input') {
  console.log('[Agent] uploadVideoFromUrl 开始:', videoUrl);
  
  try {
    const fileInput = findElementWithShadowRoot(selector);
    if (!fileInput) {
      return { success: false, error: `找不到文件输入框: ${selector}` };
    }
    
    console.log('[Agent] 正在下载视频...');
    const response = await fetch(videoUrl, { mode: 'cors', credentials: 'omit' });
    if (!response.ok) {
      return { success: false, error: `下载视频失败: ${response.status}` };
    }
    
    const blob = await response.blob();
    console.log('[Agent] 视频下载完成, 大小:', blob.size, '类型:', blob.type);
    
    const urlPath = new URL(videoUrl).pathname;
    const fileName = urlPath.split('/').pop() || 'video.mp4';
    
    let mimeType = blob.type;
    if (!mimeType || mimeType === 'application/octet-stream') {
      const ext = fileName.split('.').pop()?.toLowerCase();
      const mimeMap = {
        'mp4': 'video/mp4',
        'mov': 'video/quicktime',
        'flv': 'video/x-flv',
        'mkv': 'video/x-matroska',
        'avi': 'video/x-msvideo'
      };
      mimeType = mimeMap[ext] || 'video/mp4';
    }
    
    const file = new File([blob], fileName, { type: mimeType });
    console.log('[Agent] 创建 File 对象:', file.name, file.size, file.type);
    
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    fileInput.files = dataTransfer.files;
    
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    fileInput.dispatchEvent(new Event('input', { bubbles: true }));
    
    console.log('[Agent] 视频上传成功');
    return { success: true, fileName: file.name, fileSize: file.size };
    
  } catch (error) {
    console.error('[Agent] uploadVideoFromUrl 错误:', error);
    return { success: false, error: error.message };
  }
}

// ========== 通用富文本编辑器函数 ==========

// 通用富文本编辑器填充（支持shadowRoot）
// params: { selector, content, typeDelay, clearTags, images, markdown }
async function fillRichEditor(params) {
  const { selector, content, typeDelay, clearTags = true, images, markdown } = params;
  console.log('[Agent] fillRichEditor, selector:', selector);
  
  // 如果启用 markdown 模式，拼接图片为 Markdown 格式
  let finalContent = content;
  if (markdown && images && Array.isArray(images) && images.length > 0) {
    const imageMarkdown = images.map(url => `![图片](${url})`).join('\n\n');
    finalContent = content + '\n\n' + imageMarkdown;
    console.log('[Agent] Markdown 模式，拼接图片:', images.length, '张');
  }
  
  try {
    // 🔥 掘金 CodeMirror 特殊处理 - 最高优先级，在任何其他逻辑之前
    if (selector === '.CodeMirror' || (Array.isArray(selector) && selector.includes('.CodeMirror'))) {
      console.log('[Agent] 🎯 检测到 CodeMirror 选择器，直接使用 CodeMirror API');
      console.log('[Agent] 当前页面 URL:', location.href);
      
      const cm = document.querySelector('.CodeMirror');
      console.log('[Agent] document.querySelector(".CodeMirror"):', cm);
      console.log('[Agent] cm.CodeMirror:', cm?.CodeMirror);
      
      // content-script 无法直接访问页面的 JS 对象，需要通过注入脚本来执行
      if (cm) {
        // 清理内容
        let cleanedContent = finalContent;
        if (clearTags) {
          cleanedContent = finalContent
            .replace(/\n?#\[[^\]]+\]/g, '')
            .replace(/\n?#[^\s\n#]+/g, '')
            .trim();
        }
        
        // 通过注入脚本来设置 CodeMirror 内容
        const script = document.createElement('script');
        script.textContent = `
          (function() {
            const cm = document.querySelector('.CodeMirror');
            if (cm && cm.CodeMirror) {
              cm.CodeMirror.setValue(${JSON.stringify(cleanedContent)});
              console.log('[Agent-Injected] CodeMirror 内容设置成功');
            } else {
              console.log('[Agent-Injected] 找不到 CodeMirror 实例');
            }
          })();
        `;
        document.head.appendChild(script);
        script.remove();
        
        console.log('[Agent] 🎉 已注入脚本设置 CodeMirror 内容');
        return { success: true, contentLength: cleanedContent.length, method: 'CodeMirror.setValue.injected' };
      } else {
        console.log('[Agent] ❌ 找不到 CodeMirror 元素');
        return { success: false, error: '找不到 CodeMirror 元素' };
      }
    }
    
    // 添加30秒超时保护
    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => reject(new Error('fillRichEditor超时(30秒)')), 30000);
    });
    
    const fillPromise = (async () => {
      // 支持多选择器
      const selectors = Array.isArray(selector) ? selector : [selector];
      
      let editor = null;
      for (const sel of selectors) {
        editor = findElementWithShadowRoot(sel);
        if (editor) {
          console.log('[Agent] 找到编辑器:', sel);
          break;
        }
      }
      
      if (!editor) {
        return { success: false, error: `找不到编辑器: ${selectors.join(', ')}` };
      }
      
      // 清理内容中的标签（可选）
      let cleanedContent = finalContent;
      if (clearTags) {
        cleanedContent = finalContent
          .replace(/\n?#\[[^\]]+\]/g, '')  // 清理 #[xxx]
          .replace(/\n?#[^\s\n#]+/g, '')   // 清理 #xxx
          .trim();
      }
      console.log('[Agent] fillRichEditor 内容:', cleanedContent.substring(0, 100));
      
      // 🔥 掘金 CodeMirror 特殊处理 - 最高优先级
      console.log('[Agent] 检查元素类型:', editor.tagName, editor.className);
      console.log('[Agent] 元素父级:', editor.parentElement?.className);
      
      // 如果选择器是 .CodeMirror，我们需要确保找到正确的那个
      if (selector === '.CodeMirror' || (Array.isArray(selector) && selector.includes('.CodeMirror'))) {
        console.log('[Agent] 🎯 处理 CodeMirror 选择器');
        
        // 查找所有 CodeMirror 元素
        const allCodeMirrors = document.querySelectorAll('.CodeMirror');
        console.log('[Agent] 找到 CodeMirror 数量:', allCodeMirrors.length);
        
        // 找到在 bytemd-editor 内的 CodeMirror（内容编辑器）
        let contentCodeMirror = null;
        for (const cm of allCodeMirrors) {
          console.log('[Agent] 检查 CodeMirror:', cm.parentElement?.className);
          if (cm.closest('.bytemd-editor')) {
            contentCodeMirror = cm;
            console.log('[Agent] ✅ 找到内容区域的 CodeMirror');
            break;
          }
        }
        
        if (contentCodeMirror && contentCodeMirror.CodeMirror) {
          console.log('[Agent] ✅ 使用内容区域 CodeMirror.setValue()');
          contentCodeMirror.CodeMirror.setValue(cleanedContent);
          console.log('[Agent] 🎉 内容设置完成');
          return { success: true, contentLength: cleanedContent.length, method: 'ContentCodeMirror.setValue' };
        }
        
        // 如果没找到 bytemd-editor 内的，使用第一个有实例的
        for (const cm of allCodeMirrors) {
          if (cm.CodeMirror) {
            console.log('[Agent] ⚠️ 使用第一个可用的 CodeMirror');
            cm.CodeMirror.setValue(cleanedContent);
            return { success: true, contentLength: cleanedContent.length, method: 'FirstCodeMirror.setValue' };
          }
        }
      }
      
      // 特殊处理：CodeMirror-code 元素
      if (editor.classList && editor.classList.contains('CodeMirror-code')) {
        // 查找 CodeMirror 实例
        const cmWrapper = editor.closest('.CodeMirror');
        if (cmWrapper && cmWrapper.CodeMirror) {
          console.log('[Agent] 使用 CodeMirror 实例设置内容');
          cmWrapper.CodeMirror.setValue(cleanedContent);
          return { success: true, contentLength: cleanedContent.length, method: 'CodeMirror' };
        }
      }
      
      // 绝对保险：如果是标题输入框，但内容很长，拒绝输入
      if (editor.tagName === 'INPUT' && editor.placeholder && editor.placeholder.includes('输入文章标题') && cleanedContent.length > 100) {
        console.log('[Agent] 🚫 拒绝在标题框输入长内容:', cleanedContent.length, '字符');
        return { success: false, error: '拒绝在标题框输入长内容，请检查选择器' };
      }
      
      // 聚焦编辑器
      editor.focus();
      await new Promise(r => setTimeout(r, 50));
      
      // 清空内容
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      await new Promise(r => setTimeout(r, 50));
      
      // 输入内容 - 智能检测前台/后台
      if (typeDelay && Array.isArray(typeDelay) && !document.hidden) {
        // 前台标签页：逐字符输入 + 延迟（反风控）
        console.log('[Agent] 前台标签页，使用模拟打字');
        const [minDelay, maxDelay] = typeDelay;
        
        for (const char of cleanedContent) {
          document.execCommand('insertText', false, char);
          
          // 基础延迟
          let delay = minDelay + Math.random() * (maxDelay - minDelay);
          
          // 标点符号后稍微停顿
          if ('，。！？,.!?；;：:'.includes(char)) {
            delay += 50;
          }
          
          await new Promise(r => setTimeout(r, delay));
        }
      } else {
        // 后台标签页或无typeDelay：一次性输入（避免卡顿）
        console.log('[Agent] 后台标签页或无延迟，一次性输入');
        document.execCommand('insertText', false, cleanedContent);
      }
      
      // 触发 input 事件
      editor.dispatchEvent(new InputEvent('input', { 
        bubbles: true, 
        inputType: 'insertText',
        data: cleanedContent
      }));
      
      console.log('[Agent] fillRichEditor 完成, 长度:', cleanedContent.length);
      return { success: true, contentLength: cleanedContent.length };
    })();
    
    // 使用Promise.race实现超时
    return await Promise.race([fillPromise, timeoutPromise]);
    
  } catch (error) {
    // 如果是超时错误，返回失败
    if (error.message.includes('超时')) {
      console.warn('[Agent] fillRichEditor 超时，跳过此操作');
      return { success: false, error: 'fillRichEditor超时', skipped: true };
    }
    
    console.error('[Agent] fillRichEditor 错误:', error);
    return { success: false, error: error.message };
  }
}

// ========== 小红书话题函数 ==========

// 在小红书正文中添加话题
async function addXhsTopic(topic) {
  console.log('[Agent] addXhsTopic:', topic);
  
  // 找到编辑器
  const editor = document.querySelector('.tiptap.ProseMirror');
  if (!editor) {
    return { success: false, error: '找不到正文编辑器' };
  }
  
  // 聚焦编辑器
  editor.focus();
  
  // 移动光标到末尾
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false); // 移到末尾
  selection.removeAllRanges();
  selection.addRange(range);
  
  // 输入 #话题（逐字符输入触发下拉框）
  const hashTag = '#' + topic;
  for (const char of hashTag) {
    // 模拟键盘输入
    editor.dispatchEvent(new KeyboardEvent('keydown', { key: char, bubbles: true }));
    
    // 插入字符
    document.execCommand('insertText', false, char);
    
    editor.dispatchEvent(new KeyboardEvent('keyup', { key: char, bubbles: true }));
    
    await new Promise(r => setTimeout(r, 100));
  }
  
  // 等待下拉框出现
  await new Promise(r => setTimeout(r, 800));
  
  // 查找并点击第一个话题选项
  const topicItem = document.querySelector('#creator-editor-topic-container .item.is-selected') ||
                    document.querySelector('#creator-editor-topic-container .item');
  
  if (topicItem) {
    console.log('[Agent] 找到话题选项，点击选择');
    topicItem.click();
    return { success: true, topic };
  } else {
    console.log('[Agent] 未找到话题下拉框，话题文本已输入');
    return { success: true, topic, note: '话题文本已输入，但未找到下拉选项' };
  }
}

// ========== 抖音专用函数 ==========

// 添加抖音话题
async function addDouyinTopic(topic) {
  console.log('[Agent] addDouyinTopic:', topic);
  
  // 找到编辑器
  const editor = document.querySelector('div[data-slate-editor="true"]') ||
                 document.querySelector('div.editor-kit-editor-container[contenteditable="true"]');
  
  if (!editor) {
    return { success: false, error: '找不到抖音编辑器' };
  }
  
  // 聚焦编辑器
  editor.focus();
  await new Promise(r => setTimeout(r, 100));
  
  // 移到末尾
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
  
  // 先输入空格（确保和前面内容分开）
  document.execCommand('insertText', false, ' #');
  await new Promise(r => setTimeout(r, 150));
  
  // 输入话题名（逐字符输入触发下拉框）
  for (const char of topic) {
    document.execCommand('insertText', false, char);
    await new Promise(r => setTimeout(r, 100));
  }
  
  // 等待下拉框出现
  await new Promise(r => setTimeout(r, 1000));
  
  // 查找话题下拉框中的第一个选项
  const topicItem = document.querySelector('.mention-suggest-mount-dom div[class*="tag-dVUDkJ"]') ||
                    document.querySelector('.mention-suggest-mount-dom div[class*="tag-hash-o0tpyE"]');
  
  if (topicItem) {
    console.log('[Agent] 找到抖音话题选项，点击选择');
    topicItem.click();
    await new Promise(r => setTimeout(r, 500)); // 等待话题插入完成
    return { success: true, topic };
  } else {
    // 没找到下拉框，按空格确认话题文本
    document.execCommand('insertText', false, ' ');
    console.log('[Agent] 未找到话题下拉框，话题文本已输入');
    return { success: true, topic, note: '话题文本已输入，但未找到下拉选项' };
  }
}

// 添加B站标签
async function addBilibiliTag(tag) {
  console.log('[Agent] addBilibiliTag:', tag);
  
  // 找到B站标签输入框
  const tagInput = document.querySelector('input[placeholder*="标签"]') ||
                   document.querySelector('.tag-input input') ||
                   document.querySelector('input.tag-input');
  
  if (!tagInput) {
    return { success: false, error: '找不到B站标签输入框' };
  }
  
  // 聚焦输入框
  tagInput.focus();
  await new Promise(r => setTimeout(r, 100));
  
  // 输入标签
  tagInput.value = tag;
  
  // 触发输入事件
  tagInput.dispatchEvent(new Event('input', { bubbles: true }));
  tagInput.dispatchEvent(new Event('change', { bubbles: true }));
  
  await new Promise(r => setTimeout(r, 200));
  
  // 按回车确认标签
  tagInput.dispatchEvent(new KeyboardEvent('keydown', { 
    key: 'Enter', 
    code: 'Enter', 
    keyCode: 13,
    bubbles: true 
  }));
  
  await new Promise(r => setTimeout(r, 300));
  
  console.log('[Agent] B站标签已添加:', tag);
  return { success: true, tag };
}

// ========== 等待操作 ==========

// 等待元素出现（支持shadowRoot）
async function waitForSelector(selector, timeout = 10000, visible = true) {
  console.log('[Agent] waitForSelector 开始:', selector, 'timeout:', timeout, 'URL:', location.href);
  const startTime = Date.now();
  
  while (Date.now() - startTime < timeout) {
    const el = findElementWithShadowRoot(selector);
    if (el) {
      console.log('[Agent] waitForSelector 找到元素:', selector, el);
      if (!visible) {
        return { success: true, selector, found: true };
      }
      // 检查是否可见
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      const isVisible = rect.width > 0 && rect.height > 0 && 
                        style.display !== 'none' && 
                        style.visibility !== 'hidden' &&
                        style.opacity !== '0';
      console.log('[Agent] waitForSelector 可见性检查:', isVisible, 'rect:', rect.width, rect.height);
      if (isVisible) {
        return { success: true, selector, found: true, visible: true };
      }
    }
    await new Promise(r => setTimeout(r, 100));
  }
  
  console.log('[Agent] waitForSelector 超时:', selector, '耗时:', Date.now() - startTime, 'ms');
  return { success: false, error: `等待超时: ${selector}`, timeout };
}

// 检查元素是否存在（不等待）（支持shadowRoot）
function assertSelector(selector) {
  const el = findElementWithShadowRoot(selector);
  if (!el) {
    return { success: true, exists: false, selector };
  }
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const isVisible = rect.width > 0 && rect.height > 0 && 
                    style.display !== 'none' && 
                    style.visibility !== 'hidden';
  return { 
    success: true, 
    exists: true, 
    visible: isVisible,
    selector,
    tagName: el.tagName.toLowerCase()
  };
}

// 从 URL 上传图片到 <input type="file">（支持shadowRoot）
async function uploadImageFromUrl(imageUrl, selector = 'input.upload-input') {
  console.log('[Agent] uploadImageFromUrl 开始:', imageUrl, selector);
  
  try {
    // 1. 查找 input 元素
    const fileInput = findElementWithShadowRoot(selector);
    if (!fileInput) {
      return { success: false, error: `找不到文件输入框: ${selector}` };
    }
    
    console.log('[Agent] 找到文件输入框:', fileInput);
    
    // Fetch 图片
    console.log('[Agent] 正在下载图片...');
    const response = await fetch(imageUrl, {
      mode: 'cors',
      credentials: 'omit'
    });
    
    if (!response.ok) {
      return { success: false, error: `下载图片失败: ${response.status} ${response.statusText}` };
    }
    
    // 转换为 Blob
    const blob = await response.blob();
    console.log('[Agent] 图片下载完成, 大小:', blob.size, '类型:', blob.type);
    
    // 4. 从 URL 提取文件名
    const urlPath = new URL(imageUrl).pathname;
    const fileName = urlPath.split('/').pop() || 'image.jpg';
    
    // 5. 确定 MIME 类型
    let mimeType = blob.type;
    if (!mimeType || mimeType === 'application/octet-stream') {
      // 根据文件扩展名推断
      const ext = fileName.split('.').pop()?.toLowerCase();
      const mimeMap = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'webp': 'image/webp',
        'gif': 'image/gif'
      };
      mimeType = mimeMap[ext] || 'image/jpeg';
    }
    
    // 创建 File 对象
    const file = new File([blob], fileName, { type: mimeType });
    console.log('[Agent] 创建 File 对象:', file.name, file.size, file.type);
    
    // 使用 DataTransfer 设置文件
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    fileInput.files = dataTransfer.files;
    
    console.log('[Agent] 已设置 files:', fileInput.files.length, '个文件');
    
    // 触发 change 事件
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    fileInput.dispatchEvent(new Event('input', { bubbles: true }));
    
    console.log('[Agent] 已触发 change 和 input 事件');
    
    return { 
      success: true, 
      fileName: file.name,
      fileSize: file.size,
      fileType: file.type,
      message: '图片上传成功'
    };
    
  } catch (error) {
    console.error('[Agent] uploadImageFromUrl 错误:', error);
    return { success: false, error: error.message };
  }
}

// 下载图片并返回 base64（用于 CDP 上传）
async function downloadImageAsBase64(imageUrl) {
  console.log('[Agent] downloadImageAsBase64:', imageUrl);
  try {
    const response = await fetch(imageUrl, { mode: 'cors', credentials: 'omit' });
    if (!response.ok) {
      return { success: false, error: `下载失败: ${response.status}` };
    }
    
    const blob = await response.blob();
    const urlPath = new URL(imageUrl).pathname;
    const fileName = urlPath.split('/').pop() || 'image.jpg';
    
    // 转为 base64 data URL
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        resolve({
          success: true,
          base64: reader.result,
          fileName,
          size: blob.size,
          type: blob.type
        });
      };
      reader.onerror = () => {
        resolve({ success: false, error: '读取文件失败' });
      };
      reader.readAsDataURL(blob);
    });
  } catch (error) {
    console.error('[Agent] downloadImageAsBase64 错误:', error);
    return { success: false, error: error.message };
  }
}

// 从多个 URL 一次性上传多张图片（支持shadowRoot）
async function uploadMultipleImagesFromUrls(imageUrls, selector = 'input.upload-input') {
  console.log('[Agent] uploadMultipleImagesFromUrls 开始:', imageUrls.length, '张图片');
  
  try {
    // 1. 查找 input 元素
    const fileInput = findElementWithShadowRoot(selector);
    if (!fileInput) {
      return { success: false, error: `找不到文件输入框: ${selector}` };
    }
    
    console.log('[Agent] 找到文件输入框:', fileInput);
    
    // 2. 下载所有图片
    const files = [];
    for (let i = 0; i < imageUrls.length; i++) {
      const imageUrl = imageUrls[i];
      console.log(`[Agent] 下载图片 ${i + 1}/${imageUrls.length}: ${imageUrl}`);
      
      const response = await fetch(imageUrl, { mode: 'cors', credentials: 'omit' });
      if (!response.ok) {
        return { success: false, error: `图片 ${i + 1} 下载失败: ${response.status}` };
      }
      
      const blob = await response.blob();
      const urlPath = new URL(imageUrl).pathname;
      const fileName = urlPath.split('/').pop() || `image_${i + 1}.jpg`;
      
      let mimeType = blob.type;
      if (!mimeType || mimeType === 'application/octet-stream') {
        const ext = fileName.split('.').pop()?.toLowerCase();
        const mimeMap = { 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif' };
        mimeType = mimeMap[ext] || 'image/jpeg';
      }
      
      const file = new File([blob], fileName, { type: mimeType });
      files.push(file);
      console.log(`[Agent] 图片 ${i + 1} 准备完成: ${file.name}, ${file.size} bytes`);
    }
    
    // 3. 一次性设置所有文件
    const dataTransfer = new DataTransfer();
    files.forEach(file => dataTransfer.items.add(file));
    fileInput.files = dataTransfer.files;
    
    console.log('[Agent] 已设置 files:', fileInput.files.length, '个文件');
    
    // 4. 触发 change 事件
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    fileInput.dispatchEvent(new Event('input', { bubbles: true }));
    
    console.log('[Agent] 已触发 change 和 input 事件');
    
    return { 
      success: true, 
      count: files.length,
      message: `成功上传 ${files.length} 张图片`
    };
    
  } catch (error) {
    console.error('[Agent] uploadMultipleImagesFromUrls 错误:', error);
    return { success: false, error: error.message };
  }
}

// ========== 页面预处理（Smart Rules） ==========

// 检测是否是错误页
function detectErrorPage() {
  // Chrome 原生错误页
  if (document.querySelector('error-page-controller')) {
    return { isError: true, type: 'chrome_error' };
  }
  
  // 标题包含错误码
  const title = document.title.toLowerCase();
  if (/404|500|502|503|not found|服务器错误|页面不存在/.test(title)) {
    return { isError: true, type: 'title_error', title: document.title };
  }
  
  // 内容很少且包含错误关键词
  const bodyText = document.body?.innerText || '';
  if (bodyText.length < 500) {
    if (/404|not found|页面不存在|已删除|出错了|服务器错误|无法访问/.test(bodyText)) {
      return { isError: true, type: 'content_error' };
    }
  }
  
  return { isError: false };
}

// 查找标准 Cookie 同意按钮
function findCookieButton() {
  // 知名 Cookie 库的固定选择器
  const standardSelectors = [
    '#onetrust-accept-btn-handler',           // OneTrust
    '#CybotCookiebotDialogBodyButtonAccept',  // Cookiebot
    '.cc-accept',                              // CookieConsent
    '.cc-btn.cc-allow',                        // CookieConsent 变体
    '[data-cookiebanner="accept_button"]',
    '.cookie-accept',
    '#accept-cookies',
    '[data-testid="cookie-accept"]',
    '.accept-cookies-button',
    '#cookie-accept-btn',
    '.js-cookie-consent-agree',
    '[data-action="accept-cookies"]',
  ];
  
  for (const selector of standardSelectors) {
    const btn = document.querySelector(selector);
    if (btn && btn.offsetParent !== null) {
      return { found: true, selector, element: btn };
    }
  }
  
  return { found: false };
}

// 检测验证码
function detectCaptcha() {
  // 知名验证码库的固定选择器
  const captchaSelectors = [
    '#captcha',
    '.geetest_holder',           // 极验
    '.geetest_panel',            // 极验面板
    '.nc_wrapper',               // 阿里滑块
    '.nc-container',             // 阿里滑块
    '.JDJRV-slide',              // 京东
    '.tcaptcha-iframe',          // 腾讯
    '.tcaptcha-popup',           // 腾讯弹窗
    'iframe[src*="recaptcha"]',  // Google reCAPTCHA
    'iframe[src*="hcaptcha"]',   // hCaptcha
    '.g-recaptcha',              // Google reCAPTCHA
    '.h-captcha',                // hCaptcha
    '[class*="captcha"]:not(script):not(style)',
    '[id*="captcha"]:not(script):not(style)',
    '[class*="slider-verify"]',  // 滑块验证
    '[class*="slide-verify"]',
  ];
  
  for (const sel of captchaSelectors) {
    try {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) {
        // 额外检查：元素要有一定大小
        const rect = el.getBoundingClientRect();
        if (rect.width > 50 && rect.height > 30) {
          return { hasCaptcha: true, type: sel };
        }
      }
    } catch (e) {}
  }
  
  return { hasCaptcha: false };
}

// 检测登录弹窗
function detectLoginPopup() {
  // 检测 fixed/absolute 定位的弹窗内是否有登录表单
  const modalSelectors = [
    '[class*="modal"]',
    '[class*="dialog"]',
    '[class*="popup"]',
    '[role="dialog"]',
    '[class*="login-box"]',
    '[class*="login-panel"]',
  ];
  
  for (const sel of modalSelectors) {
    try {
      const modals = document.querySelectorAll(sel);
      
      for (const modal of modals) {
        const style = getComputedStyle(modal);
        
        // 必须是浮动定位且可见
        if (!['fixed', 'absolute'].includes(style.position)) continue;
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        
        // 检查是否有密码输入框（登录的标志）
        const hasPassword = modal.querySelector('input[type="password"]');
        if (hasPassword) {
          // 检查是否有关闭按钮（可关闭的弹窗）
          const closeBtn = modal.querySelector('[class*="close"], [aria-label*="关闭"], [aria-label*="Close"]');
          return { 
            hasLoginPopup: true, 
            canClose: !!closeBtn,
            selector: sel 
          };
        }
      }
    } catch (e) {}
  }
  
  return { hasLoginPopup: false };
}

// 查找遮罩层关闭按钮
function findOverlayCloseButton() {
  // 找 fixed 定位的遮罩层
  const overlaySelectors = [
    '[class*="overlay"]',
    '[class*="modal"]',
    '[class*="popup"]',
    '[class*="dialog"]',
    '[role="dialog"]',
  ];
  
  for (const selector of overlaySelectors) {
    const overlays = document.querySelectorAll(selector);
    
    for (const overlay of overlays) {
      const style = getComputedStyle(overlay);
      
      // 必须是 fixed/absolute 定位且可见
      if (!['fixed', 'absolute'].includes(style.position)) continue;
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      if (parseFloat(style.opacity) === 0) continue;
      
      // 检查是否覆盖了大部分屏幕（避免误判小弹窗）
      const rect = overlay.getBoundingClientRect();
      const screenCoverage = (rect.width * rect.height) / (window.innerWidth * window.innerHeight);
      if (screenCoverage < 0.3) continue;  // 覆盖面积小于 30%，跳过
      
      // 找关闭按钮
      const closeSelectors = [
        '[class*="close"]',
        '[aria-label*="关闭"]',
        '[aria-label*="Close"]',
        '[aria-label*="close"]',
        '.icon-close',
        '.btn-close',
        'button[class*="dismiss"]',
        '[data-dismiss]',
      ];
      
      for (const closeSel of closeSelectors) {
        const closeBtn = overlay.querySelector(closeSel);
        if (closeBtn && closeBtn.offsetParent !== null) {
          // 确保是按钮类元素
          const tag = closeBtn.tagName.toLowerCase();
          if (['button', 'a', 'span', 'div', 'i', 'svg'].includes(tag)) {
            return { found: true, selector: closeSel, element: closeBtn, overlaySelector: selector };
          }
        }
      }
    }
  }
  
  return { found: false };
}

// 执行页面预处理
async function runSmartRules(config) {
  const results = {
    handled: false,
    actions: [],
    hints: {}  // 检测提示（不自动处理，只告诉 AI）
  };
  
  if (!config || !config.ENABLED) {
    return results;
  }
  
  // 1. 检测错误页
  if (config.AUTO_HANDLE_ERROR) {
    const errorCheck = detectErrorPage();
    if (errorCheck.isError) {
      results.handled = true;
      results.isErrorPage = true;
      results.errorType = errorCheck.type;
      results.actions.push({ type: 'error_detected', detail: errorCheck });
      console.log('[Agent] 🚫 检测到错误页:', errorCheck.type);
      return results;  // 错误页直接返回，不继续处理
    }
  }
  
  // 2. 自动点击 Cookie 同意按钮
  if (config.AUTO_ACCEPT_COOKIE) {
    const cookieCheck = findCookieButton();
    if (cookieCheck.found) {
      try {
        cookieCheck.element.click();
        results.actions.push({ type: 'cookie_accepted', selector: cookieCheck.selector });
        console.log('[Agent] 🍪 自动点击 Cookie 同意按钮:', cookieCheck.selector);
        await new Promise(r => setTimeout(r, 300));  // 等待弹窗关闭
      } catch (e) {
        console.warn('[Agent] Cookie 按钮点击失败:', e.message);
      }
    }
  }
  
  // 3. 自动关闭遮罩层弹窗
  if (config.AUTO_CLOSE_POPUP) {
    const overlayCheck = findOverlayCloseButton();
    if (overlayCheck.found) {
      try {
        overlayCheck.element.click();
        results.actions.push({ type: 'popup_closed', selector: overlayCheck.selector });
        console.log('[Agent] ❌ 自动关闭遮罩层弹窗:', overlayCheck.selector);
        await new Promise(r => setTimeout(r, 300));  // 等待弹窗关闭
      } catch (e) {
        console.warn('[Agent] 关闭按钮点击失败:', e.message);
      }
    }
  }
  
  // 4. 检测验证码（只检测，不处理）
  const captchaCheck = detectCaptcha();
  if (captchaCheck.hasCaptcha) {
    results.hints.hasCaptcha = true;
    results.hints.captchaType = captchaCheck.type;
    console.log('[Agent] 🔐 检测到验证码:', captchaCheck.type);
  }
  
  // 5. 检测登录弹窗（只检测，不处理）
  const loginCheck = detectLoginPopup();
  if (loginCheck.hasLoginPopup) {
    results.hints.hasLoginPopup = true;
    results.hints.loginCanClose = loginCheck.canClose;
    console.log('[Agent] 🔑 检测到登录弹窗, 可关闭:', loginCheck.canClose);
  }
  
  results.handled = results.actions.length > 0;
  return results;
}

// 暴露给 background.js 调用
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'runSmartRules') {
    runSmartRules(message.config)
      .then(result => sendResponse(result))
      .catch(e => sendResponse({ handled: false, error: e.message }));
    return true;
  }
});

// 专门用于点击微信视频号发表按钮
async function clickWechatPublishButton() {
  console.log('[Agent] clickWechatPublishButton 开始');
  
  const wujieApp = document.querySelector('wujie-app');
  if (!wujieApp || !wujieApp.shadowRoot) {
    return { success: false, error: '未找到wujie-app或shadowRoot' };
  }
  
  // 查找发表按钮
  const buttons = wujieApp.shadowRoot.querySelectorAll('button.weui-desktop-btn_primary');
  let publishButton = null;
  
  for (const button of buttons) {
    if (button.textContent && button.textContent.trim() === '发表') {
      const rect = button.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        publishButton = button;
        break;
      }
    }
  }
  
  if (!publishButton) {
    return { success: false, error: '未找到发表按钮' };
  }
  
  console.log('[Agent] 找到发表按钮，准备点击:', publishButton);
  
  // 滚动到按钮位置
  publishButton.scrollIntoView({ behavior: 'instant', block: 'center' });
  await new Promise(r => setTimeout(r, 500));
  
  // 点击按钮
  publishButton.click();
  
  console.log('[Agent] 发表按钮已点击');
  return { success: true, message: '发表按钮点击成功' };
}