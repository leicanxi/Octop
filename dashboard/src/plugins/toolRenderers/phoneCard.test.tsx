/**
 * 小手机（phone）插件渲染器测试。
 *
 * 插件 UI 是给 Dashboard 用的自包含 ESM：React 由宿主通过
 * ``window.__OCTOP_REACT__`` / ``__OCTOP_JSX__`` 注入，手机内容则通过
 * ``host.request`` 读写角色工作区的 ``phone/state.json``。这里按同样的方式
 * 注入 React，并用假的 host 提供状态文件，验证手机外壳（状态栏 / 主屏 / dock）、
 * 各个 App，以及玩家动作的写回语义。
 */
import * as React from "react";
import * as ReactJSX from "react/jsx-runtime";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { OctopPluginUIHost, ToolRenderProps } from "./types";

const PHONE_UI_PATH =
  "../../../../src/octop/infra/agents/plugins/bundled/phone/ui/index.js";
const STATE_PATH = "phone/state.json";

const SAMPLE_STATE = {
  version: 2,
  owner: { name: "阿离", avatar: "🦊" },
  device: {
    model: "iPhone 15 Pro",
    os: "iOS 17.4",
    carrier: "中国移动",
    signal: 3,
    battery: 18,
    charging: false,
    storage_used_gb: 120,
    storage_total_gb: 256,
  },
  wechat: {
    chats: [
      {
        id: "c1",
        name: "小满",
        avatar: "🙂",
        unread: 2,
        messages: [{ side: "them", text: "晚上去看电影吗", time: "20:10" }],
      },
    ],
  },
  moments: [
    {
      id: "m1",
      time: "21:05",
      text: "今天风很大，但晚霞很好看。",
      images: [],
      likes: ["小满"],
      comments: [{ name: "小满", text: "拍得真好" }],
    },
  ],
  notes: [{ id: "n1", title: "买菜", body: "西红柿、鸡蛋", time: "19:30" }],
  sms: [
    {
      id: "s1",
      name: "妈妈",
      avatar: "👩",
      unread: 2,
      messages: [{ side: "them", text: "晚上回家吃饭吗", time: "18:02" }],
    },
  ],
  calls: [
    {
      id: "k1",
      name: "小满",
      avatar: "🙂",
      direction: "missed",
      time: "昨天 21:30",
      duration: "",
    },
    {
      id: "k2",
      name: "阿离",
      avatar: "🦊",
      direction: "in",
      time: "昨天 09:10",
      duration: "3分12秒",
    },
  ],
  photos: [
    {
      id: "p1",
      emoji: "🌇",
      tone: "sunset",
      caption: "天台上的晚霞",
      date: "2026-09-14",
      place: "天台",
      favorite: false,
    },
  ],
};

/** P0 时期写下的状态文件：没有 device / sms / calls / photos，也必须能打开。 */
const V1_STATE = {
  version: 1,
  owner: { name: "阿离", avatar: "🦊" },
  wechat: { chats: [] },
  moments: [],
  notes: [],
};

interface FakeBackend {
  host: OctopPluginUIHost;
  files: Record<string, string>;
  writes: string[];
}

function createBackend(
  initial: Record<string, string> = {},
  opts: { failWrites?: boolean } = {},
): FakeBackend {
  const files: Record<string, string> = { ...initial };
  const writes: string[] = [];

  const request = vi.fn(
    async (path: string, init?: RequestInit): Promise<unknown> => {
      if (path === "/settings/timezone") return { timezone: "Asia/Shanghai" };
      if (/^\/agents\/[^/]+$/.test(path)) return { name: "阿离" };
      if (path.includes("/workspace/file")) {
        const filePath =
          new URL(path, "http://localhost").searchParams.get("path") || "";
        if (init?.method === "PUT") {
          // A stopped agent rejects workspace writes (409 / not running).
          if (opts.failWrites) {
            throw new Error("Request failed: 409 Conflict - agent not running");
          }
          const body = JSON.parse(String(init.body)) as { content: string };
          files[filePath] = body.content;
          writes.push(body.content);
          return { path: filePath, size: body.content.length };
        }
        const content = files[filePath];
        if (content === undefined) {
          throw new Error("Request failed: 404 Not Found - {}");
        }
        return { path: filePath, content };
      }
      throw new Error(`unexpected request: ${path}`);
    },
  );

  const host: OctopPluginUIHost = {
    registerRenderer: vi.fn(),
    getToolContext: () => ({
      agentId: "agent-1",
      threadId: "thread-1",
      locale: "zh",
      theme: "light",
    }),
    patchResult: vi.fn(),
    request: request as OctopPluginUIHost["request"],
    openSidePanel: vi.fn(),
    closeSidePanel: vi.fn(),
  };

  return { host, files, writes };
}

