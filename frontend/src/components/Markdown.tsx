import { Fragment } from "react";
import { safeHref } from "@/lib/config";

// A tiny, safe subset of markdown for model-written answers: paragraphs, "-"/"*"/"1." lists, **bold**, `code`,
// *italic*, [text](url) and bare URLs. Everything becomes React nodes (never HTML), and links only allow http(s).
const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|https?:\/\/[^\s)<>]+)/g;

function linkLabel(href: string) {
  try {
    return new URL(href).hostname.replace(/^www\./, "");
  } catch {
    return "link";
  }
}

function inline(text: string): React.ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (/^(\*[^*].*\*)$/.test(part) && part.length > 2) return <em key={i}>{part.slice(1, -1)}</em>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const md = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    const href = safeHref(md ? md[2] : part.replace(/[.,;:]+$/, ""));
    if (md || href) {
      return href ? (
        <a key={i} href={href} target="_blank" rel="noopener noreferrer nofollow">{md ? md[1] : linkLabel(href)}</a>
      ) : <Fragment key={i}>{md ? md[1] : part}</Fragment>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join(" "))}</p>);
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(<Tag key={blocks.length}>{list.items.map((it, i) => <li key={i}>{inline(it)}</li>)}</Tag>);
    list = null;
  };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const bullet = line.match(/^([-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      flushPara();
      const ordered = /\d/.test(bullet[1]);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(bullet[2]);
    } else if (!line) {
      flushPara();
      flushList();
    } else {
      flushList();
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      if (heading) {
        flushPara();
        blocks.push(<h3 key={blocks.length}>{inline(heading[1])}</h3>);
      } else {
        para.push(line);
      }
    }
  }
  flushPara();
  flushList();
  return <div className="prose">{blocks}</div>;
}
