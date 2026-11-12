import hmac
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit


YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
MANAGED_HEADER = "# Managed by the source manager. Do not edit; changes are overwritten.\n"
MAX_REQUEST_SIZE = 8192


class DuplicateSource(Exception):
    pass


def normalize_website_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Enter an RSS or Atom feed URL.")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("The feed URL cannot contain whitespace or control characters.")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as error:
        raise ValueError("Enter a valid feed URL.") from error

    if parsed.scheme not in ("http", "https") or not hostname or parsed.username or parsed.password:
        raise ValueError("Use an http:// or https:// feed URL without embedded credentials.")

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def normalize_youtube_channel(value):
    if not isinstance(value, str):
        raise ValueError("Enter a YouTube channel ID or channel URL.")
    value = value.strip()
    if YOUTUBE_CHANNEL_ID.fullmatch(value):
        return value

    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError("Enter a valid YouTube channel ID or channel URL.") from error

    if parsed.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        raise ValueError("Use a YouTube channel ID or a youtube.com/channel/UC... URL.")
    match = re.fullmatch(r"/channel/(UC[A-Za-z0-9_-]{22})/?", parsed.path)
    if not match:
        raise ValueError("Use a channel ID or a YouTube /channel/UC... URL.")
    return match.group(1)


def normalize_title(value, fallback):
    if value is None or value == "":
        return fallback
    if not isinstance(value, str):
        raise ValueError("The feed title must be text.")
    title = value.strip()
    if not title or len(title) > 100 or any(ord(character) < 32 for character in title):
        raise ValueError("Use a feed title between 1 and 100 characters.")
    return title


def yaml_string(value):
    return json.dumps(value, ensure_ascii=False)


class SourceStore:
    def __init__(self, data_path, config_path):
        self.data_path = Path(data_path)
        self.config_path = Path(config_path)
        self.lock = threading.RLock()
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        if self.data_path.exists():
            self.sources = self._load()
        else:
            self.sources = {"websites": [], "youtube_channels": []}
            self._save()
        self._write_generated_config()

    def _load(self):
        with self.data_path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict) or set(data) != {"websites", "youtube_channels"}:
            raise ValueError("The saved source file has an invalid structure.")
        if not isinstance(data["websites"], list) or not isinstance(data["youtube_channels"], list):
            raise ValueError("The saved source lists must be arrays.")

        websites = []
        for item in data["websites"]:
            if not isinstance(item, dict) or set(item) != {"url", "title"}:
                raise ValueError("A saved website source has an invalid structure.")
            url = normalize_website_url(item["url"])
            title = normalize_title(item["title"], urlsplit(url).hostname)
            websites.append({"url": url, "title": title})

        channels = [normalize_youtube_channel(channel) for channel in data["youtube_channels"]]
        if len({item["url"] for item in websites}) != len(websites) or len(set(channels)) != len(channels):
            raise ValueError("The saved source file contains duplicates.")
        return {"websites": websites, "youtube_channels": channels}

    def _atomic_write(self, destination, contents):
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, delete=False
            ) as file:
                temporary_path = Path(file.name)
                file.write(contents)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _save(self):
        self._atomic_write(self.data_path, json.dumps(self.sources, ensure_ascii=False, indent=2) + "\n")

    def _write_generated_config(self):
        lines = [MANAGED_HEADER]
        if self.sources["websites"]:
            lines.extend(
                [
                    "- type: rss\n",
                    "  title: Added websites\n",
                    "  feeds:\n",
                ]
            )
            for website in self.sources["websites"]:
                lines.extend(
                    [
                        f"    - url: {yaml_string(website['url'])}\n",
                        f"      title: {yaml_string(website['title'])}\n",
                    ]
                )
        if self.sources["youtube_channels"]:
            lines.extend(
                [
                    "- type: videos\n",
                    "  title: Added YouTube channels\n",
                    "  channels:\n",
                ]
            )
            lines.extend(f"    - {yaml_string(channel)}\n" for channel in self.sources["youtube_channels"])
        self._atomic_write(self.config_path, "".join(lines))

    def get_sources(self):
        with self.lock:
            return json.loads(json.dumps(self.sources))

    def add_website(self, value, title):
        url = normalize_website_url(value)
        normalized_title = normalize_title(title, urlsplit(url).hostname)
        with self.lock:
            if any(item["url"] == url for item in self.sources["websites"]):
                raise DuplicateSource("That feed URL has already been added.")
            self.sources["websites"].append({"url": url, "title": normalized_title})
            self._save()
            self._write_generated_config()
            return self.get_sources()

    def add_youtube_channel(self, value):
        channel = normalize_youtube_channel(value)
        with self.lock:
            if channel in self.sources["youtube_channels"]:
                raise DuplicateSource("That YouTube channel has already been added.")
            self.sources["youtube_channels"].append(channel)
            self._save()
            self._write_generated_config()
            return self.get_sources()

    def remove_source(self, source_type, value):
        with self.lock:
            if source_type == "website":
                url = normalize_website_url(value)
                sources = self.sources["websites"]
                updated = [item for item in sources if item["url"] != url]
                found = len(updated) != len(sources)
                self.sources["websites"] = updated
            elif source_type == "youtube":
                channel = normalize_youtube_channel(value)
                sources = self.sources["youtube_channels"]
                updated = [item for item in sources if item != channel]
                found = len(updated) != len(sources)
                self.sources["youtube_channels"] = updated
            else:
                raise ValueError("Source type must be website or youtube.")

            if not found:
                return False
            self._save()
            self._write_generated_config()
            return True