async function renderPhone(
  backend: FakeBackend,
  data: unknown = { app: "home", state_path: STATE_PATH },
): Promise<void> {
  const win = window as unknown as {
    __OCTOP_REACT__?: unknown;
    __OCTOP_JSX__?: unknown;
  };
  win.__OCTOP_REACT__ = React;
  win.__OCTOP_JSX__ = ReactJSX;

  const mod = (await import(/* @vite-ignore */ PHONE_UI_PATH)) as {
    setup: (host: OctopPluginUIHost) => void;
  };
  mod.setup(backend.host);

  const registration = (
    backend.host.registerRenderer as unknown as {
      mock: {
        calls: Array<[{ component: React.ComponentType<ToolRenderProps> }]>;
      };
    }
  ).mock.calls.at(-1)?.[0];
  expect(registration?.component).toBeTruthy();

  const props: ToolRenderProps = {
    pluginId: "phone",
    toolName: "show_phone",
    callId: "call-1",
    status: "done",
    args: { app: "home" },
    data,
    textFallback: "",
    host: backend.host,
    output: "",
    isStreaming: false,
    agentId: "agent-1",
  };

  const Component = registration!.component;
  render(<Component {...props} />);
}

interface WrittenState {
  version?: number;
  owner?: { name?: string };
  device?: Record<string, unknown>;
  notes?: Array<{ title?: string }>;
  moments?: Array<{ likes?: string[] }>;
  sms?: Array<{ unread?: number }>;
  calls?: Array<{ direction?: string; duration?: string; name?: string }>;
  photos?: Array<{ favorite?: boolean }>;
}

function lastWrite(backend: FakeBackend): WrittenState {
  return JSON.parse(backend.writes[backend.writes.length - 1]) as WrittenState;
}

