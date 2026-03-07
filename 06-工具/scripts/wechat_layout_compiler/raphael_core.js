"use strict";

const { pathToFileURL } = require("url");
const MarkdownIt = require("markdown-it");
const hljs = require("highlight.js");
const { JSDOM } = require("jsdom");
const { getTheme } = require("./raphael_themes");

const md = new MarkdownIt({
  html: true,
  breaks: true,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      return `<pre><code class="hljs">${hljs.highlight(code, { language }).value}</code></pre>`;
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(code)}</code></pre>`;
  },
});

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function extractCssValue(styleText, property) {
  const text = String(styleText || "");
  const match = text.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, "i"));
  return match ? String(match[1] || "").trim() : "";
}

function extractBorderLeftColor(styleText) {
  const text = String(styleText || "");
  const match = text.match(/border-left\s*:\s*[^;]*\s(#(?:[0-9a-f]{3,8}))/i);
  return match ? String(match[1] || "").trim() : "";
}

function hexToRgba(color, alpha) {
  const raw = String(color || "").trim();
  const match = raw.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) {
    return "";
  }
  const hex = match[1].length === 3
    ? match[1].split("").map((item) => item + item).join("")
    : match[1];
  const r = Number.parseInt(hex.slice(0, 2), 16);
  const g = Number.parseInt(hex.slice(2, 4), 16);
  const b = Number.parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function preprocessMarkdown(content) {
  let text = String(content || "");
  text = text.replace(/\r\n/g, "\n");
  text = text.replace(/^[ ]{0,3}(\*[ ]*\*[ ]*\*[\* ]*)[ \t]*$/gm, "***");
  text = text.replace(/^[ ]{0,3}(-[ ]*-[ ]*-[- ]*)[ \t]*$/gm, "---");
  text = text.replace(/^[ ]{0,3}(_[ ]*_[ ]*_[_ ]*)[ \t]*$/gm, "___");
  text = text.replace(/\*\*[ \t]+\*\*/g, " ");
  text = text.replace(/\*{4,}/g, "");
  text = text.replace(
    /([^\s])\*\*([+\-＋－%％~～!！?？,，.。:：;；、\\/|@#￥$^&*_=（）()【】\[\]《》〈〉「」『』“”"'`…·][^\n*]*?)\*\*/g,
    "$1**\u200B$2**"
  );
  return text.trim();
}

function getThemeTokens(themeId) {
  const theme = getTheme(themeId);
  const background = extractCssValue(theme.styles.container, "background-color") || "#ffffff";
  const text = extractCssValue(theme.styles.p, "color")
    || extractCssValue(theme.styles.container, "color")
    || "#1f2328";
  const accent = extractCssValue(theme.styles.a, "color")
    || extractCssValue(theme.styles.h2, "color")
    || "#0969da";
  const divider = extractCssValue(theme.styles.hr, "background-color") || hexToRgba(accent, 0.18) || "#d0d7de";
  const quoteBackground = extractCssValue(theme.styles.blockquote, "background-color") || hexToRgba(accent, 0.08) || "#f8fafc";
  const quoteBorder = extractBorderLeftColor(theme.styles.blockquote) || accent;
  const accentSoft = hexToRgba(accent, 0.08) || quoteBackground;
  return {
    background,
    text,
    accent,
    accentSoft,
    divider,
    quoteBackground,
    quoteBorder,
  };
}

