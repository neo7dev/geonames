# Plan: Request lines and structured data as tables in the logs

Status: proposal, not started. Waiting on the open questions below.
Branch: to be created from `rich-logging` (which has `LOG_WIDTH`, the shared console and the INFO/DEBUG split).

Goal: one aligned line per request that says who made it (API key name, user), how it went (status, duration, size) and from where. Structured data attached to a log call (dicts, JSON, lists of rows) is drawn as a Rich table under the message, mostly at DEBUG.

## Where things stand

Today a request line looks like this:

```
[10/11/26 11:05:19] INFO     GET     200  /allcountries/?limit=100&offset=0  192.168.65.1:19843      httptools_impl.py:483
```

- It is uvicorn's own access log (`uvicorn.access`), reshaped by `AccessLogFilter` in `log/filters.py`. Uvicorn knows only the client, method, path, HTTP version and status. It does not know the duration, response size, API key or user.
- `request.state.principal` is already set by `require_api_key` (`api/auth.py:209`) and `require_user` (`api/auth.py:269`). `Principal` has `user_id`, `email`, `key_id`, `role`, `is_admin`, `tier` and the tier limits, but **not the key's name**. `_principal_select` (`api/auth.py:147`) would need `ApiKey.name`. Principals are cached per key hash (`AUTH_CACHE_SECONDS`), so a renamed key shows its new name within that time.
- Rate limit state reaches the response only as `Retry-After` on a 429 (`api/rate_limit.py:558`). There are no `X-RateLimit-*` headers. Showing "minute 4/600" needs the limiter to put its counts on `request.state`.
- There is no request middleware yet (`api/app.py`).
- All console output goes through one `Console` (`log/console.py`), at `LOG_WIDTH` (default 160) in a container. `RichHandler` is configured in `logger_config/logger_config.json` with `RichExtraFormatter`, which appends `extra=` values as `k=v`.
- The two lines in the sample above are two separate requests on one kept-alive connection (same `ip:port`). Duration and key name per row make that visible.

## What the research says

Sources: Starlette middleware docs, Rich's `logging.py` and `_log_render.py`, asgi-correlation-id, structlog's stdlib docs, OWASP Logging Cheat Sheet. Links are at the end.

1. **Access logging:** turn off `uvicorn.access` and log one line per request from a **pure ASGI middleware**, not `BaseHTTPMiddleware`.
   - `BaseHTTPMiddleware` stops `ContextVar` changes from reaching the middleware.
   - It also wraps streaming responses and has known issues with background tasks and client disconnects.
   - The middleware wraps `send`: it takes the status on `http.response.start` and sums the body sizes on `http.response.body`.
   - It logs once in a `finally`, so a request that raised is logged as 500, with `time.perf_counter()` for the duration.
2. **Request context:** the principal is read from `scope["state"]`, which backs `request.state`, after the app returns.
   - A `ContextVar` is only needed if lines logged *during* the request (SQL, auth) should carry a request ID or user.
   - If so, store one mutable object in the `ContextVar` and fill it in place. FastAPI runs sync dependencies in a thread pool, and changes they make to the variable itself would be lost.
   - A filter on the **handler** copies the values onto every record. Filters on a logger do not see records from its child loggers.
3. **Tables in RichHandler:** `RichHandler.emit()` formats the message to a string before rendering, so a `Table` passed as the message would print as its repr.
   - The usual approach is to subclass `RichHandler` and override `render_message(record, message)` to return `Group(Text(message), table)` when the record carries data.
   - No library does "record extras as a table" out of the box.
4. **Alignment across lines:** every record is its own render, so tables with automatic widths do not line up from one line to the next.
   - One-line rows need **fixed column widths**, with the variable-length part (the path) last. Uvicorn, Rich's `LogRender` (`level_width=8`) and structlog's `pad_event` all work this way.
   - Truncate by display width (`rich.cells.set_cell_size`), not `len()`, so wide characters still line up.
   - A header line scrolls away and gets interleaved in Docker logs, so do not rely on one.
5. **Structured logging library:** stdlib `extra={...}` fits the current setup. Put the data under one key (`extra={"data": ...}`) so it does not spread over the record.
   - structlog (via `ProcessorFormatter`) is only worth it for a second, machine-readable JSON output.
   - loguru replaces stdlib logging and fits poorly with uvicorn and `RichHandler`.