describe("phone plugin card", () => {
  it("renders a phone shell: status bar, home grid and dock", async () => {
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    // 状态栏用的是状态文件里的设备信息，不是写死的。
    expect(await screen.findByText("中国移动")).toBeInTheDocument();
    expect(screen.getByText("阿离 的手机")).toBeInTheDocument();

    // dock 固定「短信 / 通话 / 相册」。
    for (const label of ["短信", "通话", "相册"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    // 其余 App 在主屏宫格。
    for (const label of ["微信", "朋友圈", "备忘录", "设置"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }

    // 角标：2 条未读短信 + 2 条未读微信。
    expect(
      within(screen.getByRole("button", { name: "短信" })).getByText("2"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("button", { name: "通话" })).getByText("1"),
    ).toBeInTheDocument();
  });

  it("opens an app from the home grid and returns home", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "微信" }));
    expect(await screen.findByText("小满")).toBeInTheDocument();
    // 进入 App 后 dock 收起来，只留「回到主屏」指示条。
    expect(screen.queryByRole("button", { name: "相册" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "返回主屏" }));
    expect(
      await screen.findByRole("button", { name: "相册" }),
    ).toBeInTheDocument();
  });

  it("opens a sms thread and clears its unread count", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "短信" }));
    await user.click(await screen.findByRole("button", { name: /妈妈/ }));

    expect(await screen.findByText("晚上回家吃饭吗")).toBeInTheDocument();
    // 手机里不打字：明确告诉玩家去聊天窗口回。
    expect(screen.getByText(/手机里不打字/)).toBeInTheDocument();

    await waitFor(() => expect(backend.writes.length).toBe(1));
    expect(lastWrite(backend).sms?.[0]?.unread).toBe(0);
  });

  it("lists the call log and writes a player redial back", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "通话" }));
    await user.click(await screen.findByRole("button", { name: "未接 小满" }));

    await user.click(await screen.findByRole("button", { name: "回拨" }));

    await waitFor(() => expect(backend.writes.length).toBe(1));
    const written = lastWrite(backend);
    expect(written.calls?.[0]?.direction).toBe("out");
    expect(written.calls?.[0]?.duration).toBe("未接通");
    expect(written.calls?.[0]?.name).toBe("小满");
  });

  it("opens a photo and writes back the player's favorite", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "相册" }));
    await user.click(
      await screen.findByRole("button", { name: "天台上的晚霞" }),
    );

    expect(await screen.findByText(/2026-09-14/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "收藏" }));

    expect(await screen.findByText("⭐ 已收藏")).toBeInTheDocument();
    await waitFor(() => expect(backend.writes.length).toBe(1));
    expect(lastWrite(backend).photos?.[0]?.favorite).toBe(true);
  });

  it("reads boolean-ish fields the way an AI-written file writes them", async () => {
    const user = userEvent.setup();
    // 角色手写的 JSON 可能把布尔写成字符串或 1，两种都算真。
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify({
        ...SAMPLE_STATE,
        device: { ...SAMPLE_STATE.device, charging: "true" },
        photos: [{ ...SAMPLE_STATE.photos[0], favorite: 1 }],
      }),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "相册" }));
    await user.click(
      await screen.findByRole("button", { name: "天台上的晚霞" }),
    );
    expect(await screen.findByText("⭐ 已收藏")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "取消收藏" }));
    await waitFor(() => expect(backend.writes.length).toBe(1));
    expect(lastWrite(backend).photos?.[0]?.favorite).toBe(false);
  });

  it("shows the device facts in 设置", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend);

    await user.click(await screen.findByRole("button", { name: "设置" }));

    expect(await screen.findByText("iPhone 15 Pro")).toBeInTheDocument();
    expect(screen.getByText("18%")).toBeInTheDocument();
    expect(screen.getByText("存储 120 / 256 GB")).toBeInTheDocument();
    expect(screen.getByText(STATE_PATH)).toBeInTheDocument();
  });

  it("re-reads the state file before writing, so an agent edit is not lost", async () => {
    const user = userEvent.setup();
    const backend = createBackend({
      [STATE_PATH]: JSON.stringify(SAMPLE_STATE),
    });
    await renderPhone(backend, { app: "moments", state_path: STATE_PATH });

    // 角色在玩家点赞之前又写了一条备忘录——玩家写回时看到的必须是这一版。
    backend.files[STATE_PATH] = JSON.stringify({
      ...SAMPLE_STATE,
      notes: [
        { id: "n2", title: "角色刚写的", body: "", time: "21:30" },
        ...SAMPLE_STATE.notes,
      ],
    });

    await user.click(await screen.findByRole("button", { name: "点赞" }));

    await waitFor(() => expect(backend.writes.length).toBe(1));
    const written = lastWrite(backend);
    expect(written.moments?.[0]?.likes).toEqual(["小满", "玩家"]);
    expect(written.notes?.[0]?.title).toBe("角色刚写的");
    expect(written.owner?.name).toBe("阿离");
  });

  it("seeds a full v2 template when the state file does not exist yet", async () => {
    const backend = createBackend();
    await renderPhone(backend);

    await waitFor(() => expect(backend.writes.length).toBe(1));
    expect(Object.keys(backend.files)).toEqual([STATE_PATH]);
    const seeded = lastWrite(backend);
    expect(seeded.version).toBe(2);
    expect(seeded.device).toBeTruthy();
    expect(seeded.sms?.length).toBeGreaterThan(0);
    expect(seeded.calls?.length).toBeGreaterThan(0);
    expect(seeded.photos?.length).toBeGreaterThan(0);
    expect(
      await screen.findByText(/已生成模板 phone\/state\.json/),
    ).toBeInTheDocument();
  });

  it("still opens a P0 state file without device / sms / calls / photos", async () => {
    const user = userEvent.setup();
    const backend = createBackend({ [STATE_PATH]: JSON.stringify(V1_STATE) });
    await renderPhone(backend);

    expect(await screen.findByText("阿离 的手机")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "短信" }));
    expect(await screen.findByText(/还没有短信/)).toBeInTheDocument();
  });

  it("reports a malformed state file instead of seeding over it", async () => {
    const backend = createBackend({ [STATE_PATH]: "{ 不是 JSON" });
    await renderPhone(backend);

    expect(await screen.findByText(/不是合法 JSON/)).toBeInTheDocument();
    expect(backend.writes).toEqual([]);
  });

  it("still renders a stopped agent's phone (read-only, no seed write)", async () => {
    const backend = createBackend({}, { failWrites: true });
    await renderPhone(backend);

    expect(await screen.findByText("阿离 的手机")).toBeInTheDocument();
    expect(await screen.findByText(/手机暂时只读/)).toBeInTheDocument();
    expect(backend.writes).toEqual([]);
  });
});
