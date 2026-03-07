#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");
const MarkdownIt = require("markdown-it");
const hljs = require("highlight.js");
const { JSDOM } = require("jsdom");
const {
  escapeHtml,
  getThemeTokens,
  makeWeChatCompatible,
  preprocessMarkdown,
  renderImageFragment,
  renderMarkdownFragment,
} = require("./raphael_core");
const { getTheme } = require("./raphael_themes");

const publishMd = new MarkdownIt({
  html: true,
  breaks: true,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      return `<pre><code class="hljs">${hljs.highlight(code, { language }).value}</code></pre>`;
    }
    return `<pre><code class="hljs">${publishMd.utils.escapeHtml(code)}</code></pre>`;
  },
});

function parseArgs(argv) {
  const out = { input: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--input") {
      out.input = argv[index + 1] || "";
      index += 1;
    }
  }
  if (!out.input) {
    throw new Error("missing --input");
  }
  return out;
}

function compactMarkdown(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function resolveImageSpec(spec, articleDir) {
  const rawPath = String((spec && (spec.raw_path || spec.path)) || "").trim();
  const resolved = path.isAbsolute(rawPath) ? rawPath : path.resolve(String(articleDir || "").trim(), rawPath);
  return {
    alt: String(spec && spec.alt || "").trim() || "正文配图",
    rawPath,
    path: resolved,
  };
}

function buildFallbackArticleModel(payload) {
  const lines = String(payload.body_markdown || "").replace(/\r\n/g, "\n").split("\n");
  const imageLineRe = /^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$/;
  const headingRe = /^\s*###\s+(.+?)\s*$/;
  const leadLines = [];
  const leadImages = [];
  const sections = [];
  let current = null;

  function ensureCurrent(heading) {
    current = { heading, bodyLines: [], images: [] };
    sections.push(current);
    return current;
  }

  lines.forEach((line) => {
    const headingMatch = line.match(headingRe);
    if (headingMatch) {
      ensureCurrent(String(headingMatch[1] || "").trim());
      return;
    }

    const imageMatch = line.match(imageLineRe);
    if (imageMatch) {
      const imageSpec = {
        alt: String(imageMatch[1] || "").trim(),
        raw_path: String(imageMatch[2] || "").trim(),
      };
      if (current) {
        current.images.push(imageSpec);
      } else {
        leadImages.push(imageSpec);
      }
      return;
    }

    if (current) {
      current.bodyLines.push(line);
      return;
    }
    leadLines.push(line);
  });

  return {
    title: String(payload.title || "").trim(),
    summary: String(payload.summary || "").trim(),
    lead_markdown: compactMarkdown(leadLines.join("\n")),
    lead_images: leadImages,
    sections: sections.map((section) => ({
      heading: String(section.heading || "").trim(),
      body_markdown: compactMarkdown(section.bodyLines.join("\n")),
      images: section.images,
    })),
    cta_markdown: compactMarkdown(payload.cta_markdown),
  };
}

function normalizeArticleModel(payload) {
  const raw = payload.article_model;
  if (!raw || typeof raw !== "object") {
    return buildFallbackArticleModel(payload);
  }
  return {
    title: String(raw.title || payload.title || "").trim(),
    summary: String(raw.summary || payload.summary || "").trim(),
    lead_markdown: compactMarkdown(raw.lead_markdown),
    lead_images: Array.isArray(raw.lead_images) ? raw.lead_images : [],
    sections: Array.isArray(raw.sections) ? raw.sections : [],
    cta_markdown: compactMarkdown(raw.cta_markdown || payload.cta_markdown),
  };
}

function renderSummaryCard(summary, themeId) {
  const tokens = getThemeTokens(themeId);
  return [
    `<section data-role="summary-card" style="margin:0 0 28px; padding:18px 18px 16px; border:1px solid ${tokens.quoteBorder}; border-radius:18px; background:${tokens.accentSoft};">`,
    `<div style="margin:0 0 10px; font-size:12px; line-height:1; letter-spacing:0.08em; font-weight:700; text-transform:uppercase; color:${tokens.accent};">文章摘要</div>`,
    `<p style="margin:0; font-size:15px; line-height:1.9; color:${tokens.text}; font-weight:400 !important;">${escapeHtml(String(summary || "").trim())}</p>`,
    "</section>",
  ].join("");
}

function renderSectionHeadingCard(heading, themeId, index) {
  const tokens = getThemeTokens(themeId);
  const label = String(index + 1).padStart(2, "0");
  return [
    `<section data-role="section-heading" style="margin:34px 0 14px; padding:0;">`,
    `<div style="display:flex; align-items:center; gap:12px;">`,
    `<span style="display:inline-flex; align-items:center; justify-content:center; min-width:40px; height:40px; padding:0 10px; border-radius:999px; background:${tokens.accentSoft}; color:${tokens.accent}; font-size:16px; font-weight:700; line-height:1;">${escapeHtml(label)}</span>`,
    `<h3 style="margin:0; font-size:24px; line-height:1.45; font-weight:700; color:${tokens.text};">${escapeHtml(String(heading || "").trim())}</h3>`,
    `</div>`,
    `</section>`,
  ].join("");
}

function renderPublishSafeMarkdown(markdown, themeId, role) {
  const tokens = getThemeTokens(themeId);
  const html = publishMd.render(preprocessMarkdown(markdown));
  const dom = new JSDOM(`<body>${String(html || "")}</body>`);
  const { document } = dom.window;

  document.querySelectorAll("img, figure, table").forEach((node) => node.remove());

  document.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((node) => {
    const p = document.createElement("p");
    p.innerHTML = node.innerHTML;
    node.replaceWith(p);
  });

  const baseParagraphStyle = [
    "margin:0 0 22px",
    `font-size:${role === "lead" ? "16px" : "16px"}`,
    "line-height:1.95",
    `color:${tokens.text}`,
    "font-weight:400 !important",
    "letter-spacing:0.015em",
  ].join("; ");

  document.querySelectorAll("p").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", current ? `${current}; ${baseParagraphStyle}` : baseParagraphStyle);
  });

  document.querySelectorAll("ul, ol").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "margin:4px 0 22px",
      "padding-left:1.55em",
      `color:${tokens.text}`,
      "font-size:16px",
      "line-height:1.95",
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("li").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "margin:0 0 14px",
      `color:${tokens.text}`,
      "font-size:16px",
      "line-height:1.95",
      "font-weight:400 !important",
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("blockquote").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "margin:20px 0",
      "padding:14px 16px",
      `border-left:4px solid ${tokens.accent}`,
      `background:${tokens.accentSoft}`,
      "border-radius:14px",
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("strong, b").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", current ? `${current}; font-weight:700 !important;` : "font-weight:700 !important;");
  });

  document.querySelectorAll("em, i").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    node.setAttribute("style", current ? `${current}; font-style:italic;` : "font-style:italic;");
  });

  document.querySelectorAll("a").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [`color:${tokens.accent}`, "text-decoration:none"].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("pre").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "margin:18px 0",
      "padding:14px 16px",
      "overflow-x:auto",
      "border-radius:14px",
      "background:#0f172a",
      "color:#e2e8f0",
      "font-size:14px",
      "line-height:1.75",
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("code").forEach((node) => {
    if (node.closest("pre")) {
      return;
    }
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "padding:0.1em 0.35em",
      "border-radius:6px",
      "background:rgba(15, 23, 42, 0.06)",
      "font-size:0.94em",
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  document.querySelectorAll("hr").forEach((node) => {
    const current = String(node.getAttribute("style") || "").trim();
    const extra = [
      "margin:24px 0",
      "border:none",
      `border-top:1px solid ${tokens.divider}`,
    ].join("; ");
    node.setAttribute("style", current ? `${current}; ${extra}` : extra);
  });

  return document.body.innerHTML.trim();
}

function splitSectionHeading(heading, index) {
  const text = String(heading || "").trim();
  const matched = text.match(/^(\d{1,2})[\s、.．\-_:：]*(.+)$/);
  if (matched) {
    return {
      label: String(matched[1] || "").padStart(2, "0"),
      title: String(matched[2] || "").trim(),
    };
  }
  return {
    label: String(index + 1).padStart(2, "0"),
    title: text,
  };
}

function renderPublishSummaryBlock(summary, themeId) {
  const tokens = getThemeTokens(themeId);
  return [
    `<div data-role="summary" style="margin:0 0 30px; padding:16px 18px; border-left:3px solid ${tokens.accent}; background:${tokens.accentSoft}; border-radius:16px;">`,
    `<p style="margin:0; font-size:15px; line-height:1.9; color:${tokens.text}; font-weight:400 !important;">${escapeHtml(String(summary || "").trim())}</p>`,
    "</div>",
  ].join("");
}

function renderPublishSectionHeading(heading, themeId, index) {
  const tokens = getThemeTokens(themeId);
  const parsed = splitSectionHeading(heading, index);
  return [
    `<div data-role="section-heading" style="margin:40px 0 18px; padding:0;">`,
    `<div style="display:flex; align-items:center; gap:10px; margin:0 0 10px;">`,
    `<span style="display:inline-flex; align-items:center; justify-content:center; min-width:42px; height:32px; padding:0 12px; border-radius:999px; background:${tokens.accentSoft}; color:${tokens.accent}; font-size:15px; font-weight:700; line-height:1;">${escapeHtml(parsed.label)}</span>`,
    `<span style="display:inline-block; font-size:12px; line-height:1; letter-spacing:0.12em; text-transform:uppercase; color:${tokens.muted}; font-weight:700;">关键章节</span>`,
    `</div>`,
    `<p style="margin:0; color:${tokens.text}; font-size:24px; line-height:1.5; font-weight:700;">${escapeHtml(parsed.title)}</p>`,
    `<div style="margin:12px 0 0; width:56px; height:3px; border-radius:999px; background:${tokens.accent};"></div>`,
    `</div>`,
  ].join("");
}

function renderPublishCtaBlock(markdown, themeId) {
  const tokens = getThemeTokens(themeId);
  const inner = renderPublishSafeMarkdown(markdown, themeId, "cta");
  return [
    `<div data-role="cta" style="margin:34px 0 0; padding:18px 20px; border:1px solid ${tokens.divider}; border-radius:18px; background:${tokens.quoteBackground};">`,
    `<div style="margin:0 0 12px; font-size:12px; line-height:1; letter-spacing:0.1em; text-transform:uppercase; color:${tokens.accent}; font-weight:700;">行动建议</div>`,
    inner,
    "</div>",
  ].join("");
}

function renderPublishSpacer(heightPx) {
  return `<p data-role="spacer" style="margin:0; height:${Number(heightPx) || 16}px; line-height:${Number(heightPx) || 16}px;"><br></p>`;
}

function renderCtaCard(markdown, themeId) {
  const tokens = getThemeTokens(themeId);
  const inner = renderMarkdownFragment(markdown, themeId);
  return [
    `<section data-role="cta-card" style="margin:32px 0 0; padding:18px 18px 16px; border:1px solid ${tokens.divider}; border-radius:18px; background:${tokens.quoteBackground};">`,
    `<div style="margin:0 0 12px; font-size:12px; line-height:1; letter-spacing:0.08em; font-weight:700; text-transform:uppercase; color:${tokens.accent};">行动建议</div>`,
    inner,
    "</section>",
  ].join("");
}

function wrapBodyHtml(html, themeId, role) {
  const tokens = getThemeTokens(themeId);
  return [
    `<section data-role="${escapeHtml(String(role || "body"))}" style="margin:0; padding:0; color:${tokens.text}; font-weight:400 !important;">`,
    String(html || ""),
    "</section>",
  ].join("");
}

function buildPreviewHtml(title, contentHtml, themeId) {
  const theme = getTheme(themeId);
  const tokens = getThemeTokens(themeId);
  const baseContainer = String(theme.styles.container || "")
    .replace(/max-width:\s*[^;]+;?/i, "")
    .replace(/margin:\s*[^;]+;?/i, "")
    .trim();
  const safeBaseContainer = baseContainer.replace(/"/g, "&quot;");

  return [
    "<!doctype html>",
    "<html><head><meta charset=\"utf-8\" />",
    `<title>${escapeHtml(title)}</title>`,
    '<meta name="viewport" content="width=device-width, initial-scale=1" />',
    "</head>",
    `<body style="margin:0; padding:32px 0 56px; background:${tokens.accentSoft};">`,
    '<main style="max-width:760px; margin:0 auto; padding:0 16px;">',
    `<article style="${safeBaseContainer}; max-width:720px; margin:0 auto; border-radius:24px; box-shadow:0 18px 48px rgba(15, 23, 42, 0.08);">`,
    '<header style="margin:0 0 28px;">',
    `<div style="margin:0 0 10px; font-size:12px; line-height:1; letter-spacing:0.08em; font-weight:700; text-transform:uppercase; color:${tokens.accent};">微信公众号排版预览</div>`,
    `<h1 style="margin:0; ${theme.styles.h1}">${escapeHtml(title)}</h1>`,
    "</header>",
    contentHtml,
    "</article>",
    "</main>",
    "</body></html>",
  ].join("");
}

function compile(payload) {
  const themeId = getTheme(String(payload.theme_id || "").trim()).id;
  const layoutProfile = String(payload.layout_profile || "raphael_wechat_v1").trim() || "raphael_wechat_v1";
  const article = normalizeArticleModel(payload);
  const articleDir = String(payload.article_dir || "").trim();
  const previewBlocks = [];
  const publishBlocks = [];

  if (article.summary) {
    previewBlocks.push({
      type: "html",
      role: "summary",
      html: renderSummaryCard(article.summary, themeId),
    });
    publishBlocks.push({
      type: "html",
      role: "summary",
      html: renderPublishSummaryBlock(article.summary, themeId),
    });
  }

  if (article.lead_markdown) {
    previewBlocks.push({
      type: "html",
      role: "lead",
      html: wrapBodyHtml(renderMarkdownFragment(article.lead_markdown, themeId), themeId, "lead"),
    });
    publishBlocks.push({
      type: "html",
      role: "lead",
      html: renderPublishSafeMarkdown(article.lead_markdown, themeId, "lead"),
    });
  }

  (article.lead_images || []).forEach((image) => {
    const resolved = resolveImageSpec(image, articleDir);
    previewBlocks.push({
      type: "image",
      role: "lead_image",
      alt: resolved.alt,
      path: resolved.path,
    });
    publishBlocks.push({
      type: "image",
      role: "lead_image",
      alt: resolved.alt,
      path: resolved.path,
    });
  });

  (article.sections || []).forEach((section, index) => {
    previewBlocks.push({
      type: "html",
      role: "section_heading",
      html: renderSectionHeadingCard(section.heading, themeId, index),
    });
    publishBlocks.push({
      type: "html",
      role: "section_heading",
      html: renderPublishSectionHeading(section.heading, themeId, index),
    });

    if (String(section.body_markdown || "").trim()) {
      previewBlocks.push({
        type: "html",
        role: "body",
        html: wrapBodyHtml(renderMarkdownFragment(section.body_markdown, themeId), themeId, "body"),
      });
      publishBlocks.push({
        type: "html",
        role: "body",
        html: renderPublishSafeMarkdown(section.body_markdown, themeId, "body"),
      });
    }

    (section.images || []).forEach((image) => {
      const resolved = resolveImageSpec(image, articleDir);
      publishBlocks.push({
        type: "html",
        role: "image_spacer_before",
        html: renderPublishSpacer(18),
      });
      previewBlocks.push({
        type: "image",
        role: "body_image",
        alt: resolved.alt,
        path: resolved.path,
      });
      publishBlocks.push({
        type: "image",
        role: "body_image",
        alt: resolved.alt,
        path: resolved.path,
      });
      publishBlocks.push({
        type: "html",
        role: "image_spacer_after",
        html: renderPublishSpacer(22),
      });
    });
  });

  if (article.cta_markdown) {
    previewBlocks.push({
      type: "html",
      role: "cta",
      html: renderCtaCard(article.cta_markdown, themeId),
    });
    publishBlocks.push({
      type: "html",
      role: "cta",
      html: renderPublishCtaBlock(article.cta_markdown, themeId),
    });
  }

  const articleHtml = previewBlocks
    .map((block) => {
      if (block.type === "image") {
        return renderImageFragment(block.path, block.alt, themeId);
      }
      return block.html || "";
    })
    .join("\n");

  const clipboardHtml = makeWeChatCompatible(articleHtml, themeId);

  return {
    theme_id: themeId,
    layout_profile: layoutProfile,
    article_model: article,
    preview_blocks: previewBlocks,
    publish_blocks: publishBlocks,
    content_blocks: publishBlocks,
    content_html: clipboardHtml,
    clipboard_html: clipboardHtml,
    preview_html: buildPreviewHtml(article.title || payload.title, articleHtml, themeId),
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const payload = JSON.parse(fs.readFileSync(args.input, "utf8"));
  const result = compile(payload);
  process.stdout.write(JSON.stringify(result, null, 2));
}

main();
