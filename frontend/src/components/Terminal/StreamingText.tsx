import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface StreamingTextProps {
  content: string;
  isStreaming?: boolean;
}

export const StreamingText = memo(function StreamingText({
  content,
  isStreaming = false,
}: StreamingTextProps) {
  return (
    <div className="prose prose-invert prose-sm max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="list-none pl-4 mb-2">{children}</ul>,
          ol: ({ children }) => <ol className="list-none pl-4 mb-2">{children}</ol>,
          li: ({ children }) => (
            <li className="mb-1 flex items-start gap-2">
              <span className="text-terminal-accent">*</span>
              <span>{children}</span>
            </li>
          ),
          h1: ({ children }) => (
            <h1 className="text-lg font-bold text-terminal-text mb-2">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-bold text-terminal-text mb-2">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-bold text-terminal-muted mb-1">{children}</h3>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-terminal-accent">{children}</strong>
          ),
          em: ({ children }) => <em className="text-terminal-muted italic">{children}</em>,
          code: ({ className, children }) => {
            const isInline = !className;
            if (isInline) {
              return (
                <code className="bg-terminal-surface px-1.5 py-0.5 rounded text-terminal-accent">
                  {children}
                </code>
              );
            }
            return (
              <code className={className}>{children}</code>
            );
          },
          pre: ({ children }) => (
            <pre className="bg-terminal-surface border border-terminal-border rounded p-3 overflow-x-auto my-2">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full border-collapse">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-terminal-border bg-terminal-surface px-3 py-2 text-left text-terminal-text font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-terminal-border px-3 py-2">{children}</td>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-terminal-link hover:underline"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming && (
        <span className="inline-block w-2 h-4 bg-terminal-accent animate-blink ml-0.5" />
      )}
    </div>
  );
});
