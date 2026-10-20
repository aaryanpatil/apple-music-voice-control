"""Plays a specific song, album or playlist in the Apple Music Windows app by
driving its UI.

Apple Music has no API for "play this song", so this does what a person would:
search, then choose "Play '<song>'" from the top song's menu, or open the top
album or playlist and press its Play (or Shuffle) button. The user's own
playlists are opened from the sidebar instead. It works through UI Automation (the accessibility interface), so nothing
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
# uiautomation sleeps 0.5 s after every action unless told otherwise; every
# action here passes waitTime=0, and the _wait loops wait for what's actually
# needed instead.
PLAY_ITEM = re.compile(r"^Play [\"“‘']")  # "Play “Uptown Girl”", not "Play Next" / "Play Last"
SIDEBAR_OPEN_WIDTH = 100  # sidebar items are ~55 px wide when it's collapsed to icons, ~355 open


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
    return _in_apple_music(lambda window: _search_and_play(window, query, timeout))


def play_album(query, shuffle=False, timeout=10):
    """Search Apple Music for `query` and play (or shuffle) the top album result.
    Returns (title, artist), e.g. ("Thriller", "Michael Jackson")."""
    return _in_apple_music(lambda window: _play_collection(window, query, ["Album"], shuffle, timeout)[:2])


def play_playlist(query, shuffle=False, timeout=10):
    """Search Apple Music for `query` and play (or shuffle) the top playlist result.
    Returns (title, curator), e.g. ("Today's Hits", "Apple Music Pop")."""
    return _in_apple_music(lambda window: _play_collection(window, query, ["Playlist"], shuffle, timeout)[:2])


def play_album_or_playlist(query, shuffle=False, timeout=10):
    """Like play_album, but takes whichever of the top album and playlist results
    comes first. For "shuffle <name>", which could be either. Returns (title,
    artist or curator, "album" or "playlist")."""
    return _in_apple_music(lambda window: _play_collection(window, query, ["Album", "Playlist"],
                                                           shuffle, timeout))


def play_library_playlist(choose, shuffle=False, timeout=10):
    """Play (or shuffle) one of the user's own playlists: choose(names) is given
    their names and returns one of them, or None. Returns (title, "your library"),
    or None if none was chosen."""
    return _in_apple_music(lambda window: _play_library_playlist(window, choose, shuffle, timeout))


def set_shuffle(on):
    """Turn Apple Music's shuffle on or off. Returns False if it already was."""
    return _in_apple_music(lambda window: _set_shuffle(window, on))


def _in_apple_music(action):
    """Run action(window) on Apple Music's window, then put the user's window back."""
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
        return action(window)
    finally:
        if was_minimized:
            user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        if previous and previous != hwnd:
            focus(previous)


def _search(window, query, pick, timeout):
    """Search for `query` and return pick(window) once the results show it, or None."""
    global _last_search
    box = search_box(window)
    if not box:
        raise PlayError("couldn't find Apple Music's search box")
    # If our last search for this exact text is still on screen, use its results:
    # searching the same text again doesn't reload the page, so there'd be no
    # change to wait for.
    key = query.strip().lower()
    if _last_search[0] == key and _last_search[1] == _page_id(window):
        found = pick(window)
        if found:
            return found
    # Search, then wait for a new results page (not the one already showing).
    before = _page_id(window)
    box.GetValuePattern().SetValue(query, waitTime=0)
    # Setting the text redraws the box, and for a moment its Search button
    # can't be pressed yet; look it up again until it can.
    query_button = _wait(lambda: getattr(find(window, auto.ControlType.ButtonControl,
                                              automation_id="QueryButton"),
                                         "GetInvokePattern", lambda: None)(), 2)
    if not query_button:
        raise PlayError("Apple Music's search button didn't respond")
    query_button.Invoke(waitTime=0)
    found = _wait(lambda: _page_id(window) not in (before, None) and pick(window), timeout)
    if found:
        _last_search = (key, _page_id(window))
    return found


