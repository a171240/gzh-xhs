#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const MarkdownIt = require("markdown-it");
const hljs = require("highlight.js");
const { JSDOM } = require("jsdom");

const THEMES = {
  notion: {
    name: "notion",
    background: "#ffffff",
    pageBackground: "#f5f6f8",
    text: "#1f2328",
    muted: "#667085",
    accent: "#0969da",
    accentSoft: "#eef6ff",
    accentBorder: "#bfdbfe",
    ctaBackground: "#0f172a",
    ctaText: "#f8fafc",
    quoteBackground: "#f8fafc",
    quoteBorder: "#cbd5e1",
    codeBackground: "#f6f8fa",
    divider: "#e5e7eb",
  },
  github: {
    name: "github",
    background: "#ffffff",
    pageBackground: "#f6f8fa",
    text: "#24292f",
    muted: "#57606a",
    accent: "#0969da",
    accentSoft: "#edf6ff",
    accentBorder: "#b6d4fe",
    ctaBackground: "#0d1117",
    ctaText: "#f0f6fc",
    quoteBackground: "#f6f8fa",
    quoteBorder: "#d0d7de",
    codeBackground: "#f6f8fa",
    divider: "#d0d7de",
  },
  sspai: {
    name: "sspai",
    background: "#ffffff",
    pageBackground: "#eef2f7",
    text: "#1b1f24",
    muted: "#5b6472",
    accent: "#0070f3",
    accentSoft: "#f4f8ff",
    accentBorder: "#d6e4ff",
    ctaBackground: "#111827",
    ctaText: "#f9fafb",
    quoteBackground: "#f8fbff",
    quoteBorder: "#c7d9ff",
    codeBackground: "#f6f8fb",
    divider: "#e5e7eb",
  },
  sunset: {
    name: "sunset",
    background: "#fffdf9",
    pageBackground: "#fff6ef",
    text: "#42210b",
    muted: "#8a5a44",
    accent: "#ea580c",
    accentSoft: "#fff1e8",
    accentBorder: "#fed7aa",
    ctaBackground: "#7c2d12",
    ctaText: "#fff7ed",
    quoteBackground: "#fff7ed",
    quoteBorder: "#fdba74",
    codeBackground: "#fff3e8",
    divider: "#fed7aa",
  },
  mint: {
    name: "mint",
    background: "#fbfffd",
    pageBackground: "#eefaf5",
    text: "#16342b",
    muted: "#52756a",
    accent: "#0f766e",
    accentSoft: "#edfdfa",
    accentBorder: "#99f6e4",
    ctaBackground: "#134e4a",
    ctaText: "#f0fdfa",
    quoteBackground: "#f0fdfa",
    quoteBorder: "#99f6e4",
    codeBackground: "#effcf7",
    divider: "#cdeee1",
  },
};

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      return `<pre><code>${hljs.highlight(code, { language }).value}</code></pre>`;
    }
    return `<pre><code>${md.utils.escapeHtml(code)}</code></pre>`;
  },
});

function parseArgs(argv) {
  const out = { input: "" };
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (item === "--input") {
      out.input = argv[i + 1] || "";
      i += 1;
    }
  }
  if (!out.input) {
    throw new Error("missing --input");
  }
  return out;
}