6. **Security:** never log API keys, `Authorization`/cookie headers, tokens or passwords.
   - Strip secret query parameters (`key`, `token`, `api_key`, `signature`, ...).
   - Treat emails and IPs as personal data.
   - Allowlist the headers that get logged.
   - Escape user input, for log injection (CWE-117) and Rich markup alike.
7. **Performance:** building a Rich `Table` costs real CPU, and `RichHandler` writes synchronously from the event loop.
   - INFO request rows should be one fixed-width `Text`, not a `Table`.
   - Build tables only when `logger.isEnabledFor(DEBUG)`.
   - If load grows, `QueueHandler`/`QueueListener` moves formatting off the event loop.

## Design

### Two shapes

- **Row:** one fixed-width line per event, aligned with the lines before and after it. Used for request lines at INFO.
- **Block:** a table under a message, for one event's details. Used at DEBUG (request details, daily update diffs, settings) and for a few INFO summaries (preflight).

A table per request would add 3 to 5 lines per request, so requests are rows, not blocks.

### Module `log/tables.py` (new)

```python
@dataclass(frozen=True)
class Column:
    name: str
    width: int | None = None      # None: takes the remaining width (last column)
    justify: str = "left"
    style: str | None = None

def row(columns: list[Column], values: list) -> Text
    """One fixed-width line: values padded or cut to each column's width."""

def table(columns: list[Column], rows: list[list], title: str | None = None) -> Table
    """Generic block, any number of columns."""

def table_from_dicts(rows: list[dict], title: str | None = None, columns: list[str] | None = None) -> Table
    """Columns from the dicts' keys (or the ones given), in first-seen order."""

def key_values(mapping: dict, title: str | None = None) -> Table
    """Two columns, key | value, for one dict or parsed JSON; nested values shown compactly."""
```

Formatters built on these, next to the code they describe:

| Function | Shape | Level | Shows |
|---|---|---|---|
| `request_row(entry)` | row | INFO | method, status, duration, size, client, key name, user, path |
| `request_details(entry)` | block | DEBUG | query parameters, allowlisted headers, principal (key, user, role, tier), rate limit counts |
| `column_changes(name, day, counts)` | block | DEBUG | column, rows changed (daily update) |
| `row_changes(name, day, samples)` | block | DEBUG | geonameid, name, column, before, after |
| `deleted_rows(name, day, rows)` | block | DEBUG | geonameid, name, reason |
| `preflight_summary(checks)` | block | INFO | check, target, result (replaces 4 lines) |
| `settings_table(settings)` | block | DEBUG | the "Settings initialised" dump, secrets hidden |

### Handler

`log/handlers.py`: `TableRichHandler(RichHandler)` overrides `render_message`.
- If the record has `table` (a Rich renderable) or `data` (a dict, or a list of dicts) attached, it returns `Group(Text(message), renderable)`.
- `data` is turned into a table with `key_values` / `table_from_dicts`.
- `logger_config.json` switches the console handler to it.

Usage:

```python
if log.isEnabledFor(logging.DEBUG):
    log.debug(
        f"[{name}] {day}: changed columns",
        extra={"table": column_changes(name, day, counts)},
    )
log.info(
    "Preflight", extra={"data": [{"check": ..., "target": ..., "result": ...}, ...]}
)
```

`RichExtraFormatter` must skip `table` and `data`, the same way it skips `markup` and `color_message` now.

### Request logging

- `api/request_log.py`: a pure ASGI middleware, added in `api/app.py`.
  - Times the request and records status and response size.
  - After the app returns, reads `scope["state"]["principal"]` and the rate limit counts.
  - Logs `request_row` at INFO, and `request_details` as a block at DEBUG.
- `uvicorn.access` is turned off: its logger gets `propagate: false` and no handlers in `logger_config.json`, or uvicorn runs with `--no-access-log` in the Dockerfile and Makefile.
- `AccessLogFilter` moves into the middleware. The path rules stay: UI files, preflights and `/health` are DEBUG.
- `Principal` gets `key_name` (from `ApiKey.name` in `_principal_select`; `None` for a login token).
- `enforce_rate_limit` puts its per-window counts on `request.state.rate_limit` for the DEBUG block.

### Mockups at 160 columns

INFO:

```
11:05:19 INFO  GET    200    12 ms   41.2 KB  192.168.65.1   ci-pipeline  /allcountries/?limit=100&offset=0
11:05:20 INFO  GET    429     1 ms    0.1 KB  192.168.65.1   ci-pipeline  /allcountries/?limit=100&offset=100
11:05:21 INFO  GET    401     0 ms    0.1 KB  10.0.0.7       —            /cities15000/
11:05:22 INFO  GET    200     8 ms    1.3 KB  192.168.65.1   (web login)  /portal/me
```