def _play_collection(window, query, kinds, shuffle, timeout):
    """Search, open the top result of one of `kinds` ("Album"), or else the first in
    the results' section for it ("Albums"), and press Play (or Shuffle) on its page.
    Returns (title, the line under it, "album" or "playlist")."""
    def pick(w):
        tile = _top_result(w, kinds)
        if tile:
            return tile, next(k for k in kinds if re.search(rf"\b{k}\s·", tile.Name))
        return next(((item, k) for k in kinds if (item := _section_first(w, k + "s"))), None)

    what = " or ".join(kinds).lower()
    found = _search(window, query, pick, timeout)
    if not found:
        raise PlayError(f"no {what} found for “{query}”")
    item, kind = found
    before = _page_id(window)
    item.GetInvokePattern().Invoke(waitTime=0)
    button = "Shuffle" if shuffle else "Play"
    play = _wait(lambda: _page_id(window) not in (before, None) and _header_button(window, button), timeout)
    if not play:
        raise PlayError(f"the {kind.lower()}'s page didn't open")
    play.GetInvokePattern().Invoke(waitTime=0)
    return _header_text(window) + (kind.lower(),)


def _set_shuffle(window, on):
    # The player bar's button; playlist pages have their own "ShuffleButton" too.
    bar = find(window, auto.ControlType.GroupControl, automation_id="TransportBar")
    button = find(bar, auto.ControlType.ButtonControl, automation_id="ShuffleButton") if bar else None
    if not button:
        raise PlayError("couldn't find Apple Music's Shuffle button")
    toggle = button.GetTogglePattern()
    if bool(toggle.ToggleState) == on:
        return False
    toggle.Toggle(waitTime=0)
    return True


def _play_library_playlist(window, choose, shuffle, timeout):
    """The user's playlists are listed under "Playlists" in the sidebar, once it's
    expanded. In a narrow window the sidebar is collapsed to icons and won't
    expand, so open it first. Both are put back afterwards."""
    header = find(window, auto.ControlType.ListItemControl, automation_id="Sidebar_Header_Playlists")
    if not header:
        raise PlayError("couldn't find Playlists in Apple Music's sidebar")
    expander = header.GetExpandCollapsePattern()
    expanded = opened = chosen = False
    try:
        if header.BoundingRectangle.width() < SIDEBAR_OPEN_WIDTH:
            menu = find(window, auto.ControlType.ButtonControl, automation_id="Hamburger_Button")
            if not menu:
                raise PlayError("couldn't open Apple Music's sidebar")
            menu.GetInvokePattern().Invoke(waitTime=0)
            opened = True
            _wait(lambda: header.BoundingRectangle.width() >= SIDEBAR_OPEN_WIDTH, 1)
        if expander.ExpandCollapseState == auto.ExpandCollapseState.Collapsed:
            expander.Expand(waitTime=0)
            expanded = True
        host = _wait(lambda: _playlist_host(header), 2)
        if not host:
            raise PlayError("couldn't list your playlists")
        items = {item.Name.strip(): item for item in host.GetChildren()
                 if item.ControlTypeName == "ListItemControl" and item.AutomationId != "Sidebar_AllPlaylists"}
        name = choose(list(items))
        if name is None:
            return None
        before = _page_id(window)
        items[name].GetSelectionItemPattern().Select(waitTime=0)
        chosen = True
        button = "Shuffle" if shuffle else "Play"
        play = _wait(lambda: _page_id(window) not in (before, None) and _header_button(window, button), timeout)
        if not play:
            raise PlayError(f"your playlist “{name}” didn't open")
        play.GetInvokePattern().Invoke(waitTime=0)
        return name, "your library"
    finally:
        if expanded:
            expander.Collapse(waitTime=0)
        # Choosing a playlist closes a sidebar that was opened over the page (give
        # it a moment to slide shut); if none was chosen it's still open.
        if opened and not _wait(lambda: header.BoundingRectangle.width() < SIDEBAR_OPEN_WIDTH,
                                1 if chosen else 0.01):
            menu = find(window, auto.ControlType.ButtonControl, automation_id="Hamburger_Button")
            if menu:
                menu.GetInvokePattern().Invoke(waitTime=0)


def _playlist_host(header):
    """The list of playlists under the sidebar's Playlists header, while it shows."""
    host = find(header, auto.ControlType.GroupControl, automation_id="NavigationViewItemMenuItemsHost")
    return host if host and host.BoundingRectangle.width() > 0 else None


