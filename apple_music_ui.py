"""Plays a specific song in the Apple Music Windows app by driving its UI.

Apple Music has no API for "play this song", so this does what a person would:
search, open the top song's album, and choose "Play '<song>'" from that track's
menu. It works through UI Automation (the accessibility interface), so nothing
is clicked with the mouse. Apple Music does come to the front while it works
(setting its search box activates it), so the previously focused window is put
back afterwards, and a minimized Apple Music is minimized again.
"""

import ctypes
import re
import time

import uiautomation as auto
from uiautomation import uiautomation as _uia

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.GetForegroundWindow.restype = ctypes.c_void_p
for _f in (user32.SetForegroundWindow, user32.BringWindowToTop, user32.IsIconic):
    _f.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7
PLAY_ITEM = re.compile(r"^Play [\"“‘']")  # "Play “Uptown Girl”", not "Play Next" / "Play Last"


TREE_SCOPE_DESCENDANTS = 4


def find(parent, control_type=None, name=None, automation_id=None):
    """First descendant of `parent` matching all the given properties, or None.
    Uses UI Automation's own search (one call) rather than uiautomation's
    Python-side tree walk, which makes a round trip to Apple Music per element
    and took over a second per lookup. It searches the raw view: the default
    (control) view hides some of Apple Music's elements, like its Search button."""
    client = _uia._AutomationClient.instance().IUIAutomation
    raw_view = client.CreateCacheRequest()
    raw_view.TreeFilter = client.RawViewCondition
    conditions = [client.CreatePropertyCondition(prop, value) for prop, value in (
        (auto.PropertyId.ControlTypeProperty, control_type),
        (auto.PropertyId.NameProperty, name),
        (auto.PropertyId.AutomationIdProperty, automation_id)) if value is not None]
    condition = conditions[0]
    for extra in conditions[1:]:
        condition = client.CreateAndCondition(condition, extra)
    try:
        element = parent.Element.FindFirstBuildCache(TREE_SCOPE_DESCENDANTS, condition, raw_view)
    except Exception:
        return None
    return auto.Control.CreateControlFromElement(element) if element else None


# (query, page id) of the last search this module ran, so a repeat request can
# reuse results that are still showing (the same search doesn't reload the page).
_last_search = (None, None)


class PlayError(Exception):
    """A step failed; the message says which, for the console."""


def play_song(query, timeout=10):
    """Search Apple Music for `query` and play the top song result.
    Returns (title, artist) of what was started, e.g. ("Uptown Girl", "Billy Joel")."""
    window = auto.WindowControl(searchDepth=1, ClassName="WinUIDesktopWin32WindowClass",
                                Name="Apple Music")
    if not window.Exists(1):
        raise PlayError("Apple Music isn't open")
    hwnd = window.NativeWindowHandle
    previous = user32.GetForegroundWindow()
    was_minimized = bool(user32.IsIconic(hwnd))
    if was_minimized:
        # Menus don't open in a minimized window; show it without activating it.
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(0.3)
    try:
        return _search_and_play(window, query, timeout)
    finally:
        if was_minimized:
            user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        if previous and previous != hwnd:
            focus(previous)


def _search_and_play(window, query, timeout):
    global _last_search
    box = find(window, auto.ControlType.EditControl, automation_id="TextBox")
    if not box:
        raise PlayError("couldn't find Apple Music's search box")
    # If our last search for this exact text is still on screen, use its results:
    # searching the same text again doesn't reload the page, so there'd be no
    # change to wait for.
    key = query.strip().lower()
    tile = None
    if _last_search[0] == key and _last_search[1] == _page_id(window):
        tile = _top_song(window)
    if not tile:
        # Search, then wait for a new results page (not the one already showing).
        before = _page_id(window)
        box.GetValuePattern().SetValue(query)
        # Setting the text redraws the box, and for a moment its Search button
        # can't be pressed yet; look it up again until it can.
        query_button = _wait(lambda: getattr(find(window, auto.ControlType.ButtonControl,
                                                  automation_id="QueryButton"),
                                             "GetInvokePattern", lambda: None)(), 2)
        if not query_button:
            raise PlayError("Apple Music's search button didn't respond")
        query_button.Invoke()
        tile = _wait(lambda: _page_id(window) not in (before, None) and _top_song(window), timeout)
        if tile:
            _last_search = (key, _page_id(window))
    if not tile:
        raise PlayError(f"no song found for “{query}”")
    title, subtitle = (t.Name for t in tile.GetChildren()[0].GetChildren()[1:3])
    artist = subtitle.split("·")[-1].strip()  # "Song · Billy Joel" -> "Billy Joel"

    # Quickest: the same song in the results' "Songs" list, which has a menu.
    row = _songs_row(window, title, artist)
    if not row:
        # Otherwise open the song's album and use its track row.
        before = _page_id(window)
        tile.GetInvokePattern().Invoke()
        row = _wait(lambda: _page_id(window) not in (before, None) and _track_row(window, title), timeout)
        if not row:
            raise PlayError(f"couldn't find “{title}” on its album page")
    _play_from_menu(window, row)
    return title, artist


