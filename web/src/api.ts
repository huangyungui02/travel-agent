import { useCallback, useEffect, useRef, useState } from "react";

type Message = {
  id?: string;
  type: string;
  content: string | { type: string; text?: string }[];
  tool_calls?: { id: string; name: string }[];
  tool_call_id?: string;
  status?: string;
};
export type TravelState = {
  messages: Message[];
  files?: Record<string, { content: string | string[]; modified_at?: string }>;
};
export type Thread = { thread_id: string; status: string; values: TravelState };
type Snapshot = { values: TravelState; status: string; error?: string };
const empty = (): TravelState => ({ messages: [] });
export const apiUrl = new URL(
  import.meta.env.VITE_AGENT_API_URL || "/api", window.location.origin,
).href.replace(/\/$/, "");

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : `请求失败（${response.status}）`);
  }
  return response.json();
}
export const client = {
  threads: { search: (_options?: unknown) => request<Thread[]>("/threads/search", {}) },
};

export function useTravelStream(threadId: string | undefined, onCompleted: () => Promise<void>) {
  const [values, setValues] = useState<TravelState>(empty);
  const [isLoading, setLoading] = useState(false);
  const [isThreadLoading, setThreadLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const activeId = useRef<string | undefined>(threadId);
  const submitting = useRef(false);

  const watch = useCallback(async (id: string, signal: AbortSignal) => {
    const response = await fetch(`${apiUrl}/threads/${id}/events`, { signal });
    if (!response.ok || !response.body) throw new Error("读取回复失败，请刷新会话。");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const event = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (!event.startsWith("data: ")) continue;
          const snapshot: Snapshot = JSON.parse(event.slice(6));
          if (signal.aborted) return;
          setValues(snapshot.values);
          setLoading(snapshot.status === "running");
          if (snapshot.error) setError(snapshot.error);
          terminal = snapshot.status !== "running";
        }
      }
      if (!terminal && !signal.aborted) throw new Error("连接中断，请刷新会话查看任务进度。");
      if (!signal.aborted) await onCompleted();
    } finally {
      await reader.cancel().catch(() => {});
    }
  }, [onCompleted]);

  useEffect(() => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    activeId.current = threadId;
    setValues(empty()); setError(null); setLoading(false); setThreadLoading(!!threadId);
    if (threadId) {
      void (async () => {
        try {
          const thread = await request<Thread>(`/threads/${threadId}`, undefined, current.signal);
          if (current.signal.aborted) return;
          setValues(thread.values); setThreadLoading(false);
          if (thread.status === "busy") {
            setLoading(true);
            await watch(threadId, current.signal);
          }
        } catch (cause) {
          if (!current.signal.aborted) { setError(errorText(cause)); setLoading(false); }
        } finally {
          if (!current.signal.aborted) setThreadLoading(false);
        }
      })();
    }
    return () => current.abort();
  }, [threadId, watch]);

  async function submit(message: string) {
    if (submitting.current) throw new Error("消息正在发送");
    submitting.current = true;
    setError(null); setLoading(true);
    try {
      let id = activeId.current;
      if (!id) id = (await request<Thread>("/threads", {})).thread_id;
      await request(`/threads/${id}/runs`, { message });
      activeId.current = id;
      // Navigation attaches to a newly created thread through the effect.
      if (id === threadId) {
        controller.current?.abort();
        const current = new AbortController();
        controller.current = current;
        void watch(id, current.signal).catch((cause) => {
          if (!current.signal.aborted) { setError(errorText(cause)); setLoading(false); }
        });
      }
      return id;
    } catch (cause) {
      setLoading(false); throw cause;
    } finally {
      submitting.current = false;
    }
  }

  async function stop() {
    const id = activeId.current;
    if (!id) return;
    await request(`/threads/${id}/stop`, {});
    const thread = await request<Thread>(`/threads/${id}`);
    setValues(thread.values); setLoading(false);
  }

  const messages = (values.messages ?? []).map((message) => ({
    ...message,
    text: typeof message.content === "string" ? message.content : message.content
      .filter((block) => block.type === "text" || block.type === "output_text")
      .map((block) => block.text ?? "").join(""),
  }));
  const results = new Map(messages.filter((m) => m.type === "tool").map((m) => [m.tool_call_id, m]));
  const toolCalls = messages.flatMap((message) => (message.tool_calls ?? []).map((call) => ({
    callId: call.id, name: call.name,
    status: results.get(call.id)?.status === "error" ? "error" : results.has(call.id) ? "completed" : "running",
  })));
  return { values, messages, toolCalls, isLoading, isThreadLoading, error, submit, stop };
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : typeof error === "string" ? error : "连接暂时失败，请稍后重试。";
}