function preprocessMarkdown(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function splitBodyBlocks(bodyMarkdown, articleDir) {
  const lines = String(bodyMarkdown || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  const buffer = [];
  const imageLineRe = /^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$/;

  function flushBuffer() {
    const content = buffer.join("\n").trim();
    if (!content) {
      buffer.length = 0;
      return;
    }
    blocks.push({ type: "markdown", markdown: content });
    buffer.length = 0;
  }

  lines.forEach((line) => {
    const match = line.match(imageLineRe);
    if (!match) {
      buffer.push(line);
      return;
    }
    flushBuffer();
    const alt = String(match[1] || "").trim();
    const rawPath = String(match[2] || "").trim();
    const resolved = path.isAbsolute(rawPath)
      ? rawPath
      : path.resolve(articleDir, rawPath);
    blocks.push({
      type: "image",
      alt: alt || "正文配图",
      rawPath,
      path: resolved,
    });
  });

  flushBuffer();
  return blocks;
}

function applyTheme(fragment, theme) {
  const dom = new JSDOM(`<body>${fragment}</body>`);
  const { document } = dom.window;

  const blockSpacing = "margin:0 0 16px;";
  const bodyText = `${blockSpacing}color:${theme.text};font-size:16px;line-height:1.9;letter-spacing:0.15px;`;

  document.querySelectorAll("p").forEach((node) => {
    node.setAttribute("style", bodyText);
  });
  document.querySelectorAll("ul,ol").forEach((node) => {
    node.setAttribute(
      "style",
      `${blockSpacing}padding-left:24px;color:${theme.text};font-size:16px;line-height:1.9;`
    );
  });
  document.querySelectorAll("li").forEach((node) => {
    node.setAttribute("style", "margin:0 0 10px;");
  });
  document.querySelectorAll("strong").forEach((node) => {
    node.setAttribute("style", `color:${theme.text};font-weight:700;`);
  });
  document.querySelectorAll("em").forEach((node) => {
    node.setAttribute("style", `color:${theme.muted};font-style:italic;`);
  });
  document.querySelectorAll("a").forEach((node) => {
    node.setAttribute(
      "style",
      `color:${theme.accent};text-decoration:none;border-bottom:1px solid ${theme.accentBorder};`
    );
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  });
  document.querySelectorAll("blockquote").forEach((node) => {
    node.setAttribute(
      "style",
      `margin:22px 0;padding:14px 16px;border-left:4px solid ${theme.accent};background:${theme.quoteBackground};color:${theme.text};border-radius:8px;`
    );
  });
  document.querySelectorAll("pre").forEach((node) => {
    node.setAttribute(
      "style",
      `margin:18px 0;padding:14px 16px;background:${theme.codeBackground};border-radius:12px;overflow:auto;`
    );
  });
  document.querySelectorAll("code").forEach((node) => {
    const isInline = node.parentElement && node.parentElement.tagName !== "PRE";
    if (isInline) {
      node.setAttribute(
        "style",
        `padding:2px 6px;background:${theme.codeBackground};border-radius:6px;color:${theme.text};font-size:0.92em;`
      );
    } else {
      node.setAttribute("style", `color:${theme.text};font-size:14px;line-height:1.7;`);
    }
  });
  document.querySelectorAll("hr").forEach((node) => {
    node.setAttribute("style", `border:none;border-top:1px solid ${theme.divider};margin:28px 0;`);
  });
  document.querySelectorAll("h3").forEach((node) => {
    node.setAttribute(
      "style",
      `margin:28px 0 14px;padding-left:12px;border-left:4px solid ${theme.accent};font-size:20px;line-height:1.55;color:${theme.text};`
    );
  });
  document.querySelectorAll("h4").forEach((node) => {
    node.setAttribute(
      "style",
      `margin:22px 0 12px;font-size:18px;line-height:1.55;color:${theme.text};`
    );
  });

  return document.body.innerHTML.trim();
}

function renderMarkdownBlock(markdown, theme) {
  const html = md.render(preprocessMarkdown(markdown));
  return applyTheme(html, theme);
}

function renderSummaryCard(summary, theme) {
  return [
    `<section style="margin:0 0 24px;padding:18px 20px;border:1px solid ${theme.accentBorder};border-radius:16px;background:${theme.accentSoft};">`,
    `<div style="margin:0 0 8px;font-size:13px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:${theme.accent};">文章摘要</div>`,
    `<p style="margin:0;color:${theme.text};font-size:15px;line-height:1.85;">${escapeHtml(String(summary || "").trim())}</p>`,
    "</section>",
  ].join("");
}

function renderCtaCard(markdown, theme) {
  const inner = renderMarkdownBlock(markdown, {
    ...theme,
    text: theme.ctaText,
    muted: theme.ctaText,
    accent: "#93c5fd",
    accentBorder: "rgba(147,197,253,0.45)",
    quoteBackground: "rgba(255,255,255,0.08)",
    codeBackground: "rgba(255,255,255,0.08)",
    divider: "rgba(255,255,255,0.18)",
  });
  return [
    `<section style="margin:32px 0 0;padding:18px 20px;border-radius:16px;background:${theme.ctaBackground};color:${theme.ctaText};">`,
    `<div style="margin:0 0 10px;font-size:13px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#93c5fd;">行动建议</div>`,
    inner,
    "</section>",
  ].join("");
}

function renderImageBlock(imagePath, alt) {
  return [
    '<figure style="margin:24px 0;">',
    `<img src="${pathToFileURL(imagePath).href}" alt="${escapeHtml(alt)}" style="display:block;width:100%;border-radius:18px;border:1px solid #e5e7eb;" />`,
    "</figure>",
  ].join("");
}

function renderClipboardImage(imagePath, alt) {
  return [
    '<figure style="margin:24px 0;">',
    `<img src="${pathToFileURL(imagePath).href}" alt="${escapeHtml(alt)}" style="display:block;max-width:100%;height:auto;border-radius:16px;" />`,
    "</figure>",
  ].join("");
}

function buildPreviewHtml(title, blocks, theme) {
  const body = blocks
    .map((block) => {
      if (block.type === "image") {
        return renderImageBlock(block.path, block.alt);
      }
      return block.html || "";
    })
    .join("\n");

  return [
    "<!doctype html>",
    "<html><head><meta charset=\"utf-8\" />",
    `<title>${escapeHtml(title)}</title>`,
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />",
    "</head>",
    `<body style="margin:0;padding:32px 0;background:${theme.pageBackground};font-family:'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;">`,
    '<main style="max-width:760px;margin:0 auto;padding:0 18px;">',
    `<article style="padding:34px 32px;background:${theme.background};border-radius:24px;box-shadow:0 18px 40px rgba(15,23,42,0.08);">`,
    `<h1 style="margin:0 0 26px;font-size:34px;line-height:1.28;color:${theme.text};">${escapeHtml(title)}</h1>`,
    body,
    "</article></main></body></html>",
  ].join("");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function compile(payload) {
  const themeId = THEMES[payload.theme_id] ? payload.theme_id : "sspai";
  const theme = THEMES[themeId];
  const blocks = [];

  blocks.push({
    type: "html",
    role: "summary",
    html: renderSummaryCard(payload.summary, theme),
  });

  const bodyBlocks = splitBodyBlocks(payload.body_markdown, payload.article_dir);
  bodyBlocks.forEach((block) => {
    if (block.type === "image") {
      blocks.push({
        type: "image",
        role: "body_image",
        alt: block.alt,
        path: block.path,
      });
      return;
    }
    blocks.push({
      type: "html",
      role: "body",
      html: renderMarkdownBlock(block.markdown, theme),
    });
  });

  if (String(payload.cta_markdown || "").trim()) {
    blocks.push({
      type: "html",
      role: "cta",
      html: renderCtaCard(payload.cta_markdown, theme),
    });
  }

  const clipboardHtml = blocks
    .map((block) => {
      if (block.type === "image") {
        return renderClipboardImage(block.path, block.alt);
      }
      return block.html || "";
    })
    .join("\n");

  return {
    theme_id: themeId,
    layout_profile: "raphael_wechat_v1",
    content_blocks: blocks,
    content_html: clipboardHtml,
    clipboard_html: clipboardHtml,
    preview_html: buildPreviewHtml(payload.title, blocks, theme),
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const payload = JSON.parse(fs.readFileSync(args.input, "utf8"));
  const result = compile(payload);
  process.stdout.write(JSON.stringify(result, null, 2));
}

main();
