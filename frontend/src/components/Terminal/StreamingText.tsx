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
  // Check if content looks like an ASCII table (contains | and multiple lines)
  const isAsciiTable = content.includes('|') && content.includes('\n') && content.split('\n').filter(l => l.includes('|')).length > 1;

  return (
    <div className="prose prose-invert prose-sm max-w-none overflow-x-auto">
      {isAsciiTable ? (
        <pre className="bg-transparent border-0 p-0 m-0 whitespace-pre overflow-x-auto text-text font-mono text-sm">
          {content}
        </pre>
      ) : (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="list-none pl-4 mb-2">{children}</ul>,
          ol: ({ children }) => <ol className="list-none pl-4 mb-2">{children}</ol>,
          li: ({ children }) => (
            <li className="mb-1 flex items-start gap-2">
              <span className="text-accent">*</span>
              <span>{children}</span>
            </li>
          ),
          h1: ({ children }) => (
            <h1 className="text-lg font-bold text-text mb-2">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-bold text-text mb-2">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-bold text-muted mb-1">{children}</h3>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-accent">{children}</strong>
          ),
          em: ({ children }) => <em className="text-muted italic">{children}</em>,
          code: ({ className, children }) => {
            const isInline = !className;
            if (isInline) {
              return (
                <code className="bg-panel px-1.5 py-0.5 rounded text-accent">
                  {children}
                </code>
              );
            }
            return (
              <code className={className}>{children}</code>
            );
          },
          pre: ({ children }) => (
            <pre className="bg-panel border border-border rounded p-3 overflow-x-auto my-2">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full border-collapse">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-tableBorder px-3 py-2.5 text-left text-text font-semibold align-top">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-tableBorder px-3 py-2.5 align-top">{children}</td>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      )}
      {isStreaming && (
        <span className="inline-block w-2 h-4 bg-accent animate-blink ml-0.5" />
      )}
    </div>
  );
});