function decorateImageGrids(document) {
  const isSingleImageParagraph = (node) => {
    if (!node || node.tagName !== "P") {
      return false;
    }
    const children = Array.from(node.childNodes).filter((child) => {
      if (child.nodeType === 3) {
        return String(child.textContent || "").trim();
      }
      if (child.nodeType === 1 && child.tagName === "BR") {
        return false;
      }
      return true;
    });
    if (children.length !== 1) {
      return false;
    }
    const onlyChild = children[0];
    if (onlyChild.nodeName === "IMG") {
      return true;
    }
    return (
      onlyChild.nodeName === "A"
      && onlyChild.childNodes.length === 1
      && onlyChild.childNodes[0].nodeName === "IMG"
    );
  };

  const paragraphs = Array.from(document.querySelectorAll("p"));
  for (const paragraph of paragraphs) {
    if (!paragraph.isConnected || !isSingleImageParagraph(paragraph)) {
      continue;
    }
    const run = [paragraph];
    let cursor = paragraph.nextElementSibling;
    while (cursor && cursor.tagName === "P" && isSingleImageParagraph(cursor)) {
      run.push(cursor);
      cursor = cursor.nextElementSibling;
    }
    if (run.length < 2) {
      continue;
    }
    for (let index = 0; index + 1 < run.length; index += 2) {
      const first = run[index];
      const second = run[index + 1];
      if (!first.isConnected || !second.isConnected) {
        continue;
      }
      const wrapper = document.createElement("p");
      wrapper.classList.add("image-grid");
      wrapper.setAttribute("style", "display: flex; justify-content: center; gap: 8px; margin: 24px 0; align-items: flex-start;");
      wrapper.appendChild(first.firstElementChild || first.firstChild);
      wrapper.appendChild(second.firstElementChild || second.firstChild);
      first.before(wrapper);
      first.remove();
      second.remove();
    }
  }
}

