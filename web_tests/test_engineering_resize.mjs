/** Verify panel layout persistence and accessible resizing independently of project state. */
import assert from "node:assert/strict";
import test from "node:test";
import {mountEngineeringResize} from "../webapp/static/engineering-resize.js";

const key = "lre.workspace.engineering-width.v1";
function surface() {
  const listeners = new Map(), attributes = new Map(), properties = new Map(), classes = new Set(), capture = new Set();
  return {
    listeners, capture, hidden: false, tabIndex: 0,
    style: {setProperty: (name, value) => properties.set(name, value), getPropertyValue: name => properties.get(name)},
    classList: {toggle(name, active) {active ? classes.add(name) : classes.delete(name);}, contains: name => classes.has(name)},
    setAttribute: (name, value) => attributes.set(name, String(value)), getAttribute: name => attributes.get(name),
    addEventListener(name, callback) {if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback);},
    removeEventListener(name, callback) {listeners.get(name)?.delete(callback);},
    dispatch(name, properties = {}) {
      const event = {button: 0, isPrimary: true, pointerId: 7, clientX: 700, defaultPrevented: false,
        preventDefault() {this.defaultPrevented = true;}, ...properties};
      for (const callback of listeners.get(name) || []) callback(event);
      return event;
    },
    setPointerCapture: id => capture.add(id), releasePointerCapture: id => capture.delete(id), focus() {},
  };
}
function harness({saved = null, size = 1400, blocked = false, storageKey} = {}) {
  const container = surface(), separator = surface(), win = surface(), writes = [], removals = [];
  let observer;
  container.getBoundingClientRect = () => ({width: size, left: 340});
  win.innerWidth = 2100; // The editor width, not window width, determines stacking.
  win.localStorage = {
    getItem: requested => {assert.equal(requested, storageKey || key); if (blocked) throw new Error("blocked"); return saved;},
    setItem: (name, value) => {if (blocked) throw new Error("blocked"); saved = value; writes.push([name, value]);},
    removeItem: name => {if (blocked) throw new Error("blocked"); saved = null; removals.push(name);},
  };
  win.ResizeObserver = class {
    constructor(callback) {this.callback = callback; observer = this;}
    observe(target) {assert.equal(target, container); this.observed = target;}
    disconnect() {this.disconnected = true;}
  };
  const controller = mountEngineeringResize(container, separator, {window: win, storageKey});
  return {container, separator, win, writes, removals, controller, observer,
    width: () => Number.parseFloat(container.style.getPropertyValue("--engineering-view-width")),
    key: name => separator.dispatch("keydown", {key: name}),
    resize(value) {size = value; observer.callback();},
    setSize(value) {size = value;},
    pointer: (name, properties) => separator.dispatch(name, properties),
  };
}

test("automatic sizing follows the actual editor width, including hidden initial mounting", () => {
  const h = harness({size: 0});
  assert.equal(h.separator.hidden, true);
  assert.equal(h.separator.tabIndex, -1);
  assert.ok(Number.isNaN(h.width()));
  h.resize(1400);
  assert.equal(h.width(), 630);
  assert.equal(h.separator.hidden, false);
  assert.equal(h.separator.getAttribute("aria-valuenow"), "630");
  h.resize(1800); assert.equal(h.width(), 810);
  h.resize(1079); assert.equal(h.separator.hidden, true);
  h.resize(1080); assert.equal(h.separator.hidden, false); assert.equal(h.width(), 486);
  assert.equal(h.writes.length, 0);
  h.controller.dispose();
});

test("malformed storage is ignored and a saved width survives viewport constraints", () => {
  for (const saved of [null, "", "auto", "{}", "NaN", "Infinity", "500px", "-40", "0"] ) {
    const h = harness({saved}); assert.equal(h.width(), 630, String(saved)); h.controller.dispose();
  }
  const h = harness({saved: "850"});
  assert.equal(h.width(), 850);
  h.resize(1080); assert.equal(h.width(), 588);
  assert.equal(h.separator.getAttribute("aria-valuemax"), "588");
  // A simple pointer focus without movement must not replace the preferred width.
  h.pointer("pointerdown", {clientX: 700}); h.pointer("pointerup", {clientX: 700});
  assert.equal(h.writes.length, 0);
  h.resize(900); h.key("End"); assert.equal(h.writes.length, 0);
  h.resize(1400); assert.equal(h.width(), 850);
  h.resize(0); h.controller.refresh(); h.resize(1800); assert.equal(h.width(), 850);
  assert.equal(h.writes.length, 0);
  h.controller.dispose();
});