def _play_from_menu(window, row):
    """Choose "Play '<title>'" from a song row's "More" menu. The button only
    exists on screen once the row is scrolled into view."""
    row.GetScrollItemPattern().ScrollIntoView()
    more = _wait(lambda: (lambda b: b if b and b.BoundingRectangle.width() > 0 else None)(
        find(row, auto.ControlType.ButtonControl, name="More")), 2)
    if not more:
        raise PlayError("the song's menu button didn't appear")
    more.GetInvokePattern().Invoke()
    item = _wait(lambda: _play_menu_item(window), 3)
    if not item:
        raise PlayError("the song's menu didn't open")
    item.GetInvokePattern().Invoke()


def _songs_row(window, title, artist):
    """The "<title> <artist>" row in the search results' Songs section, if any."""
    page = find(_content(window), auto.ControlType.ListControl) if _content(window) else None
    section = find(page, auto.ControlType.ListItemControl, name="Songs") if page else None
    if not section:
        return None
    return find(section, auto.ControlType.ListItemControl, name=f"{title} {artist}")


def _top_song(window):
    top = find(window, auto.ControlType.ListItemControl, name="Top Results")
    if not top:
        return None
    return next((t for t in _list_items(top, 4) if re.search(r"\bSong\b", t.Name)), None)


def _track_row(window, title):
    # Only look at the track list (rows directly on the page, or inside "Disc N"
    # groups), not the "More By" / "Featured On" shelves; each lookup is a round
    # trip to Apple Music, so scanning the whole page is slow.
    content = _content(window)
    page = find(content, auto.ControlType.ListControl) if content else None
    if not page:
        return None
    row = re.compile(rf"^Track \d+ {re.escape(title)} \d")
    for item in page.GetChildren():
        if item.Name.startswith("Track "):
            candidates = [item]
        elif item.Name.startswith("Disc "):
            candidates = _list_items(item, 3)
        else:
            continue
        match = next((r for r in candidates if row.match(r.Name)), None)
        if match:
            return match
    return None


def _play_menu_item(window):
    menu = find(window, auto.ControlType.MenuControl)
    if not menu:
        return None
    return next((m for m in menu.GetChildren() if PLAY_ITEM.match(m.Name)), None)


def _content(window):
    # Looked up fresh each time: Apple Music replaces this element on every page
    # change, and a stale one finds nothing.
    return find(window, auto.ControlType.GroupControl, name="Content")


def _page_id(window):
    """Identifies the page currently shown; changes when Apple Music navigates."""
    content = _content(window)
    page = find(content, auto.ControlType.ListControl) if content else None
    return tuple(page.GetRuntimeId()) if page else None


def _list_items(root, depth):
    found = []

    def walk(node, d):
        if d > depth:
            return
        for child in node.GetChildren():
            if child.ControlTypeName == "ListItemControl":
                found.append(child)
            walk(child, d + 1)

    walk(root, 0)
    return found


def _wait(check, timeout, step=0.15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(step)
    return None


def focus(hwnd):
    """Bring hwnd to the front. Windows only lets the foreground app do that, so
    briefly share input state with it first."""
    current = user32.GetForegroundWindow()
    if current == hwnd:
        return
    their_thread = user32.GetWindowThreadProcessId(current, None)
    my_thread = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(my_thread, their_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(my_thread, their_thread, False)


if __name__ == "__main__":
    import sys
    t = time.monotonic()
    print(play_song(" ".join(sys.argv[1:]) or "uptown girl"), f"{time.monotonic() - t:.1f}s")
