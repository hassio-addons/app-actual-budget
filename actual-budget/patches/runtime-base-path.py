#!/usr/bin/env python3
"""Move Actual's web client off the site root and onto the document base.

Actual is built to be served from the root of a host and says so: a base path
is an open request upstream, not a supported setting. Ingress has no root to
give it. Every address is settled per request, under a path Home Assistant
picks, so the client has to work out where it is at the moment it loads rather
than be told when it was built.

Everything below is that: the handful of places where the built client names
the site root are pointed at the document base instead. The base is written
into the page by NGINX, read back by ha-base-path.js, and reaches the workers
through the payload Actual already sends them. At the root the substitutions
resolve to exactly what they replaced, so one image serves both Ingress and the
published port.

This edits a compiled bundle, whose shape is upstream's to change. Every
substitution below therefore states what it expects to match and stops the
build if it does not, which turns a silently half-patched client into a failed
build.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

SHIM = "ha-base-path.js"

# Every address the document loads the client through, made relative so that it
# resolves against the base. Written as a pair so that what is left behind can
# be checked for afterwards.
DOCUMENT_ROOT_HREF = re.compile(r'(href|src)="/(?!/)')
DOCUMENT_ROOT_HREF_REPLACEMENT = r'\1="'


class PatchError(Exception):
    """A substitution did not match what it was written against."""


def substitute(
    text: str, pattern: str, replacement: str, *, expected: int, where: str
) -> str:
    """Apply a regular expression, insisting on the number of matches."""
    found = len(re.findall(pattern, text))
    if found != expected:
        raise PatchError(
            f"{where}: expected {expected} match(es) of {pattern!r}, found {found}"
        )
    return re.sub(pattern, replacement, text)


def find_entry(index_html: str, root: Path) -> Path:
    """Resolve the entry bundle out of the page that loads it."""
    match = re.search(
        r'<script type="module"[^>]*\ssrc="/(static/js/index\.[^"]+\.js)"',
        index_html,
    )
    if not match:
        raise PatchError("index.html does not load an entry bundle where expected")

    entry = root / match.group(1)
    if not entry.is_file():
        raise PatchError(f"index.html points at {entry}, which is not there")
    return entry


def patch_index_html(path: Path) -> int:
    """Point the page at itself and pull in the shim.

    The client is loaded by a set of root-relative addresses in the document,
    one per bundle it preloads, so how many there are is upstream's business.
    What matters is that none are left, and that the base they now resolve
    against is stated before the first of them.
    """
    text = path.read_text(encoding="utf-8")

    text, rewritten = DOCUMENT_ROOT_HREF.subn(DOCUMENT_ROOT_HREF_REPLACEMENT, text)
    if not rewritten:
        raise PatchError("index.html: no root-relative addresses found to rewrite")

    # Anything still pointing at the root is an attribute this was not written
    # for, and would be an address that never arrives under Ingress.
    remaining = re.findall(r"""=["']/(?!/)""", text)
    if remaining:
        raise PatchError(
            f"index.html: {len(remaining)} address(es) still point at the site root"
        )

    text = substitute(
        text,
        r"<head>",
        f'<head>\n    <base href="/">\n    <script src="{SHIM}"></script>',
        expected=1,
        where=path.name,
    )

    path.write_text(text, encoding="utf-8")
    return rewritten


