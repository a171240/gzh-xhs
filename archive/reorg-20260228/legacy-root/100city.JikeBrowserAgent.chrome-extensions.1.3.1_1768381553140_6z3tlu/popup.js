// popup.js - 极客增长浏览器助手

// 配置（与 config.js 保持一致）
const POPUP_CONFIG = {
  SITE_URL: 'https://100.city'
};

// DOM 元素
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const newChatBtn = document.getElementById('newChat');
const versionEl = document.getElementById('version');

// 显示版本号
versionEl.textContent = 'v' + chrome.runtime.getManifest().version;

// 获取当前状态
async function updateStatus() {
  try {
    const response = await chrome.runtime.sendMessage({ type: 'getStatus' });
    
    if (response?.status === 'running') {
      statusDot.classList.add('running');
      statusText.textContent = '运行中';
    } else {
      statusDot.classList.remove('running');
      statusText.textContent = '空闲';
    }
  } catch (e) {
    // 默认空闲状态
    statusDot.classList.remove('running');
    statusText.textContent = '空闲';
  }
}

// 开启新对话
newChatBtn.addEventListener('click', () => {
  // 打开主站对话页面
  chrome.tabs.create({ url: POPUP_CONFIG.SITE_URL + '/cityChat' });
  window.close();
});

// 初始化
updateStatus();

// 监听状态变化
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'statusChanged') {
    updateStatus();
  }
});
