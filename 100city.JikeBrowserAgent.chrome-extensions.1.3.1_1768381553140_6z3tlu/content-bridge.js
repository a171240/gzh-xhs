// content-bridge.js - 注入到 100city 网站，负责与网页通信

(function() {
  'use strict';
  
  const EXTENSION_SOURCE = 'geek-growth-extension';
  const APP_SOURCE = 'geek-growth-app';
  
  console.log('[极客增长] Content Bridge 已加载');
  
  // 监听网页发来的消息
  window.addEventListener('message', async (event) => {
    if (event.source !== window) return;
    
    const data = event.data;
    if (!data) return;
    
    // 兼容两种消息格式：
    // 格式1（官网 SDK）: { source: 'geek-growth-app', type: 'execute', action, params }
    // 格式2（agent-sdk.js）: { target: 'browser-agent-extension', action, params }
    
    // 格式1：官网 SDK
    if (data.source === APP_SOURCE) {
      console.log('[极客增长] 收到官网消息:', data.type);
      
      switch (data.type) {
        case 'ping':
          handlePing(data);
          break;
        case 'execute':
          handleExecute(data);
          break;
        default:
          console.log('[极客增长] 未知消息类型:', data.type);
      }
      return;
    }
    
    // 格式2：agent-sdk.js
    if (data.target === 'browser-agent-extension') {
      console.log('[极客增长] 收到 SDK 消息:', data.action);
      handleExecute({
        action: data.action,
        params: data.params || {},
        requestId: data.requestId
      });
      return;
    }
  });
  
  // 响应 ping 检测
  function handlePing(data) {
    window.postMessage({
      source: EXTENSION_SOURCE,
      type: 'pong',
      requestId: data.requestId,
      version: chrome.runtime.getManifest().version,
      ready: true
    }, '*');
    console.log('[极客增长] 已响应 ping 检测');
  }
  
  // 执行操作指令
  async function handleExecute(data) {
    const { action, params = {}, requestId } = data;
    
    try {
      const messageToSend = { type: 'execute', action, params };
      console.log('[极客增长] 发送给 background:', messageToSend);
      
      const response = await chrome.runtime.sendMessage(messageToSend);
      
      // 返回执行结果给网页（兼容两种格式）
      window.postMessage({
        target: 'browser-agent-web',
        source: EXTENSION_SOURCE,
        type: 'executeResult',
        requestId,
        success: response?.success ?? false,
        data: response,
        result: response
      }, '*');
      
    } catch (error) {
      console.error('[极客增长] 执行失败:', error);
      window.postMessage({
        target: 'browser-agent-web',
        source: EXTENSION_SOURCE,
        type: 'executeResult',
        requestId,
        success: false,
        error: error.message
      }, '*');
    }
  }
  
  // 发送 ready 消息
  function sendReadyMessage() {
    window.postMessage({
      target: 'browser-agent-web',
      source: EXTENSION_SOURCE,
      type: 'ready',
      version: chrome.runtime.getManifest().version
    }, '*');
    console.log('[极客增长] 已发送 ready 消息');
  }
  
  // 通知网页插件已就绪
  sendReadyMessage();
  setTimeout(sendReadyMessage, 100);
  setTimeout(sendReadyMessage, 500);
  setTimeout(sendReadyMessage, 1000);
  
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sendReadyMessage);
  }
  window.addEventListener('load', sendReadyMessage);
  
})();
