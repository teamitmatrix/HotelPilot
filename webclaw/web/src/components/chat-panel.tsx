"use client";

import { useEffect, useRef, useState } from "react";
import {
  MessageSquare,
  Plus,
  Send,
  X,
  Trash2,
  Bot,
  User,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  useChat,
  type ChatMessage,
  type ChatSession,
} from "@/lib/chat";
import { cn } from "@/lib/utils";
import { postAction } from "@/lib/api";
import { useToast } from "@/components/toast-provider";
import { ConfirmationCard } from "@/components/confirmation-card";
import type { CompositionResult } from "@/lib/composition-types";

// ── Toggle Button (used in page headers) ────────────────────────────────────

export function ChatToggleButton() {
  const { toggle, isOpen } = useChat();
  return (
    <Button
      variant="default"
      size="sm"
      className={cn(
        "ai-chat-btn gap-1.5 rounded-lg border border-navy-800 bg-navy-900 font-medium text-white transition-colors duration-200",
        "hover:bg-navy-800 hover:text-white",
        "[&_svg]:stroke-white [&_svg]:fill-transparent",
        isOpen && "close-chat-btn"
      )}
      onClick={toggle}
      title="Toggle AI Chat (Ctrl+Shift+K)"
    >
      <MessageSquare className="h-4 w-4" />
      <span className="hidden sm:inline">{isOpen ? "Close Chat" : "AI Chat"}</span>
    </Button>
  );
}

// ── Simple Markdown Renderer ────────────────────────────────────────────────

function renderMarkdown(text: string) {
  // Split into code blocks and non-code sections
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith("```")) {
      const content = part.replace(/^```\w*\n?/, "").replace(/\n?```$/, "");
      return (
        <pre
          key={i}
          className="my-2 overflow-x-auto rounded bg-muted p-3 text-xs"
        >
          <code>{content}</code>
        </pre>
      );
    }
    // Inline formatting
    return (
      <span key={i}>
        {part.split("\n").map((line, j) => (
          <span key={j}>
            {j > 0 && <br />}
            {renderInline(line)}
          </span>
        ))}
      </span>
    );
  });
}

function renderInline(text: string) {
  // Bold, italic, inline code
  return text
    .split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g)
    .map((part, i) => {
      if (part.startsWith("`") && part.endsWith("`")) {
        return (
          <code key={i} className="rounded bg-muted px-1 py-0.5 text-xs">
            {part.slice(1, -1)}
          </code>
        );
      }
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={i}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("*") && part.endsWith("*")) {
        return <em key={i}>{part.slice(1, -1)}</em>;
      }
      return part;
    });
}

// ── Structured block parsers ───────────────────────────────────────────────

type ToolResultDisplay = {
  tool?: string;
  ok?: boolean;
  type: "metrics" | "table" | "status";
  title?: string;
  body?: string;
  tone?: string;
  metrics?: { label: string; value: string }[];
  columns?: string[];
  rows?: string[][];
  empty?: string;
  footer_table?: {
    type?: string;
    title?: string;
    columns?: string[];
    rows?: string[][];
  };
};