def _search_and_play(window, query, timeout):
    tile = _search(window, query, _top_song, timeout)
    if not tile:
        raise PlayError(f"no song found for “{query}”")
    title, subtitle = (t.Name for t in tile.GetChildren()[0].GetChildren()[1:3])
    artist = subtitle.split("·")[-1].strip()  # "Song · Billy Joel" -> "Billy Joel"

    # Quickest: the same song in the results' "Songs" list, which has a menu.
    row = _songs_row(window, title, artist)
    if not row:
        # Otherwise open the song's album and use its track row.
        before = _page_id(window)
        tile.GetInvokePattern().Invoke(waitTime=0)
        row = _wait(lambda: _page_id(window) not in (before, None) and _track_row(window, title), timeout)
        if not row:
            raise PlayError(f"couldn't find “{title}” on its album page")
    _play_from_menu(window, row)
    return title, artist


def _play_from_menu(window, row):
    """Choose "Play '<title>'" from a song row's "More" menu. The button only
    exists on screen once the row is scrolled into view."""
    row.GetScrollItemPattern().ScrollIntoView(waitTime=0)
    more = _wait(lambda: (lambda b: b if b and b.BoundingRectangle.width() > 0 else None)(
        find(row, auto.ControlType.ButtonControl, name="More")), 2)
    if not more:
        raise PlayError("the song's menu button didn't appear")
    more.GetInvokePattern().Invoke(waitTime=0)
    item = _wait(lambda: _play_menu_item(window), 3)
    if not item:
        raise PlayError("the song's menu didn't open")
    item.GetInvokePattern().Invoke(waitTime=0)


def _songs_row(window, title, artist):
    """The "<title> <artist>" row in the search results' Songs section, if any."""
    page = find(_content(window), auto.ControlType.ListControl) if _content(window) else None
    section = find(page, auto.ControlType.ListItemControl, name="Songs") if page else None
    if not section:
        return None
    return find(section, auto.ControlType.ListItemControl, name=f"{title} {artist}")


def search_box(window):
    """Apple Music's search box. In a narrow window the sidebar collapses and the
    box is replaced by a "Click to search" button, which brings it back."""
    box = find(window, auto.ControlType.EditControl, automation_id="TextBox")
    if box:
        return box
    button = find(window, auto.ControlType.ButtonControl, automation_id="Search_Button")
    if not button:
        return None
    button.GetInvokePattern().Invoke(waitTime=0)
    return _wait(lambda: find(window, auto.ControlType.EditControl, automation_id="TextBox"), 2)


def _top_song(window):
    return _top_result(window, ["Song"])


def _top_result(window, kinds):
    """The first "Top Results" tile of one of `kinds`: tiles are named like
    "Thriller Album · Michael Jackson"."""
    top = find(window, auto.ControlType.ListItemControl, name="Top Results")
    if not top:
        return None
    label = re.compile(rf"\b(?:{'|'.join(kinds)})\s·")
    return next((t for t in _list_items(top, 4) if label.search(t.Name)), None)


def _section_first(window, section):
    """The first item in a section of the search results, like "Albums"."""
    content = _content(window)
    page = find(content, auto.ControlType.ListControl) if content else None
    group = find(page, auto.ControlType.ListItemControl, name=section) if page else None
    return find(group, auto.ControlType.ListItemControl) if group else None


def _header(window):
    """The header of an album or playlist page (artwork, title, Play, Shuffle...)."""
    content = _content(window)
    page = find(content, auto.ControlType.ListControl) if content else None
    return find(page, auto.ControlType.GroupControl) if page else None


def _header_button(window, name):
    header = _header(window)
    button = find(header, auto.ControlType.ButtonControl, name=name) if header else None
    return button if button and button.BoundingRectangle.width() > 0 else None


def _header_text(window):
    """(title, the line under it) from an album or playlist page: ("Thriller",
    "Michael Jackson"), or ("Gym", "34 items · 1 hour, 52 minutes")."""
    header = _header(window)
    inner = header.GetChildren()[0] if header else None
    texts = [c.Name for c in inner.GetChildren() if c.ControlTypeName == "TextControl"] if inner else []
    return tuple(texts + ["", ""])[:2]


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
    args = sys.argv[1:]
    play = play_song
    if args[:1] == ["--album"]:
        play, args = play_album, args[1:]
    elif args[:1] == ["--playlist"]:
        play, args = play_playlist, args[1:]
    elif args[:1] == ["--shuffle"]:
        play, args = (lambda query: play_album_or_playlist(query, shuffle=True)), args[1:]
    t = time.monotonic()
    print(play(" ".join(args) or "uptown girl"), f"{time.monotonic() - t:.1f}s")
