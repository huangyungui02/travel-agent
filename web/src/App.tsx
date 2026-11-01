import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useStream } from "@langchain/react";
import { HumanMessage } from "@langchain/core/messages";
import type { Thread } from "@langchain/langgraph-sdk";
import {
  ArrowDownToLine,
  ArrowUp,
  Check,
  ChevronDown,
  Compass,
  FileText,
  Globe2,
  LoaderCircle,
  MapPin,
  Menu,
  MessageSquare,
  PanelRightClose,
  Plus,
  Route,
  Square,
  Sunrise,
  Utensils,
  X,
} from "lucide-react";
import {
  apiUrl,
  assistantId,
  client,
  errorText,
  type TravelState,
} from "./api";
import { Markdown } from "./Markdown";

const suggestions = [
  {
    icon: Sunrise,
    label: "去一座城，慢下来",
    text: "想找一座适合慢旅行的国内城市，玩三天，帮我一起选个目的地吧。",
    detail: "三天两晚 · 城市漫游",
  },
  {
    icon: Utensils,
    label: "为了美食出发",
    text: "想安排一次以当地美食为主的旅行，请先帮我选目的地和确认预算。",
    detail: "街巷烟火 · 当地滋味",
  },
  {
    icon: Route,
    label: "把想去的地方串起来",
    text: "我已经有几个想去的地方了，想请你帮我确认交通、酒店和预算，再安排详细行程。",
    detail: "心愿清单 · 专属行程",
  },
];

function threadTitle(thread: Thread<TravelState>) {
  const message = thread.values?.messages?.find(
    (message) => (message.type as string) === "human",
  );
  return typeof message?.content === "string"
    ? message.content.slice(0, 28)
    : "新的旅行";
}

const toolLabels: Record<string, string> = {
  task: "规划每日行程",
  flyai: "查询交通与酒店",
  current_date: "确认旅行日期",
  read_file: "阅读资料",
  write_file: "保存行程",
  edit_file: "更新行程",
  web_search: "搜索旅行信息",
};

