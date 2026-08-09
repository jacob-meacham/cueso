import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

type Props = {
  children: string;
};

/** Assistant-message markdown, tuned for compact chat bubbles. */
const Markdown = memo(function Markdown({ children }: Props) {
  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          strong: ({ children }) => (
            <strong className="font-semibold text-white">{children}</strong>
          ),
          ul: ({ children }) => (
            <ul className="mb-2 list-disc space-y-0.5 pl-5 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2 list-decimal space-y-0.5 pl-5 last:mb-0">{children}</ol>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-indigo-400 underline underline-offset-2 hover:text-indigo-300"
            >
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="rounded bg-black/30 px-1 py-0.5 font-mono text-[0.85em]">
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded-lg bg-black/30 p-3 text-xs last:mb-0 [&>code]:bg-transparent [&>code]:p-0">
              {children}
            </pre>
          ),
          h1: ({ children }) => (
            <h1 className="mb-1 mt-3 text-base font-semibold text-white first:mt-0">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-1 mt-3 text-base font-semibold text-white first:mt-0">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1 mt-2 text-sm font-semibold text-white first:mt-0">{children}</h3>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l border-slate-600 pl-3 text-slate-400 last:mb-0">
              {children}
            </blockquote>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;
