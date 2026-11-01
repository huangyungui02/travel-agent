import { Client } from "@langchain/langgraph-sdk";
import type { BaseMessage } from "@langchain/core/messages";

export type TravelState = {
  messages: BaseMessage[];
  files?: Record<string, { content: string | string[]; modified_at?: string }>;
};

// A relative same-origin URL works for Vite and nginx. Tauri can set an HTTPS API URL.
export const apiUrl = new URL(
  import.meta.env.VITE_AGENT_API_URL || "/api",
  window.location.origin,
).href.replace(/\/$/, "");
export const client = new Client<TravelState>({
  apiUrl,
  callerOptions: { maxRetries: 0 },
  timeoutMs: 15_000,
});
export const assistantId = "travel_agent";

export function errorText(error: unknown): string {
  return error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : "连接暂时失败，请稍后重试。";
}
