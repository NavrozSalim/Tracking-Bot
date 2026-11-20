"""Page helpers at a person's pace: open a page, wait for it, read its text, click a button."""
import logging
import random

log = logging.getLogger("trackingbot")

PAUSE_MIN_MS, PAUSE_MAX_MS = 400, 900
LOAD_WAIT_MS = 6000
NAV_TIMEOUT_MS = 60000
CLICK_TRIES = 3


class PageError(Exception):
    pass


def pause(page, lo=None, hi=None):
    lo = PAUSE_MIN_MS if lo is None else lo
    hi = PAUSE_MAX_MS if hi is None else hi
    try:
        page.wait_for_timeout(random.randint(lo, max(lo, hi)))
    except Exception:
        pass


def settle(page, ms=800):
    for state, kw in (("domcontentloaded", {}), ("load", {"timeout": LOAD_WAIT_MS})):
        try:
            page.wait_for_load_state(state, **kw)
        except Exception:
            pass
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass
    pause(page)


def goto(page, url):
    log.info("      open %s", str(url).split("?")[0][:150])
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as first:
        if "timeout" not in str(first).lower():
            raise
        try:
            page.goto(url, wait_until="commit", timeout=NAV_TIMEOUT_MS)
        except Exception:
            raise PageError(f"page did not load in {NAV_TIMEOUT_MS // 1000}s: {url}") from None
        settle(page, 4000)


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:
        return ""


# Close marketing pop-ups that cover a button (never a window with form fields).
_DISMISS_POPUPS_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const words = /subscribe|newsletter|coupon|sign up for|% off|\$\d+ off|select your (country|region|location)/i;
  let n = 0;
  for (const el of document.querySelectorAll('[class*=modal i],[class*=popup i],[role=dialog]')) {
    if (!vis(el) || !words.test(el.innerText || '') || el.querySelector('input:not([type=hidden])')) continue;
    const c = [...el.querySelectorAll('[class*=close i],[aria-label*=close i]')]
      .find(x => vis(x) && x.getBoundingClientRect().width < 80);
    if (c) { c.click(); n++; }
  }
  return n;
}"""


def click_loc(page, loc, tries=CLICK_TRIES):
    """Click with 3 tries; between tries close pop-ups and wait. Last resort: click from the page's script."""
    last = None
    url_before = getattr(page, "url", None)
    for _ in range(tries):
        try:
            loc.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
        except Exception:
            pass
        try:
            loc.hover(timeout=3000)
        except Exception:
            pass
        pause(page, 250, 650)
        log.info("      click %s", _label(loc))
        try:
            loc.click(timeout=10000)
            return
        except Exception as e:
            last = e
        if url_before is not None and getattr(page, "url", None) not in (None, url_before):
            return
        try:
            page.evaluate(_DISMISS_POPUPS_JS)
            page.wait_for_timeout(2000)
        except Exception:
            pass
    try:
        loc.evaluate("el => el.click()")
        return
    except Exception:
        pass
    raise last


def _label(loc):
    try:
        return " ".join(loc.inner_text(timeout=1000).split())[:40] or "button"
    except Exception:
        return "button"
