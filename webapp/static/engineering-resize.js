// Browser-local panel sizing. No project input or result is changed here.
export const ENGINEERING_SPLIT_MIN_WIDTH = 1080;
export const ENGINEERING_VIEW_MIN_WIDTH = 380;
export const ENGINEERING_EDITOR_MIN_WIDTH = 480;
export const ENGINEERING_SEPARATOR_WIDTH = 12;
const DEFAULT_FRACTION = 0.45;
const MAX_FRACTION = 0.65;
const STORAGE_KEY = "lre.workspace.engineering-width.v1";

export function mountEngineeringResize(container, separator, {
  window: win = globalThis.window, storageKey = STORAGE_KEY, storage,
} = {}) {
  const noop = {refresh() {}, reset() {}, dispose() {}};
  if (!container || !separator || !win) return noop;
  let savedStorage = storage;
  try { if (savedStorage === undefined) savedStorage = win.localStorage; } catch { savedStorage = null; }
  // null means proportional automatic sizing, as distinct from a saved pixel width.
  let preferred = null;
  try {
    const saved = savedStorage?.getItem(storageKey);
    if (saved != null && /^\d+(?:\.\d+)?$/.test(String(saved)) && Number.isFinite(Number(saved)) && Number(saved) > 0)
      preferred = Math.max(ENGINEERING_VIEW_MIN_WIDTH, Math.round(Number(saved)));
  } catch { /* Storage restrictions do not disable the splitter. */ }

  let available = 0, width = ENGINEERING_VIEW_MIN_WIDTH, drag = null, disposed = false;
  const listeners = [];
  const listen = (target, event, handler) => {
    target.addEventListener(event, handler);
    listeners.push(() => target.removeEventListener(event, handler));
  };
  const measure = () => {
    const measured = container.getBoundingClientRect?.().width ?? container.clientWidth ?? 0;
    return Number.isFinite(measured) && measured > 0 ? measured : 0;
  };
  const wide = () => available >= ENGINEERING_SPLIT_MIN_WIDTH;
  const maximum = () => Math.max(ENGINEERING_VIEW_MIN_WIDTH, Math.floor(Math.min(
    available * MAX_FRACTION, available - ENGINEERING_EDITOR_MIN_WIDTH - ENGINEERING_SEPARATOR_WIDTH,
  )));
  const automatic = () => available * DEFAULT_FRACTION;
  const clamp = value => Math.min(maximum(), Math.max(ENGINEERING_VIEW_MIN_WIDTH, Math.round(value)));

  separator.setAttribute("role", "separator");
  separator.setAttribute("aria-orientation", "vertical");
  separator.setAttribute("aria-label", "调整工程视图宽度");
  separator.setAttribute("aria-controls", "engineering-view");
  separator.setAttribute("title", "拖动调整工程视图宽度；方向键微调，双击恢复默认宽度");

  function paint(value = preferred ?? automatic()) {
    separator.hidden = !wide();
    separator.tabIndex = wide() ? 0 : -1;
    separator.setAttribute("aria-disabled", String(!wide()));
    if (!available) return; // A hidden editor must not replace a valid layout preference.
    width = clamp(value);
    container.style.setProperty("--engineering-view-width", `${width}px`);
    separator.setAttribute("aria-valuemin", String(ENGINEERING_VIEW_MIN_WIDTH));
    separator.setAttribute("aria-valuemax", String(maximum()));
    separator.setAttribute("aria-valuenow", String(width));
    separator.setAttribute("aria-valuetext", `工程视图宽度 ${width} 像素`);
  }
  function save(value) {
    preferred = value;
    try {
      if (value === null && typeof savedStorage?.removeItem === "function") savedStorage.removeItem(storageKey);
      else savedStorage?.setItem(storageKey, value === null ? "auto" : String(value));
    } catch { /* Keep the current layout usable when storage is unavailable. */ }
  }
  function finish(commit) {
    if (!drag) return;
    const previous = drag;
    drag = null;
    container.classList.toggle("engineering-resizing", false);
    if (commit && width !== previous.width) save(width);
    else preferred = previous.preferred;
    paint();
    try { separator.releasePointerCapture(previous.id); } catch { /* Capture may already be lost. */ }
  }
  function refresh() {
    if (disposed) return;
    const next = measure();
    if (next !== available) finish(false);
    available = next;
    paint(drag ? width : preferred ?? automatic());
  }
  function reset() {
    if (disposed) return;
    finish(false);
    save(null);
    paint();
  }
  listen(separator, "pointerdown", event => {
    refresh();
    if (!wide() || drag || event.button !== 0 || event.isPrimary === false || !Number.isFinite(event.clientX)) return;
    event.preventDefault();
    drag = {id: event.pointerId, x: event.clientX, width, preferred};
    try { separator.setPointerCapture(event.pointerId); } catch { drag = null; return; }
    separator.focus({preventScroll: true});
    container.classList.toggle("engineering-resizing", true);
  });
  listen(separator, "pointermove", event => {
    if (!drag || event.pointerId !== drag.id || !Number.isFinite(event.clientX)) return;
    paint(drag.width - (event.clientX - drag.x));
  });
  listen(separator, "pointerup", event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (Number.isFinite(event.clientX)) paint(drag.width - (event.clientX - drag.x));
    finish(true);
  });
  for (const name of ["pointercancel", "lostpointercapture"]) listen(separator, name, event => {
    if (drag && event.pointerId === drag.id) finish(false);
  });
  listen(separator, "keydown", event => {
    if (event.key === "Escape" && drag) { event.preventDefault(); finish(false); return; }
    if (!wide() || drag) return;
    if (event.key === "Enter") { event.preventDefault(); reset(); return; }
    const step = event.shiftKey ? 32 : 16;
    const values = {ArrowLeft: width + step, ArrowRight: width - step, Home: ENGINEERING_VIEW_MIN_WIDTH, End: maximum()};
    if (!Object.hasOwn(values, event.key)) return;
    event.preventDefault();
    paint(values[event.key]);
    save(width);
  });
  listen(separator, "dblclick", event => {
    if (!wide()) return;
    event.preventDefault();
    reset();
  });
  listen(win, "resize", refresh);
  listen(win, "blur", () => finish(false));
  const observer = typeof win.ResizeObserver === "function" ? new win.ResizeObserver(refresh) : null;
  observer?.observe(container);
  refresh();
  return {
    refresh, reset,
    dispose() {
      if (disposed) return;
      finish(false);
      disposed = true;
      observer?.disconnect();
      for (const remove of listeners) remove();
    },
  };
}