function extractStructuredBlocks(text: string): {
  textParts: string[];
  compositions: CompositionResult[];
  toolResults: ToolResultDisplay[];
} {
  const regex = /<(composition|tool_result)>([\s\S]*?)<\/\1>/g;
  const textParts: string[] = [];
  const compositions: CompositionResult[] = [];
  const toolResults: ToolResultDisplay[] = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    textParts.push(text.slice(lastIndex, match.index));
    const kind = match[1];
    try {
      const parsed = JSON.parse(match[2]);
      if (kind === "composition") compositions.push(parsed);
      else toolResults.push(parsed as ToolResultDisplay);
    } catch {
      textParts.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  textParts.push(text.slice(lastIndex));

  return { textParts, compositions, toolResults };
}

function ToolResultCard({ result }: { result: ToolResultDisplay }) {
  if (result.type === "metrics") {
    return (
      <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-foreground shadow-sm">
        {result.title && (
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {result.title}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2">
          {(result.metrics || []).map((m) => (
            <div
              key={m.label}
              className="rounded-md border border-slate-100 bg-slate-50 px-2.5 py-2"
            >
              <div className="text-[11px] text-slate-500">{m.label}</div>
              <div className="text-sm font-semibold text-navy-900">{m.value}</div>
            </div>
          ))}
        </div>
        {result.footer_table && (
          <ToolResultCard
            result={{
              type: "table",
              title: result.footer_table.title,
              columns: result.footer_table.columns,
              rows: result.footer_table.rows,
            }}
          />
        )}
      </div>
    );
  }

  if (result.type === "table") {
    const cols = result.columns || [];
    const rows = result.rows || [];
    return (
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white text-foreground shadow-sm">
        {result.title && (
          <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            {result.title}
          </div>
        )}
        {rows.length === 0 ? (
          <div className="px-3 py-3 text-xs text-muted-foreground">
            {result.empty || "No rows"}
          </div>
        ) : (
          <div className="max-w-full overflow-x-auto">
            <table className="w-full min-w-[240px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  {cols.map((c) => (
                    <th key={c} className="px-2.5 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-t border-slate-100">
                    {row.map((cell, j) => (
                      <td key={j} className="px-2.5 py-1.5 text-slate-800">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 text-sm",
        result.tone === "success"
          ? "border-emerald-200 bg-emerald-50 text-emerald-900"
          : result.ok === false
            ? "border-red-200 bg-red-50 text-red-900"
            : "border-slate-200 bg-white text-foreground"
      )}
    >
      {result.title && (
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide opacity-70">
          {result.title}
        </div>
      )}
      <div className="prose prose-sm max-w-none dark:prose-invert">
        {renderMarkdown(result.body || "")}
      </div>
    </div>
  );
}

// ── Message Bubble ──────────────────────────────────────────────────────────

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const { pageContext } = useChat();
  const { showToast } = useToast();

  const hasBlocks =
    !isUser &&
    !!message.content &&
    (message.content.includes("<composition>") ||
      message.content.includes("<tool_result>"));
  const parsed = hasBlocks ? extractStructuredBlocks(message.content) : null;
  const hasRich =
    parsed &&
    (parsed.compositions.length > 0 || parsed.toolResults.length > 0);

  return (
    <div
      className={cn(
        "flex gap-2 px-3 py-2",
        isUser ? "flex-row-reverse" : "flex-row"
      )}
    >
      <div
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground"
        )}
      >
        {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </div>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2 text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground"
        )}
      >
        {isUser ? (
          message.content
        ) : message.content ? (
          hasRich && parsed ? (
            <div className="space-y-3">
              {parsed.textParts.map(
                (part, i) =>
                  part.trim() && (
                    <div
                      key={`text-${i}`}
                      className="prose prose-sm dark:prose-invert max-w-none"
                    >
                      {renderMarkdown(part)}
                    </div>
                  )
              )}
              {parsed.toolResults.map((tr, i) => (
                <ToolResultCard key={`tool-${i}`} result={tr} />
              ))}
              {parsed.compositions.map((comp, i) => (
                <ConfirmationCard
                  key={`comp-${i}`}
                  composition={comp}
                  skill={pageContext.skill || comp.action.split("-").slice(1).join("-")}
                  onSubmit={async (params) => {
                    const skill = comp.skill || pageContext.skill || "unknown";
                    const result = await postAction(skill, comp.action, params);
                    if (result.status === "ok") {
                      showToast({ type: "success", message: `${comp.action} completed successfully` });
                    } else {
                      showToast({
                        type: "error",
                        message: result.message || "Action failed",
                        duration: 0,
                      });
                    }
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              {renderMarkdown(message.content)}
            </div>
          )
        ) : (
          <span className="flex items-center gap-1 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Thinking...
          </span>
        )}
      </div>
    </div>
  );
}

// ── Chat History Sidebar ────────────────────────────────────────────────────

function ChatHistorySidebar({
  open,
  onClose,
  sessions,
  currentId,
  onSwitch,
  onDelete,
}: {
  open: boolean;
  onClose: () => void;
  sessions: ChatSession[];
  currentId: string | null;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <>
      {/* Backdrop */}
      <div
        className={cn(
          "absolute inset-0 z-20 bg-black/30 transition-opacity duration-200",
          open ? "opacity-100" : "pointer-events-none opacity-0"
        )}
        onClick={onClose}
        aria-hidden={!open}
      />

      {/* Sidebar panel */}
      <aside
        className={cn(
          "absolute inset-y-0 left-0 z-30 flex w-[260px] flex-col border-r border-[#CBD5E1] bg-white shadow-lg transition-transform duration-200 ease-out",
          open ? "translate-x-0" : "-translate-x-full"
        )}
        aria-hidden={!open}
      >
        <div className="flex items-center gap-2 bg-navy-900 px-4 py-3 text-white">
          <MessageSquare className="h-4 w-4" />
          <span className="flex-1 text-sm font-semibold">Chat History</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-navy-800 hover:text-white"
            onClick={onClose}
            title="Close history"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {sessions.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              No chat history yet
            </p>
          ) : (
            <div className="space-y-1">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={cn(
                    "group flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-accent",
                    currentId === s.id && "bg-accent"
                  )}
                  onClick={() => onSwitch(s.id)}
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">{s.title || "Untitled"}</span>
                  <button
                    className="hidden shrink-0 text-muted-foreground hover:text-destructive group-hover:block"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(s.id);
                    }}
                    title="Delete chat"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

// ── Chat Panel Content (shared between inline & Sheet) ──────────────────────

function ChatPanelContent() {
  const {
    messages,
    isStreaming,
    sendMessage,
    pageContext,
    sessionScope,
    sessionId,
    sessions,
    startNewChat,
    switchSession,
    deleteSession,
    close,
  } = useChat();

  const [input, setInput] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const userScrolledRef = useRef(false);

  const scopeBadge = sessionScope?.scope_badge;
  const contextLabel = pageContext.skill
    ? pageContext.skill.replace(/^erpclaw-/, "")
    : pageContext.view || "general";

  // Auto-scroll to bottom on new messages (unless user scrolled up)
  useEffect(() => {
    if (!userScrolledRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    userScrolledRef.current = !atBottom;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput("");
    userScrolledRef.current = false; // auto-scroll for new message
    await sendMessage(text);
    inputRef.current?.focus();
  }

  async function handleNewChat() {
    setInput("");
    setShowHistory(false);
    userScrolledRef.current = false;
    await startNewChat();
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="ai-assistant-container relative flex h-full flex-col overflow-hidden bg-[#F1F5F9]">
      {/* Header */}
      <div className="ai-assistant-header flex flex-col gap-2 bg-navy-900 px-4 py-3 text-white">
        <div className="flex items-center gap-2 [&_*]:text-white">
          <Bot className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">
            AI Assistant
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 rounded-full border-white/40 bg-navy-900 px-2.5 text-xs font-medium text-white hover:bg-navy-800 hover:text-white"
            onClick={handleNewChat}
            disabled={isStreaming}
            title="Start a new chat"
          >
            <Plus className="h-3 w-3" />
            New Chat
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-navy-800 hover:text-white"
            onClick={() => setShowHistory(true)}
            title="Chat history"
          >
            <MessageSquare className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-white hover:bg-navy-800 hover:text-white lg:hidden"
            onClick={close}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        {scopeBadge && (
          <div
            className="truncate rounded-md border border-white/20 bg-white/10 px-2 py-0.5 text-[11px] font-medium tracking-wide text-white/90"
            title={scopeBadge}
          >
            {scopeBadge}
          </div>
        )}
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto"
        onScroll={handleScroll}
      >
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <Bot className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Ask me anything about your data.
            </p>
            {scopeBadge && (
              <p className="text-xs text-muted-foreground">
                Scope: <strong>{scopeBadge}</strong>
              </p>
            )}
            {pageContext.skill && (
              <p className="text-xs text-muted-foreground">
                Viewing <strong>{contextLabel}</strong>.
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-1 py-2">
            {messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t p-3">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your data..."
            className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            rows={1}
            disabled={isStreaming}
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim() || isStreaming}
          >
            {isStreaming ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* History sidebar (overlays chat content) */}
      <ChatHistorySidebar
        open={showHistory}
        onClose={() => setShowHistory(false)}
        sessions={sessions}
        currentId={sessionId}
        onSwitch={(id) => {
          switchSession(id);
          setShowHistory(false);
        }}
        onDelete={deleteSession}
      />
    </div>
  );
}

// ── Main ChatPanel — inline on desktop, Sheet on mobile ─────────────────────

export function ChatPanel() {
  const { isOpen, close } = useChat();
  const [isDesktop, setIsDesktop] = useState(false);

  // Match the lg: breakpoint (1024px) to decide inline vs Sheet
  useEffect(() => {
    const mql = window.matchMedia("(min-width: 1024px)");
    const onChange = () => setIsDesktop(mql.matches);
    mql.addEventListener("change", onChange);
    setIsDesktop(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  // Keyboard shortcut: Ctrl+Shift+K
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "K") {
        e.preventDefault();
        // Access toggle via the hook is tricky here since this is in useEffect;
        // we use a custom event to avoid stale closures
        window.dispatchEvent(new CustomEvent("ocui-chat-toggle"));
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  return (
    <>
      {/* Desktop: full-height right dock */}
      {isOpen && (
        <aside className="chat-window-wrapper hidden h-svh max-h-svh w-[360px] shrink-0 flex-col self-stretch border-l border-[#CBD5E1] bg-[#F1F5F9] lg:flex">
          <ChatPanelContent />
        </aside>
      )}

      {/* Mobile: Sheet slide-over (only open on non-desktop to avoid aria-hidden on page) */}
      <Sheet open={isOpen && !isDesktop} onOpenChange={(open) => !open && close()}>
        <SheetContent
          side="right"
          className="chat-window-wrapper h-svh max-h-svh w-full border-l border-[#CBD5E1] bg-[#F1F5F9] p-0 sm:max-w-md lg:hidden"
          showCloseButton={false}
        >
          <SheetHeader className="sr-only">
            <SheetTitle>AI Chat</SheetTitle>
            <SheetDescription>Chat with AI assistant</SheetDescription>
          </SheetHeader>
          <ChatPanelContent />
        </SheetContent>
      </Sheet>
    </>
  );
}