function applyTheme(fragmentHtml, themeId) {
  const theme = getTheme(themeId);
  const dom = new JSDOM(`<body>${String(fragmentHtml || "")}</body>`);
  const { document } = dom.window;
  const styles = theme.styles || {};

  decorateImageGrids(document);

  Object.entries(styles).forEach(([selector, styleText]) => {
    if (!styleText || selector === "container" || selector === "pre code") {
      return;
    }
    document.querySelectorAll(selector).forEach((node) => {
      if (selector === "code" && node.parentElement && node.parentElement.tagName === "PRE") {
        return;
      }
      if (node.tagName === "IMG" && node.closest(".image-grid")) {
        return;
      }
      const current = String(node.getAttribute("style") || "").trim();
      node.setAttribute("style", current ? `${current}; ${styleText}` : styleText);
    });
  });

  document.querySelectorAll("ul").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", `${current}; list-style-type: disc !important; list-style-position: outside;`);
  });
  document.querySelectorAll("ul ul").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", `${current}; list-style-type: circle !important;`);
  });
  document.querySelectorAll("ul ul ul").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", `${current}; list-style-type: square !important;`);
  });
  document.querySelectorAll("ol").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", `${current}; list-style-type: decimal !important; list-style-position: outside;`);
  });

  const tokenStyles = {
    "hljs-comment": "color: #6a737d; font-style: italic;",
    "hljs-quote": "color: #6a737d; font-style: italic;",
    "hljs-keyword": "color: #d73a49; font-weight: 600;",
    "hljs-selector-tag": "color: #d73a49; font-weight: 600;",
    "hljs-string": "color: #032f62;",
    "hljs-title": "color: #6f42c1; font-weight: 600;",
    "hljs-section": "color: #6f42c1; font-weight: 600;",
    "hljs-type": "color: #005cc5; font-weight: 600;",
    "hljs-number": "color: #005cc5;",
    "hljs-literal": "color: #005cc5;",
    "hljs-built_in": "color: #005cc5;",
    "hljs-variable": "color: #e36209;",
    "hljs-template-variable": "color: #e36209;",
    "hljs-tag": "color: #22863a;",
    "hljs-name": "color: #22863a;",
    "hljs-attr": "color: #6f42c1;",
  };
  document.querySelectorAll(".hljs span").forEach((node) => {
    let merged = String(node.getAttribute("style") || "").trim();
    node.classList.forEach((className) => {
      if (tokenStyles[className]) {
        merged = merged ? `${merged}; ${tokenStyles[className]}` : tokenStyles[className];
      }
    });
    if (merged) {
      node.setAttribute("style", merged);
    }
  });

  document.querySelectorAll("img").forEach((node) => {
    const inGrid = Boolean(node.closest(".image-grid"));
    const current = String(node.getAttribute("style") || "").trim();
    const extra = inGrid
      ? "display:block; max-width:100%; height:auto; margin:0 !important; border-radius:14px !important; box-sizing:border-box;"
      : "display:block; width:100%; max-width:100%; height:auto; margin:28px auto !important; border-radius:14px !important; box-sizing:border-box;";
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  return document.body.innerHTML.trim();
}

function renderMarkdownFragment(markdown, themeId) {
  const html = md.render(preprocessMarkdown(markdown));
  return applyTheme(html, themeId);
}

function renderImageFragment(imagePath, alt, themeId) {
  const tokens = getThemeTokens(themeId);
  const caption = String(alt || "").trim();
  const captionHtml = caption
    ? `<figcaption style="margin:10px 0 0; font-size:13px; line-height:1.6; color:${tokens.accent}; text-align:center;">${escapeHtml(caption)}</figcaption>`
    : "";
  return [
    '<figure style="margin:28px 0; padding:0; border:0;">',
    `<img src="${pathToFileURL(imagePath).href}" alt="${escapeHtml(caption || "正文配图")}" style="display:block; width:100%; max-width:100%; height:auto; border-radius:14px;" />`,
    captionHtml,
    "</figure>",
  ].join("");
}

function makeWeChatCompatible(html, themeId) {
  const theme = getTheme(themeId);
  const dom = new JSDOM(`<body>${String(html || "")}</body>`);
  const { document } = dom.window;
  const section = document.createElement("section");
  section.setAttribute("style", theme.styles.container || "");

  Array.from(document.body.childNodes).forEach((node) => {
    section.appendChild(node);
  });
  document.body.innerHTML = "";
  document.body.appendChild(section);

  Array.from(section.querySelectorAll("div, p.image-grid")).forEach((node) => {
    const style = String(node.getAttribute("style") || "");
    const isFlexNode = style.includes("display: flex") || style.includes("display:flex");
    const isImageGrid = node.classList.contains("image-grid");
    if (!isFlexNode && !isImageGrid) {
      return;
    }
    const children = Array.from(node.children);
    if (!children.length || !children.every((child) => child.tagName === "IMG" || child.querySelector("img"))) {
      if (isFlexNode) {
        node.setAttribute("style", style.replace(/display:\s*flex;?/gi, "display: block;"));
      }
      return;
    }
    const table = document.createElement("table");
    table.setAttribute("style", "width: 100%; border-collapse: collapse; margin: 16px 0; border: none !important;");
    const tbody = document.createElement("tbody");
    const tr = document.createElement("tr");
    tr.setAttribute("style", "border: none !important; background: transparent !important;");
    children.forEach((child) => {
      const td = document.createElement("td");
      td.setAttribute("style", "padding: 0 4px; vertical-align: top; border: none !important; background: transparent !important;");
      td.appendChild(child);
      if (child.tagName === "IMG") {
        const current = String(child.getAttribute("style") || "");
        child.setAttribute("style", `${current.replace(/width:\s*[^;]+;?/gi, "")} width: 100% !important; display: block; margin: 0 auto;`);
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
    table.appendChild(tbody);
    node.parentNode.replaceChild(table, node);
  });

  const fontFamily = extractCssValue(theme.styles.container, "font-family");
  const fontSize = extractCssValue(theme.styles.container, "font-size");
  const lineHeight = extractCssValue(theme.styles.container, "line-height");
  const color = extractCssValue(theme.styles.container, "color");

  section.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, blockquote, span").forEach((node) => {
    if (node.tagName === "SPAN" && node.closest("pre, code")) {
      return;
    }
    let current = String(node.getAttribute("style") || "").trim();
    if (fontFamily && !current.includes("font-family:")) {
      current = `${current}; font-family: ${fontFamily};`;
    }
    if (lineHeight && !current.includes("line-height:")) {
      current = `${current}; line-height: ${lineHeight};`;
    }
    if (fontSize && !current.includes("font-size:") && ["P", "LI", "BLOCKQUOTE", "SPAN"].includes(node.tagName)) {
      current = `${current}; font-size: ${fontSize};`;
    }
    if (color && !current.includes("color:")) {
      current = `${current}; color: ${color};`;
    }
    if (!current.includes("font-weight:") && ["P", "LI", "BLOCKQUOTE", "SPAN"].includes(node.tagName)) {
      current = `${current}; font-weight: 400 !important;`;
    }
    node.setAttribute("style", current.trim());
  });

  let output = document.body.innerHTML;
  output = output.replace(/(<\/(?:strong|b|em|span|a|code)>)\s*([：；，。！？、])/g, "$1\u2060$2");
  return output;
}

module.exports = {
  escapeHtml,
  getThemeTokens,
  makeWeChatCompatible,
  preprocessMarkdown,
  renderImageFragment,
  renderMarkdownFragment,
};