DEBUG, the same request with its block:

```
11:05:19 DEBUG GET /allcountries/ details
               query     limit=100, offset=0
               key       ci-pipeline (id 3f2a…)   user alice@example.com   role dev (pro tier)
               limits    minute 4/600   hour 120/10,000   month 8,210/1,000,000
               headers   user-agent curl/8.7   accept */*
```

DEBUG, a daily update:

```
[allcountries] 2026-10-10: 3,412 modifications, 120 new
  column          rows
  population     2,103
  name             211
  geonameid  name     column      before    after
  2950159    Berlin   population  3644826   3677472
```

## Open questions (answers needed before implementing)

1. **INFO columns.** Proposed: method, status, duration, size, client IP, key name, path.
   - Should the **user's email** be in the INFO line, or only at DEBUG (it is personal data)?
   - Should the **client port** be shown? It is random per connection, so it adds little.
2. **Hidden requests.** Keep UI files, CORS preflights and `/health` out of INFO? Should 4xx/5xx and slow requests (over 1 s?) always be logged, even on those paths?
3. **Per-request DEBUG block.** Wanted? It is about 4 extra lines per request at DEBUG.
4. **Other places to convert to tables:** daily update diffs (DEBUG), preflight (INFO), settings dump (DEBUG). Any others?
5. **Secrets and personal data:** never log keys, `Authorization`/cookies, or secret query values. Allowlist headers (user-agent, accept, content-type, referer?). Should IPs be shortened or kept whole?
6. **Library:** stay with stdlib `logging` + Rich (proposed), or move to structlog for a parallel JSON log?
7. **Request IDs:** give each request a short ID (`#a1b2`) carried by every line logged during it, and send it back as `X-Request-ID`? This needs the `ContextVar` and a handler filter.
8. **Rate limit counts in responses:** add `X-RateLimit-Limit/Remaining` headers to every data response (useful to clients too), or keep the counts internal for the log only?

## Work, once the answers are in

1. `log/tables.py`: `Column`, `row`, `table`, `table_from_dicts`, `key_values`.
2. `log/handlers.py`: `TableRichHandler`, and switch to it in `logger_config.json`; `RichExtraFormatter` skips `table`/`data`.
3. `api/auth.py`: `key_name` on `Principal` and in `_principal_select`.
4. `api/rate_limit.py`: counts on `request.state.rate_limit` (and headers, if question 8 says so).
5. `api/request_log.py`: the ASGI middleware with `request_row` / `request_details`, and redaction of query and headers. Add it in `api/app.py`.
6. Turn off `uvicorn.access`, and retire `AccessLogFilter` (its rules move to the middleware).
7. Convert daily update diffs (`ingestion/daily_update.py` `_log_modifications`, `_log_deletions`), preflight (`api/preflight.py`) and the settings dump (`config/config.py`) to tables.
8. Request IDs, if question 7 says so.
9. Tests in `tests/test_logging.py` and `tests/api/`:
   - fixed widths line up, and long paths and wide characters are cut by display width
   - a raising endpoint is logged as 500
   - key names, user and status appear, and no secrets do (key, `Authorization`, secret query values)
   - static and health requests are DEBUG
   - blocks are only built at DEBUG
10. Docs: README settings table (if a setting is added), `.devcontainer/README.md` logging paragraph.
11. Check in a container at `LOG_WIDTH=160` and 120, at INFO and DEBUG, with a test image in a separate container, not the user's stack.

## Out of scope

- Logs of the other services (Kong, Auth, Postgres): separate images, no Python inside.
- A log viewer or JSON log shipping (Loki, Dozzle).

## Sources

- Starlette middleware: https://raw.githubusercontent.com/encode/starlette/master/docs/middleware.md
- Rich logging handler: https://raw.githubusercontent.com/Textualize/rich/master/rich/logging.py
- Rich log render: https://raw.githubusercontent.com/Textualize/rich/master/rich/_log_render.py
- asgi-correlation-id: https://github.com/snok/asgi-correlation-id
- structlog with stdlib: https://www.structlog.org/en/stable/standard-library.html
- OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- FastAPI logging guide: https://apitally.io/blog/fastapi-logging-guide
