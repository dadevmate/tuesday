"""Tuesday's web browser: a real (headless) Chromium on the owner's machine, so the minds can do things online:
open pages, read them, click, type and fill in forms. Logins persist in data/browser/ (log in once with
`python -m tuesday browser-login`).

Safety: anything that spends, sends, posts, books, deletes or signs up needs the owner's explicit yes. The gate is in
code: a risky click only goes through when the owner's latest message actually confirms it."""
import queue
import re
import threading

from . import config

PROFILE = config.DATA / "browser"
RISKY = re.compile(r"\b(buy|pay|purchase|place order|order now|checkout|check out|send|submit|post|publish|tweet|reply|delete|remove|"
                   r"transfer|book|reserve|confirm|sign ?up|register|subscribe|unsubscribe|apply|donate|accept|agree)\b", re.I)
YES = re.compile(r"\b(yes|yep|yeah|go ahead|do it|confirm(?:ed)?|approved?|proceed|ok(?:ay)?,? (?:do|go|send|buy|book|submit))\b", re.I)

owner_said = {"text": ""}   # the owner's latest message, set by the agent for the confirmation gate


class _Browser:
    """All Playwright work runs on one thread (its sync API isn't thread-safe)."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.page = None
        self.items: list = []
        threading.Thread(target=self._run, daemon=True, name="browser").start()

    def _run(self):
        ctx = None
        while True:
            fn, box, done = self.q.get()
            try:
                if ctx is None:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    PROFILE.mkdir(parents=True, exist_ok=True)
                    ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=True)
                    self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
                box["out"] = fn()
            except Exception as e:
                box["out"] = {"error": str(e)[:300]}
            done.set()

    def call(self, fn, timeout=90):
        box, done = {}, threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            return {"error": "the browser timed out"}
        return box["out"]

    # ---- views ----
    def _snapshot(self, chars=5000):
        p = self.page
        text = p.inner_text("body")[:chars] if p.url != "about:blank" else ""
        els = p.query_selector_all("a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link]")
        self.items, listing = [], []
        for e in els:
            try:
                if not e.is_visible():
                    continue
                label = (e.inner_text() or e.get_attribute("aria-label") or e.get_attribute("placeholder") or e.get_attribute("name")
                         or e.get_attribute("value") or "").strip().replace("\n", " ")[:60]
                tag = e.evaluate("x => x.tagName.toLowerCase()")
                if tag in ("input", "textarea", "select") or label:
                    self.items.append(e)
                    listing.append(f"[{len(self.items) - 1}] {tag}: {label}")
            except Exception:
                continue
            if len(self.items) >= 80:
                break
        return {"url": p.url, "title": p.title(), "text": text, "elements": listing}

    def open(self, url):
        if not re.match(r"https?://", url):
            url = "https://" + url
        return self.call(lambda: (self.page.goto(url, wait_until="domcontentloaded", timeout=45000), self._snapshot())[1])

    def read(self):
        return self.call(lambda: self._snapshot(12000))

    def click(self, target, confirmed=False):
        def go():
            e = self._find(target)
            label = (e.inner_text() or e.get_attribute("value") or e.get_attribute("aria-label") or "").strip()
            if RISKY.search(label) and not (confirmed and YES.search(owner_said["text"])):
                return {"needs_confirmation": f"'{label}' looks like an action that spends, sends, books or signs up. "
                                              "Describe exactly what will happen and ask the owner; click again with confirmed=true only after he says yes."}
            e.click(timeout=15000)
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            return self._snapshot()
        return self.call(go)

    def type(self, target, text, submit=False, confirmed=False):
        def go():
            e = self._find(target)
            e.fill(text, timeout=15000)
            if submit:
                if not (confirmed and YES.search(owner_said["text"])):
                    return {"typed": True, "needs_confirmation": "Submitting sends this form. Ask the owner first; submit with confirmed=true after he says yes."}
                e.press("Enter")
                self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            return self._snapshot()
        return self.call(go)

    def _find(self, target):
        t = str(target).strip()
        if t.isdigit() and int(t) < len(self.items):
            return self.items[int(t)]
        for e in self.items:
            label = (e.inner_text() or e.get_attribute("aria-label") or e.get_attribute("placeholder") or e.get_attribute("name") or "")
            if t.lower() in label.lower():
                return e
        raise ValueError(f"no element matching '{t}'; call browse_read to see the numbered elements")


_b: _Browser | None = None


def browser() -> _Browser:
    global _b
    if _b is None:
        _b = _Browser()
    return _b


def login():
    """Open a visible browser with Tuesday's profile so the owner can log in to sites once."""
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.google.com")
        input("Log in to the sites Tuesday may use, then press Enter here to save and close… ")
        ctx.close()