test("pointer dragging grows the right pane to the left and saves only on completion", () => {
  const h = harness();
  h.pointer("pointerdown", {clientX: 700}); assert.ok(h.separator.capture.has(7));
  assert.equal(h.container.classList.contains("engineering-resizing"), true);
  h.pointer("pointermove", {clientX: 600}); assert.equal(h.width(), 730);
  assert.equal(h.writes.length, 0);
  h.pointer("pointerup", {clientX: 580}); assert.equal(h.width(), 750);
  assert.deepEqual(h.writes, [[key, "750"]]); assert.equal(h.separator.capture.size, 0);
  h.pointer("pointerdown", {clientX: 580}); h.pointer("pointerup", {clientX: 5000});
  assert.equal(h.width(), 380);
  h.pointer("pointerdown", {clientX: 5000}); h.pointer("pointerup", {clientX: -5000});
  assert.equal(h.width(), 908); // Leaves the editor's 480 px plus the 12 px separator.
  assert.equal(h.container.classList.contains("engineering-resizing"), false);
  h.controller.dispose();
});

test("secondary pointers cannot start or take over a resize", () => {
  const h = harness();
  h.pointer("pointerdown", {button: 2}); assert.equal(h.separator.capture.size, 0);
  h.pointer("pointerdown", {isPrimary: false}); assert.equal(h.separator.capture.size, 0);
  h.pointer("pointerdown", {clientX: 700});
  h.pointer("pointermove", {pointerId: 9, clientX: 200});
  h.pointer("pointerup", {pointerId: 9, clientX: 200});
  assert.equal(h.width(), 630); assert.equal(h.writes.length, 0);
  h.pointer("pointermove", {clientX: 650}); h.pointer("pointerup", {clientX: 650});
  assert.equal(h.width(), 680); assert.deepEqual(h.writes, [[key, "680"]]);
  h.controller.dispose();
});

test("cancellation, blur and layout changes roll back active drags without persisting", () => {
  for (const cancel of ["pointercancel", "lostpointercapture", "Escape", "blur", "resize"]) {
    const h = harness({saved: "700"});
    h.pointer("pointerdown", {clientX: 700}); h.pointer("pointermove", {clientX: 600});
    assert.equal(h.width(), 800);
    if (cancel === "Escape") h.key(cancel);
    else if (cancel === "blur") h.win.dispatch(cancel);
    else if (cancel === "resize") h.resize(900);
    else h.pointer(cancel);
    assert.equal(h.separator.capture.size, 0, cancel); assert.equal(h.writes.length, 0, cancel);
    h.resize(1400); assert.equal(h.width(), 700, cancel);
    h.pointer("pointerup", {clientX: 600}); assert.equal(h.writes.length, 0, cancel);
    h.controller.dispose();
  }
});

test("keyboard widths, bound keys and reset have the same limits as pointer resizing", () => {
  const h = harness();
  assert.equal(h.key("ArrowLeft").defaultPrevented, true); assert.equal(h.width(), 646);
  h.separator.dispatch("keydown", {key: "ArrowLeft", shiftKey: true}); assert.equal(h.width(), 678);
  h.key("ArrowRight"); assert.equal(h.width(), 662);
  h.key("Home"); assert.equal(h.width(), 380);
  h.key("End"); assert.equal(h.width(), 908);
  assert.equal(h.separator.getAttribute("aria-valuetext"), "工程视图宽度 908 像素");
  h.key("Enter"); assert.equal(h.width(), 630); assert.deepEqual(h.removals, [key]);
  h.resize(1800); assert.equal(h.width(), 810);
  h.key("ArrowLeft"); h.separator.dispatch("dblclick"); assert.equal(h.width(), 810);
  h.resize(1400); assert.equal(h.width(), 630);
  assert.equal(h.key("Tab").defaultPrevented, false);
  h.controller.dispose();
});

test("storage and capture failures retain keyboard operation, and disposal releases all resources", () => {
  const h = harness({blocked: true});
  h.separator.setPointerCapture = () => {throw new Error("pointer expired");};
  assert.doesNotThrow(() => h.pointer("pointerdown"));
  assert.equal(h.container.classList.contains("engineering-resizing"), false);
  assert.doesNotThrow(() => h.key("ArrowLeft")); assert.equal(h.width(), 646);
  h.controller.dispose(); h.controller.dispose();
  assert.equal(h.observer.disconnected, true);
  const before = h.width();
  h.key("End"); h.resize(1900); h.controller.reset(); h.controller.refresh();
  assert.equal(h.width(), before);
  for (const target of [h.separator, h.win]) assert.ok([...target.listeners.values()].every(items => items.size === 0));

  const active = harness();
  active.pointer("pointerdown", {clientX: 700}); active.pointer("pointermove", {clientX: 500});
  active.controller.dispose(); assert.equal(active.separator.capture.size, 0); assert.equal(active.writes.length, 0);
});

test("custom storage keys are supported and unavailable DOM returns a safe controller", () => {
  const h = harness({storageKey: "layout.test"});
  h.key("ArrowLeft"); assert.deepEqual(h.writes, [["layout.test", "646"]]);
  h.controller.reset(); assert.deepEqual(h.removals, ["layout.test"]); h.controller.dispose();
  assert.doesNotThrow(() => {
    const empty = mountEngineeringResize(null, null); empty.refresh(); empty.reset(); empty.dispose();
  });
});
