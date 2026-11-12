# Glance Dashboard

A self-hosted personal dashboard powered by [Glance](https://github.com/glanceapp/glance). The included home page brings together news, RSS feeds, videos, weather, markets, and GitHub releases in one view.

## Technology

- **Glance** — the dashboard application, run from the upstream `glanceapp/glance` Docker image.
- **Docker Compose** — starts and manages the container and exposes the dashboard on port `8080`.
- **YAML** — configures the server, page layout, and widgets in `config/`.
- **CSS (optional)** — add dashboard styling in `assets/user.css`.

There is no local application build step; Docker downloads and runs the Glance image.

## Requirements

- Docker with the Compose plugin (`docker compose`)
- A network connection so Glance can fetch content from configured feeds and services

## Run

From the project directory, create the environment file from the example. In PowerShell:

```powershell
Copy-Item example.env .env
```

Or, on macOS/Linux:

```sh
cp example.env .env
```

Before starting, replace `SOURCE_MANAGER_PASSWORD` in `.env` with a unique random value of at least 32 characters. `SOURCE_MANAGER_URL` must be a URL the browser can open; the default is suitable when the dashboard is opened on the same machine. For a remote or HTTPS dashboard, use a browser-accessible HTTPS URL for the source manager, typically through your reverse proxy.

Then start the dashboard:

```sh
docker compose up -d
```

Open [http://localhost:8080](http://localhost:8080).

To follow the container logs:

```sh
docker compose logs -f glance
```

To stop the dashboard:

```sh
docker compose down
```

## Personalize the dashboard

Use the **Website & YouTube inputs** section at the top of the dashboard to add and remove sources. Enter an RSS/Atom feed URL for a website, or a YouTube channel ID (or a `youtube.com/channel/UC...` URL). Enter the source manager password from `.env` to view and manage saved sources. Changes are shared across dashboard users, persisted in `data/sources.json`, and appear in the RSS or video widgets after Glance reloads its configuration.

For manual configuration, edit `config/home.yml` to add or change widgets. For example, to add a favorite RSS feed, add this widget under the `widgets:` list of the column where you want it to appear:

```yaml
- type: rss
  limit: 10
  feeds:
    - url: https://example.com/feed.xml
      title: My favorite site
```

Replace the example URL and title with the feed you prefer. You can also change the existing RSS feeds, video channels, weather location, market symbols, and GitHub repositories in the same file. Keep the YAML indentation consistent with the surrounding widgets. Save the file and refresh the dashboard; if the changes do not appear, restart the service with `docker compose restart glance`.

## Configuration

- `config/glance.yml` sets the assets directory, custom CSS path, and included pages.
- `config/home.yml` defines the Home page and its widgets. Edit it to change feeds, channels, locations, markets, or repositories.
- `source-manager/` contains the small companion service that powers the on-dashboard source form. Its generated widget configuration is `config/user-sources.yml`; do not edit that file manually.
- `data/sources.json` stores sources added through the dashboard form and is excluded from version control.
- `assets/user.css` is mounted into the container for optional custom styles.
- `.env` supplies values referenced from YAML using `${VARIABLE_NAME}`. Start from `example.env`; do not commit secrets.

Configuration and asset directories are mounted into the container, so changes to the YAML and CSS files are available without rebuilding an image. If you change `.env`, rerun `docker compose up -d` so Compose applies the environment.

The Compose file publishes port `8080` and restarts the container unless it is stopped. The Docker socket mount is commented out by default; enable it only if you intend to use Glance's Docker containers widget.