export function App() {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const [threads, setThreads] = useState<Thread<TravelState>[]>([]);
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [showItinerary, setShowItinerary] = useState(false);
  const [stopping, setStopping] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const refreshThreads = useCallback(async () => {
    try {
      const results = await client.threads.search({
        limit: 50,
        sortBy: "updated_at",
        sortOrder: "desc",
      });
      setThreads(results);
      setConnected(true);
    } catch (error) {
      setConnected(false);
      setLocalError(errorText(error));
    }
  }, []);

  const stream = useStream<TravelState>({
    apiUrl,
    assistantId,
    threadId: threadId ?? null,
    onThreadId: (id) => {
      void navigate(`/chat/${id}`, { replace: true });
    },
    onCompleted: () => {
      void refreshThreads();
    },
  });
  const itineraryContent =
    stream.values.files?.["/daily-itinerary.md"]?.content;
  const itinerary = Array.isArray(itineraryContent)
    ? itineraryContent.join("\n")
    : (itineraryContent ?? "");
  const messages = stream.messages.filter(
    (message) => ["human", "ai"].includes(message.type) && message.text.trim(),
  );
  const current = threads.find((thread) => thread.thread_id === threadId);
  const title = current ? threadTitle(current) : "新的旅行";
  const busy = stream.isLoading || stopping;
  const error = localError || (stream.error ? errorText(stream.error) : null);

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, messages.at(-1)?.text, busy]);
  useEffect(() => {
    setLocalError(null);
    setMobileMenu(false);
    setDraft("");
  }, [threadId]);

  async function send(text = draft) {
    if (!text.trim() || busy || stream.isThreadLoading) return;
    setLocalError(null);
    setDraft("");
    try {
      await stream.submit(
        { messages: [new HumanMessage(text.trim())] },
        { multitaskStrategy: "reject" },
      );
    } catch (error) {
      setLocalError(errorText(error));
    }
  }

  async function stop() {
    setStopping(true);
    try {
      await stream.stop({ cancel: true });
    } catch (error) {
      setLocalError(`停止任务失败：${errorText(error)}`);
    } finally {
      setStopping(false);
      void refreshThreads();
    }
  }

  function download() {
    const url = URL.createObjectURL(
      new Blob([itinerary], { type: "text/markdown;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "旅行行程.md";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className={`app ${showItinerary ? "itinerary-open" : ""}`}>
      {mobileMenu && (
        <button
          className="scrim"
          aria-label="关闭菜单"
          onClick={() => setMobileMenu(false)}
        />
      )}
      <aside className={`sidebar ${mobileMenu ? "mobile-open" : ""}`}>
        <a
          className="brand"
          href="/chat"
          onClick={(event) => {
            event.preventDefault();
            if (!busy) void navigate("/chat");
          }}
        >
          <span className="brand-icon">
            <Compass size={25} strokeWidth={1.6} />
          </span>
          <span>
            漫游<span className="brand-en">ROAM</span>
          </span>
        </a>
        <button
          className="new-trip"
          disabled={busy}
          onClick={() => {
            void navigate("/chat");
            setShowItinerary(false);
          }}
        >
          <Plus size={17} /> 开始新的旅行
        </button>
        <div className="nav-label">
          我的旅行 <span>{threads.length.toString().padStart(2, "0")}</span>
        </div>
        <nav className="thread-list" aria-label="旅行会话">
          {threads.map((thread) => (
            <button
              key={thread.thread_id}
              disabled={busy}
              className={`thread ${threadId === thread.thread_id ? "selected" : ""}`}
              onClick={() => void navigate(`/chat/${thread.thread_id}`)}
            >
              <MessageSquare size={15} />
              <span>{threadTitle(thread)}</span>
              {thread.status === "busy" && (
                <LoaderCircle size={13} className="spin" />
              )}
            </button>
          ))}
          {!threads.length && (
            <p className="empty-history">
              下一段旅程，从这里开始。
              <br />
              对话会自动保存在这里。
            </p>
          )}
        </nav>
        <div className="sidebar-note">
          <Globe2 size={23} strokeWidth={1.3} />
          <p>
            世界很大，
            <br />
            慢慢出发。
          </p>
          <span>让每一次出发，都刚刚好。</span>
        </div>
        <div className="connection">
          <span className={`status-dot ${connected ? "online" : ""}`} />
          {connected === null
            ? "正在连接"
            : connected
              ? "旅行助手已连接"
              : "服务暂未连接"}
          <button
            aria-label="重新连接"
            onClick={() => {
              setLocalError(null);
              void refreshThreads();
            }}
          >
            重连
          </button>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="icon-button mobile-menu-button"
              aria-label="打开菜单"
              onClick={() => setMobileMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span className="breadcrumb">旅行工作室</span>
            <span className="breadcrumb-divider">/</span>
            <span className="current-title">{title}</span>
          </div>
          <button
            className={`itinerary-toggle ${showItinerary ? "active" : ""}`}
            onClick={() => setShowItinerary(!showItinerary)}
          >
            <FileText size={16} />
            行程草稿{itinerary && <span className="saved-dot" />}
          </button>
        </header>
        <div className="chat-scroll">
          {!messages.length && !stream.isThreadLoading ? (
            <section className="welcome">
              <div className="eyebrow">
                <span /> YOUR NEXT CHAPTER
              </div>
              <h1>
                下一站，
                <br />
                <span>想去哪里？</span>
              </h1>
              <p className="welcome-copy">
                从一个念头，到一段值得期待的旅程。
                <br />
                聊聊你的想法，剩下的我们一起安排。
              </p>
              <div className="welcome-art" aria-hidden="true">
                <svg viewBox="0 0 200 170" fill="none">
                  <path
                    d="M20 112 59 35l59 25 53-30 12 91-61 26-57-28-45 26z"
                    fill="#eceee4"
                    stroke="#c7cdbb"
                  />
                  <path d="m59 35 6 84m53-59 4 87" stroke="#c7cdbb" />
                  <path
                    d="M44 97c21-51 28 16 57-2s10-36 46-32"
                    stroke="#ca775f"
                    strokeWidth="2"
                    strokeDasharray="4 5"
                  />
                  <circle cx="44" cy="97" r="4" fill="#ca775f" />
                  <path
                    d="M149 30c-10 0-18 8-18 18 0 14 18 30 18 30s18-16 18-30c0-10-8-18-18-18z"
                    fill="#ca775f"
                  />
                  <circle cx="149" cy="48" r="6" fill="#faf8f3" />
                  <path
                    d="m34 27 7-12 7 12m-7-12v30"
                    stroke="#7a8569"
                    strokeWidth="1.5"
                  />
                </svg>
                <span>A LITTLE FURTHER, A LITTLE FREER</span>
              </div>
              <div className="suggestions">
                {suggestions.map(({ icon: Icon, label, text, detail }) => (
                  <button
                    key={label}
                    onClick={() => {
                      setDraft(text);
                      input.current?.focus();
                    }}
                  >
                    <Icon size={21} strokeWidth={1.4} />
                    <strong>{label}</strong>
                    <span>{detail}</span>
                    <ArrowUp size={14} className="suggestion-arrow" />
                  </button>
                ))}
              </div>
              <div className="process-note">
                <span>01 聊聊期待</span>
                <i />
                <span>02 确认交通与住宿</span>
                <i />
                <span>03 收获专属行程</span>
              </div>
            </section>
          ) : (
            <section className="conversation" aria-label="对话消息">
              {stream.isThreadLoading && (
                <p className="loading-note">
                  <LoaderCircle size={16} className="spin" />
                  正在读取旅行记录…
                </p>
              )}
              {messages.map((message, index) => (
                <article
                  key={message.id || index}
                  className={`message ${message.type === "human" ? "user-message" : "assistant-message"}`}
                >
                  <div className="message-avatar">
                    {message.type === "human" ? "你" : <Compass size={20} />}
                  </div>
                  <div className="message-body">
                    <div className="message-author">
                      {message.type === "human" ? "你" : "漫游助手"}
                      {message.type === "ai" && <span>旅行规划伙伴</span>}
                    </div>
                    <Markdown>{message.text}</Markdown>
                  </div>
                </article>
              ))}
              {busy && (
                <div className="working" role="status">
                  <LoaderCircle size={15} className="spin" />
                  {stopping ? "正在停止后台任务…" : "正在为你整理旅行安排…"}
                </div>
              )}
              {stream.toolCalls.length > 0 && (
                <details className="tool-activity">
                  <summary>
                    查看助手的工作记录 <span>{stream.toolCalls.length}</span>
                    <ChevronDown size={14} />
                  </summary>
                  <ul>
                    {stream.toolCalls.map((call) => (
                      <li key={call.callId}>
                        {call.status === "running" && busy ? (
                          <LoaderCircle size={13} className="spin" />
                        ) : call.status === "error" ? (
                          <X size={13} />
                        ) : (
                          <Check size={13} />
                        )}
                        <span>{toolLabels[call.name] || "查询旅行资料"}</span>
                        <small>
                          {call.status === "running"
                            ? busy
                              ? "进行中"
                              : "已结束"
                            : call.status === "error"
                              ? "失败"
                              : "完成"}
                        </small>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div ref={bottom} />
            </section>
          )}
        </div>
        <div className="composer-wrap">
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button
                aria-label="关闭错误提示"
                onClick={() => setLocalError(null)}
              >
                <X size={15} />
              </button>
            </div>
          )}
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={input}
              aria-label="旅行想法"
              placeholder="想去哪里、和谁出发，或者只是一个模糊的想法…"
              value={draft}
              rows={2}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  if (!busy) void send();
                }
              }}
            />
            <div className="composer-bottom">
              <span>
                <Compass size={14} />
                你的专属旅行规划伙伴
              </span>
              {busy ? (
                <button
                  className="send-button stop-button"
                  type="button"
                  aria-label="停止生成"
                  disabled={stopping}
                  onClick={() => void stop()}
                >
                  <Square size={15} fill="currentColor" />
                </button>
              ) : (
                <button
                  className="send-button"
                  type="submit"
                  aria-label="发送消息"
                  disabled={!draft.trim() || stream.isThreadLoading}
                >
                  <ArrowUp size={20} />
                </button>
              )}
            </div>
          </form>
          <p className="composer-hint">
            规划从沟通开始 · 价格与开放信息以实际查询及预订页面为准
          </p>
        </div>
      </main>

      {showItinerary && (
        <aside className="itinerary-panel" aria-label="行程草稿">
          <div className="itinerary-header">
            <div>
              <span className="eyebrow">THE PLAN</span>
              <h2>行程草稿</h2>
            </div>
            <button
              className="icon-button"
              aria-label="收起行程"
              onClick={() => setShowItinerary(false)}
            >
              <PanelRightClose size={19} />
            </button>
          </div>
          {itinerary ? (
            <>
              <div className="itinerary-meta">
                <span>
                  <Check size={13} />
                  已保存到当前旅行
                </span>
                <button onClick={download} aria-label="下载行程">
                  <ArrowDownToLine size={15} />
                  下载
                </button>
              </div>
              <div className="itinerary-content">
                <Markdown>{itinerary}</Markdown>
              </div>
            </>
          ) : (
            <div className="empty-itinerary">
              <div className="empty-itinerary-icon">
                <MapPin size={30} strokeWidth={1.2} />
              </div>
              <h3>好行程，值得慢慢打磨</h3>
              <p>
                确认旅行需求、交通和住宿后，
                <br />
                你的每日行程会出现在这里。
              </p>
              <div>
                <span>目的地与期待</span>
                <span>交通与住宿</span>
                <span>每日安排</span>
              </div>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
