import React from "react";

// Minimal, dependency-free inline formatter: **bold**, `code`, and bullet lines.
function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      nodes.push(<strong key={`${keyPrefix}-b${i}`}>{tok.slice(2, -2)}</strong>);
    } else {
      nodes.push(<code key={`${keyPrefix}-c${i}`}>{tok.slice(1, -1)}</code>);
    }
    last = m.index + tok.length;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  const blocks: React.ReactNode[] = [];
  let list: React.ReactNode[] = [];

  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`ul-${blocks.length}`}>{list}</ul>);
      list = [];
    }
  };

  lines.forEach((line, idx) => {
    const t = line.trim();
    if (t.startsWith("- ")) {
      list.push(<li key={`li-${idx}`}>{inline(t.slice(2), `l${idx}`)}</li>);
    } else {
      flush();
      if (t === "") {
        blocks.push(<div key={`sp-${idx}`} className="spacer" />);
      } else {
        blocks.push(<p key={`p-${idx}`}>{inline(line, `p${idx}`)}</p>);
      }
    }
  });
  flush();
  return <div className="markdown">{blocks}</div>;
}