def patch_entry(path: Path) -> None:
    """Rework the entry bundle's own references to the site root."""
    text = path.read_text(encoding="utf-8")

    # The backend worker sits next to this bundle, so it is reached from the
    # bundle's own address rather than from the root.
    text = substitute(
        text,
        r"new URL\(`/static/js/(browser-server\.[A-Za-z0-9_-]+\.js)`",
        r"new URL(`./\1`",
        expected=1,
        where=path.name,
    )

    # The SharedWorker that coordinates tabs is addressed by the document, so
    # it takes the base rather than a leading slash.
    text = substitute(
        text,
        r"new SharedWorker\(`/static/(shared-browser-server-[A-Za-z0-9_-]+\.js)`",
        r"new SharedWorker(window.__actualBasePath+`static/\1`",
        expected=1,
        where=path.name,
    )

    # What the workers are told the site root is. Upstream builds this from
    # Vite's base and hands it over in the init message, since a worker has no
    # document of its own to ask.
    text = substitute(
        text,
        r"publicUrl:`/`\.slice\(0,-1\)",
        "publicUrl:window.__actualBasePath.slice(0,-1)",
        expected=1,
        where=path.name,
    )

    # Without a basename the router reads the Ingress path as a route, matches
    # nothing, and navigates the frame out of Ingress on the first click.
    text = substitute(
        text,
        r"BrowserRouter,\{children:",
        "BrowserRouter,{basename:window.__actualBasePath,children:",
        expected=1,
        where=path.name,
    )

    # The service worker is switched off. It is registered against the site
    # root, which is Home Assistant's under Ingress, and what it is for is
    # holding the app in a cache to be used offline: for a server only
    # reachable while Home Assistant is up, that buys nothing and costs a class
    # of bug where an app update is served out of yesterday's cache.
    text = substitute(
        text,
        r"async function register\(\)\{if\(`serviceWorker`in navigator\)\{",
        "async function register(){if(!1){",
        expected=1,
        where=path.name,
    )

    path.write_text(text, encoding="utf-8")


def patch_preload_helper(paths: list[Path]) -> Path:
    """Point the preloader at the base as well.

    Every lazily loaded part of the client is imported relative to the bundle
    asking for it, which needs nothing from us, but each import is preceded by
    a preload hint built from a list of plain names and a prefix. The prefix is
    the site root, so the hints miss, and the browser fetches the same bundles
    twice: once as a preload that 404s, once for real.

    Which bundle this helper is minified into is Vite's business and moves
    between releases, so it is searched for rather than named.
    """
    pattern = r"assetsURL=function\((\w+)\)\{return`/`\+\1\}"
    replacement = r"assetsURL=function(\1){return window.__actualBasePath+\1}"

    matches = [path for path in paths if re.search(pattern, path.read_text("utf-8"))]
    helper = only(matches, "bundle holding Vite's preload helper")

    helper.write_text(
        substitute(
            helper.read_text(encoding="utf-8"),
            pattern,
            replacement,
            expected=1,
            where=helper.name,
        ),
        encoding="utf-8",
    )
    return helper


def patch_stylesheets(root: Path) -> int:
    """Load the fonts from under the base.

    A stylesheet resolves its own addresses against where it was served from,
    which is a known two directories under the base, so the fonts are reached
    from there rather than from the root.
    """
    stylesheets = sorted((root / "static" / "css").glob("*.css"))
    if not stylesheets:
        raise PatchError("no stylesheets found under static/css")

    patched = 0
    for path in stylesheets:
        text = path.read_text(encoding="utf-8")
        text, rewritten = re.subn(r"url\(/static/", "url(../", text)
        remaining = re.findall(r"url\(/(?!/)", text)
        if remaining:
            raise PatchError(
                f"{path.name}: {len(remaining)} address(es) still point at the "
                "site root"
            )
        if rewritten:
            path.write_text(text, encoding="utf-8")
            patched += rewritten

    if not patched:
        raise PatchError("no root-relative addresses found in the stylesheets")
    return patched


def patch_manifests(root: Path) -> int:
    """Describe the app relative to where it is served from.

    The web app manifest names the icons, the start address and the scope the
    app answers for. Left at the root they fall outside the scope the browser
    works out from the base, and it says so on every load.
    """
    manifests = sorted(root.glob("*.webmanifest"))
    if not manifests:
        raise PatchError("no web app manifest found")

    patched = 0
    for path in manifests:
        text = path.read_text(encoding="utf-8")
        text, rewritten = re.subn(r'"/(?!/)', '"./', text)
        remaining = re.findall(r'"/(?!/)', text)
        if remaining:
            raise PatchError(
                f"{path.name}: {len(remaining)} address(es) still point at the "
                "site root"
            )
        if rewritten:
            path.write_text(text, encoding="utf-8")
            patched += rewritten

    if not patched:
        raise PatchError("no root-relative addresses found in the manifests")
    return patched


def patch_origins(paths: list[Path]) -> int:
    """Extend the client's idea of its own address to include the base.

    The origin is where Actual looks for the server it syncs against, where it
    sends people back to after signing in through OpenID, and what it compares
    the current address against when deciding whether a navigation has already
    happened. Under Ingress the server is not at the origin, it is at the
    origin plus the path Home Assistant serves this app under.
    """
    patched = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        occurrences = text.count("window.location.origin")
        if not occurrences:
            continue
        path.write_text(
            text.replace(
                "window.location.origin",
                "(window.location.origin+window.__actualBasePath.slice(0,-1))",
            ),
            encoding="utf-8",
        )
        patched += occurrences

    if not patched:
        raise PatchError("no references to window.location.origin found to rewrite")
    return patched