def make_handler(store, password):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SourceManager/1.0"

        def _send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            return supplied.startswith(prefix) and hmac.compare_digest(supplied[len(prefix):], password)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid request size.")
            if length < 1 or length > MAX_REQUEST_SIZE:
                raise ValueError("Request body must be between 1 and 8192 bytes.")
            try:
                data = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("Request body must be valid JSON.") from error
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object.")
            return data

        def do_GET(self):
            if self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; frame-ancestors *; form-action 'self'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/api/sources":
                if not self._authorized():
                    self._send_json(401, {"error": "Enter the source manager password."})
                    return
                self._send_json(200, store.get_sources())
                return
            self._send_json(404, {"error": "Not found."})

        def do_POST(self):
            if self.path != "/api/sources":
                self._send_json(404, {"error": "Not found."})
                return
            if not self._authorized():
                self._send_json(401, {"error": "Enter the source manager password."})
                return

            try:
                data = self._read_json()
                source_type = data.get("type")
                if source_type == "website":
                    result = store.add_website(data.get("url"), data.get("title"))
                elif source_type == "youtube":
                    result = store.add_youtube_channel(data.get("channel"))
                else:
                    raise ValueError("Source type must be website or youtube.")
            except DuplicateSource as error:
                self._send_json(409, {"error": str(error)})
                return
            except ValueError as error:
                self._send_json(400, {"error": str(error)})
                return
            self._send_json(201, result)

        def do_DELETE(self):
            if not self.path.startswith("/api/sources?"):
                self._send_json(404, {"error": "Not found."})
                return
            if not self._authorized():
                self._send_json(401, {"error": "Enter the source manager password."})
                return

            query = parse_qs(urlsplit(self.path).query, strict_parsing=True)
            source_type = query.get("type", [""])[0]
            value = query.get("value", [""])[0]
            try:
                removed = store.remove_source(source_type, value)
            except ValueError as error:
                self._send_json(400, {"error": str(error)})
                return
            if not removed:
                self._send_json(404, {"error": "Source not found."})
                return
            self._send_json(200, store.get_sources())

        def log_message(self, format_string, *args):
            print(f"{self.address_string()} - {format_string % args}")

    return Handler


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Manage sources</title>
  <style>
    :root { color-scheme: dark; font: 15px/1.5 system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 18px; color: #e5e7eb; background: #111827; }
    h1 { margin: 0 0 12px; font-size: 1.3rem; }
    h2 { margin: 0 0 10px; font-size: 1rem; }
    p { margin: 0 0 12px; color: #aab4c3; }
    .panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
    section { padding: 14px; border: 1px solid #344155; border-radius: 9px; background: #182232; }
    label { display: block; margin: 9px 0 4px; color: #bac5d4; font-size: .85rem; }
    input { width: 100%; padding: 9px 10px; border: 1px solid #43536b; border-radius: 6px; color: #f3f4f6; background: #101927; }
    button { padding: 8px 11px; border: 0; border-radius: 6px; color: white; background: #2563eb; cursor: pointer; }
    button:hover { background: #1d4ed8; }
    button.remove { padding: 5px 8px; background: #7f1d1d; }
    form button { margin-top: 10px; }
    ul { margin: 8px 0 0; padding: 0; list-style: none; }
    li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 0; border-top: 1px solid #344155; overflow-wrap: anywhere; }
    li span { min-width: 0; }
    .password { max-width: 450px; margin-bottom: 12px; }
    .password button { margin-top: 8px; }
    #status { min-height: 1.5em; margin: 8px 0 0; color: #a7f3d0; }
    @media (max-width: 520px) { body { padding: 12px; } .panel { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <h1>Website &amp; YouTube inputs</h1>
  <p>Add RSS/Atom feed URLs and YouTube channel IDs. Saved sources are shared and appear in the dashboard feeds.</p>
  <section class="password">
    <label for="password">Source manager password</label>
    <input id="password" type="password" autocomplete="current-password">
    <button id="load" type="button">Load saved sources</button>
  </section>
  <div class="panel">
    <section>
      <h2>Add a website feed</h2>
      <form id="website-form">
        <label for="feed-url">RSS/Atom feed URL</label>
        <input id="feed-url" type="url" required placeholder="https://example.com/feed.xml">
        <label for="feed-title">Display name (optional)</label>
        <input id="feed-title" type="text" maxlength="100" placeholder="Example site">
        <button type="submit">Add website</button>
      </form>
    </section>
    <section>
      <h2>Add a YouTube channel</h2>
      <form id="youtube-form">
        <label for="channel">Channel ID or /channel/UC... URL</label>
        <input id="channel" type="text" required placeholder="UCxxxxxxxxxxxxxxxxxxxxxx">
        <button type="submit">Add channel</button>
      </form>
    </section>
  </div>
  <section style="margin-top:12px">
    <h2>Saved sources</h2>
    <div id="sources">Unlock the manager to view and edit saved sources.</div>
  </section>
  <p id="status" role="status" aria-live="polite"></p>
  <script>
    const passwordInput = document.getElementById('password');
    const status = document.getElementById('status');
    const sources = document.getElementById('sources');
    let managerPassword = '';

    async function request(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          'Authorization': `Bearer ${managerPassword}`,
          ...(options.headers || {})
        }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The request failed.');
      return data;
    }

    function renderList(title, values, type, valueOf) {
      const section = document.createElement('section');
      const heading = document.createElement('h2');
      heading.textContent = title;
      section.append(heading);
      if (values.length === 0) {
        const empty = document.createElement('p');
        empty.textContent = 'None added yet.';
        section.append(empty);
      }
      const list = document.createElement('ul');
      for (const item of values) {
        const row = document.createElement('li');
        const text = document.createElement('span');
        text.textContent = typeof item === 'string' ? item : `${item.title} — ${item.url}`;
        const remove = document.createElement('button');
        remove.className = 'remove';
        remove.type = 'button';
        remove.textContent = 'Remove';
        remove.addEventListener('click', () => removeSource(type, valueOf(item)));
        row.append(text, remove);
        list.append(row);
      }
      section.append(list);
      return section;
    }

    async function loadSources() {
      managerPassword = passwordInput.value;
      try {
        const data = await request('/api/sources');
        sources.replaceChildren(
          renderList('Website feeds', data.websites, 'website', item => item.url),
          renderList('YouTube channels', data.youtube_channels, 'youtube', item => item)
        );
        status.textContent = 'Saved sources loaded.';
      } catch (error) {
        sources.textContent = 'Enter the correct password to view saved sources.';
        status.textContent = error.message;
      }
    }

    async function removeSource(type, value) {
      try {
        const query = new URLSearchParams({ type, value });
        await request(`/api/sources?${query}`, { method: 'DELETE' });
        await loadSources();
        status.textContent = 'Source removed.';
      } catch (error) {
        status.textContent = error.message;
      }
    }

    document.getElementById('load').addEventListener('click', loadSources);
    document.getElementById('website-form').addEventListener('submit', async event => {
      event.preventDefault();
      managerPassword = passwordInput.value;
      try {
        await request('/api/sources', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'website',
            url: document.getElementById('feed-url').value,
            title: document.getElementById('feed-title').value
          })
        });
        event.target.reset();
        await loadSources();
        status.textContent = 'Website feed added to the dashboard.';
      } catch (error) {
        status.textContent = error.message;
      }
    });
    document.getElementById('youtube-form').addEventListener('submit', async event => {
      event.preventDefault();
      managerPassword = passwordInput.value;
      try {
        await request('/api/sources', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'youtube',
            channel: document.getElementById('channel').value
          })
        });
        event.target.reset();
        await loadSources();
        status.textContent = 'YouTube channel added to the dashboard.';
      } catch (error) {
        status.textContent = error.message;
      }
    });
  </script>
</body>
</html>
"""


def main():
    password = os.environ.get("SOURCE_MANAGER_PASSWORD", "")
    if len(password) < 32 or password.startswith("CHANGE-THIS"):
        raise SystemExit(
            "Set SOURCE_MANAGER_PASSWORD to a unique random value of at least 32 characters."
        )

    store = SourceStore(
        os.environ.get("DATA_PATH", "/data/sources.json"),
        os.environ.get("GENERATED_CONFIG", "/app/config/user-sources.yml"),
    )
    server = ThreadingHTTPServer(("0.0.0.0", 8787), make_handler(store, password))
    print("Source manager listening on port 8787.")
    server.serve_forever()


if __name__ == "__main__":
    main()
