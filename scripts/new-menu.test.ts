import { expect, test } from "bun:test";
import { readFile } from "node:fs/promises";

class Element {
  nodes: Element[] = [];
  calls: Record<string, any[]> = {};
  constructor() {
    const proxy: Element = new Proxy(this, { get: (target, key: string) => key in target ? (target as any)[key] : (...args: any[]) => {
      if (key === "child") target.nodes.push(args[0]);
      else if (key === "children") target.nodes.push(...args[0]);
      else if (key === "when") { if (args[0]) args[1](proxy); }
      else target.calls[key] = args;
      return proxy;
    }});
    return proxy;
  }
}
const source = await readFile(new URL("../defaults/mods/new-menu/main.js", import.meta.url), "utf8");
function find(root: Element, id: string): Element | undefined {
  if (root?.calls?.id?.[0] === id) return root;
  for (const child of root?.nodes ?? []) { const found = find(child, id); if (found) return found; }
}

test.each([
  ["from local", null, false],
  ["from remote", "remote", false],
  ["after an agent check fails", null, true],
] as const)("cached agents use recovery config %s", async (_scenario, initialMachine, checkFails) => {
  const agent = { key: "claude", name: "Claude", installed: !checkFails, run: ["claude"], icon: "claude.svg" };
  let release!: (value: string | null) => void;
  const pending = new Promise<string | null>(resolve => { release = resolve; });
  const launches: any[] = [], tasks: Promise<void>[] = [];
  let machine = initialMachine;
  const element = () => new Element();
  const values: Record<string, any> = {
    View: class {}, div: element, svg: element, image: element, h_flex: element, v_flex: element,
    theme: () => ({}), spaces: () => [{ id: "space", active: true, machine }], panes: () => [], keys: () => [],
    knownAgents: async () => {
      if (checkFails) throw Error("shell failed");
      return [agent];
    },
    shared: () => ({ "": [agent], remote: [agent] }), setShared() {},
    run: (...args: any[]) => launches.push(args), openOverlay() {}, closeOverlay() {}, props: () => ({}),
    window: () => ({ platform: "windows" }), appHome: () => "/home", readTextFile: () => pending,
  };
  const Menu = new Function(...Object.keys(values), source.replace(/^import .*;\n/gm, "").replace("export default class NewMenu", "return class NewMenu"))(...Object.values(values));
  const cx = { focus_handle: () => ({ focus() {} }), notify() {}, spawn: (f: any) => tasks.push(f(cx)) };
  const view = new Menu(); view.init({}, cx);
  machine = null;
  let tree = view.render();
  find(tree, "claude")!.calls.on_click[0]();
  tree.calls.on_key_down[0]({ key: "1", modifiers: {} });
  expect(launches).toHaveLength(0);
  release("{}"); await Promise.all(tasks);
  tree = view.render();
  find(tree, "claude")!.calls.on_click[0]();
  expect(launches[0][1].argv).toEqual(["python", "/home/local/agent-recovery/agent_recovery.py", "new", "--config", "/home/local/agent-recovery/claude.json"]);
});
