import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const itinerary =
  "# 杭州一日游\n\n## 11月1日 · 西湖漫游\n\n| 时间 | 活动 | 描述 | 链接 | 预计费用 |\n|---|---|---|---|---|\n| 11:00–12:00 | 游览西湖 | 轻松散步 | [详情](https://example.com) | ¥0 |";
const created: string[] = [];

test.afterEach(async ({ request }) => {
  for (const threadId of created.splice(0))
    await request.delete(`/api/threads/${threadId}`);
});

test("desktop, mobile and suggestion composer", async ({ page }) => {
  await page.goto("/chat");
  await expect(
    page.getByRole("heading", { name: "下一站， 想去哪里？" }),
  ).toBeVisible();
  await expect(page.getByText("旅行助手已连接")).toBeVisible();
  await page.getByRole("button", { name: /去一座城/ }).click();
  await expect(page.getByRole("textbox", { name: "旅行想法" })).toHaveValue(
    /慢旅行/,
  );
  await page.getByRole("button", { name: "行程草稿", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "好行程，值得慢慢打磨" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "收起行程" }).click();
  await page.screenshot({ path: "test-results/desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("textbox", { name: "旅行想法" })).toBeVisible();
  await page.getByRole("button", { name: "打开菜单" }).click();
  await expect(
    page.getByRole("button", { name: "开始新的旅行" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭菜单" }).click();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await expect
    .poll(() =>
      page
        .locator(".sidebar")
        .evaluate((element) => element.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/mobile.png" });
});

for (const legacy of [false, true]) {
  test(`restores ${legacy ? "legacy array" : "current string"} Markdown file, downloads and isolates new chat`, async ({
    page,
    request,
  }) => {
    const response = await request.post("/api/threads", {
      data: { metadata: { graph_id: "travel_agent", title: "界面联调" } },
    });
    expect(response.ok()).toBeTruthy();
    const { thread_id: id } = await response.json();
    created.push(id);
    const update = await request.post(`/api/threads/${id}/state`, {
      data: {
        values: {
          messages: [
            { type: "human", content: "杭州旅行测试", id: "fixture-human" },
            {
              type: "ai",
              content: "行程已保存，可以查看草稿。",
              id: "fixture-ai",
            },
          ],
          files: {
            "/daily-itinerary.md": {
              content: legacy ? itinerary.split("\n") : itinerary,
              encoding: "utf-8",
              created_at: new Date().toISOString(),
              modified_at: new Date().toISOString(),
            },
          },
        },
        as_node: "model",
      },
    });
    expect(update.ok(), await update.text()).toBeTruthy();
    // A seeded checkpoint must be terminal, like a successfully completed run.
    const finished = await request.post(`/api/threads/${id}/state`, {
      data: { values: null, as_node: "__end__" },
    });
    expect(finished.ok(), await finished.text()).toBeTruthy();
    await page.goto(`/chat/${id}`);
    await expect(page.getByText("行程已保存，可以查看草稿。")).toBeVisible();
    await page.getByRole("button", { name: "行程草稿", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "杭州一日游" }),
    ).toBeVisible();
    await expect(page.getByRole("cell", { name: "游览西湖" })).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "预计费用" }),
    ).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载行程" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("旅行行程.md");
    expect(await readFile((await download.path())!, "utf8")).toBe(itinerary);
    await page.reload();
    await expect(page.getByText("行程已保存，可以查看草稿。")).toBeVisible();
    await page.getByRole("button", { name: "开始新的旅行" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await page.getByRole("button", { name: "行程草稿", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "好行程，值得慢慢打磨" }),
    ).toBeVisible();
  });
}

test("shows server error without an endless spinner", async ({ page }) => {
  await page.route("**/api/threads/search", (route) =>
    route.fulfill({ status: 503, json: { detail: "服务暂不可用" } }),
  );
  await page.goto("/chat");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("服务暂未连接")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
});

test("live model streaming, current-date tool and reload", async ({ page }) => {
  test.skip(
    process.env.LIVE_AGENT_TEST !== "1",
    "Opt-in: uses the configured model API.",
  );
  await page.goto("/chat");
  await page
    .getByRole("textbox", { name: "旅行想法" })
    .fill("你好，请用一句话问候我，不要调用工具。");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  await expect(page.locator(".assistant-message .markdown")).toHaveCount(1, {
    timeout: 90_000,
  });
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0, {
    timeout: 90_000,
  });
  const id = page.url().split("/").at(-1)!;
  created.push(id);
  await expect(page.locator(".assistant-message .markdown")).not.toBeEmpty();
  await page
    .getByRole("textbox", { name: "旅行想法" })
    .fill("今天的北京时间日期是什么？请先调用 current_date，再仅回复日期。");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0, {
    timeout: 90_000,
  });
  await page.locator(".tool-activity summary").click();
  await expect(page.getByText("确认旅行日期", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".assistant-message .markdown")).toHaveCount(2);
  await page.screenshot({ path: "test-results/live-chat.png" });
});

test("stop cancels the server run", async ({ page, request }) => {
  test.skip(
    process.env.LIVE_AGENT_TEST !== "1",
    "Opt-in: starts and cancels a real run.",
  );
  await page.goto("/chat");
  await page
    .getByRole("textbox", { name: "旅行想法" })
    .fill("你好，请介绍你能帮我做的旅行规划。");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/.+/);
  const id = page.url().split("/").at(-1)!;
  created.push(id);
  await expect
    .poll(async () => {
      const response = await request.get(`/api/threads/${id}/runs`);
      return (await response.json()).some((run: { status: string }) =>
        ["running", "pending"].includes(run.status),
      );
    })
    .toBeTruthy();
  await page.getByRole("button", { name: "停止生成" }).click();
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
  await expect
    .poll(async () => {
      const response = await request.get(`/api/threads/${id}/runs`);
      return (await response.json()).map(
        (run: { status: string }) => run.status,
      );
    })
    .toEqual(["interrupted"]);
  await page.reload();
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "开始新的旅行" }),
  ).toBeEnabled();
});