def patch_backend_worker(path: Path) -> None:
    """Keep the base within reach of the database worker.

    The worker that owns the database is pulled into this one, which is the
    only side of the pair handed the init message, so the base is put somewhere
    both can see before that happens.
    """
    text = path.read_text(encoding="utf-8")

    text = substitute(
        text,
        r"const hash = msg\.hash;",
        (
            "const hash = msg.hash;\n"
            "        self.__actualPublicUrl = msg.publicUrl || '';"
        ),
        expected=1,
        where=path.name,
    )

    path.write_text(text, encoding="utf-8")


def patch_database_worker(path: Path) -> None:
    """Fetch the SQLite build and the seed files from under the base.

    A worker resolves a relative address against its own script, which is not
    where these files are, so they are addressed from the base the worker was
    handed instead.
    """
    text = path.read_text(encoding="utf-8")

    # The list of files Actual seeds its in-memory filesystem from, and the
    # files themselves: the migrations and the demo budget.
    text = substitute(
        text,
        r"fetch\(`/data-file-index\.txt`\)",
        "fetch((self.__actualPublicUrl||``)+`/data-file-index.txt`)",
        expected=1,
        where=path.name,
    )
    text = substitute(
        text,
        r"fetchFile\(`/data/`\+file\)",
        "fetchFile((self.__actualPublicUrl||``)+`/data/`+file)",
        expected=1,
        where=path.name,
    )

    # Where sql.js goes looking for its WebAssembly build.
    text = substitute(
        text,
        r"\{baseURL=`/`,wasmBinary=",
        "{baseURL=(self.__actualPublicUrl||``)+`/`,wasmBinary=",
        expected=1,
        where=path.name,
    )

    path.write_text(text, encoding="utf-8")


def only(paths: list[Path], description: str) -> Path:
    """Return the single match, or say which expectation broke."""
    if len(paths) != 1:
        raise PatchError(
            f"expected exactly one {description}, found {len(paths)}: "
            f"{sorted(p.name for p in paths)}"
        )
    return paths[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <web-root>", file=sys.stderr)
        return 2

    root = Path(argv[1])
    index_html = root / "index.html"
    if not index_html.is_file():
        print(f"{root} does not look like Actual's web root", file=sys.stderr)
        return 2

    scripts = sorted(root.glob("**/*.js"))

    entry = find_entry(index_html.read_text(encoding="utf-8"), root)
    backend_worker = only(
        [
            path
            for path in scripts
            if re.fullmatch(r"browser-server\.[A-Za-z0-9_-]+\.js", path.name)
        ],
        "backend worker (static/js/browser-server.<hash>.js)",
    )
    database_worker = only(
        [
            path
            for path in scripts
            if re.fullmatch(r"kcab\.worker\.[A-Za-z0-9_-]+\.js", path.name)
        ],
        "database worker (kcab/kcab.worker.<hash>.js)",
    )

    addresses = patch_index_html(index_html)
    patch_entry(entry)
    patch_backend_worker(backend_worker)
    patch_database_worker(database_worker)
    preloader = patch_preload_helper(scripts)
    fonts = patch_stylesheets(root)
    manifests = patch_manifests(root)
    origins = patch_origins(scripts)

    shutil.copyfile(Path(__file__).parent / SHIM, root / SHIM)

    print(f"Patched Actual's web client in {root}")
    print(f"  index.html: base tag, {SHIM}, {addresses} address(es) made relative")
    print(f"  entry bundle: {entry.name}")
    print(f"  backend worker: {backend_worker.name}")
    print(f"  database worker: {database_worker.name}")
    print(f"  preload helper: {preloader.name}")
    print(f"  stylesheets: {fonts} address(es) made relative")
    print(f"  manifests: {manifests} address(es) made relative")
    print(f"  origin extended with the base in {origins} place(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except PatchError as err:
        print(f"Patching Actual's web client failed: {err}", file=sys.stderr)
        sys.exit(1)
