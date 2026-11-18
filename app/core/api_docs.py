"""The interactive API docs at `/docs` (Swagger UI), outside production only.

FastAPI's own `/docs` page is replaced so that:
- the search box finds endpoints by path, summary, method or tag (Swagger's built-in filter only
  matches tag names, so "login" wouldn't find `/admin/auth/login`);
- sections start collapsed, "Try it out" is open and each call shows how long it took.

Logging in works through the browser's cookies: run a login endpoint on this page and every
later call sends that login's cookie by itself (same origin). Endpoints that need a login show
a lock naming which one (`app/modules/auth/dependencies.py`). No token is ever shown here.

Swagger UI's files come from jsDelivr at an exact, pinned version.
"""

import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

SWAGGER_UI_VERSION = "5.17.14"
_CDN = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"

DESCRIPTION = """
Use the **search box** below to find an endpoint by path, name, method or section,
e.g. `login`, `/me`, `claims` or `delete`.

### How to log in here
Logins are cookies: run a login endpoint on this page, and every endpoint you try after that is
called as that person (🔒 shows which login an endpoint needs). It lasts until the cookie
expires or you log out.

- **Admin:** `POST /api/v1/admin/auth/login` with your email and password.
- **Venue owner:** `POST /api/v1/venue/auth/login` with the venue's email and password.
- **Customer:** log in as an admin, send `LOGIN` with `POST /api/v1/dev/simulate-inbound`,
  then put the link's `token` into `POST /api/v1/auth/customer/consume`.
- **Counter phone:** make a pairing link as the venue, then `POST /api/v1/display/pair`
  with its `token`.

Logins for different apps don't replace each other: you can be an admin, a venue and a
customer at once. To start again, log out or clear this site's cookies.
"""

# Swagger UI calls `fn.opsFilter(taggedOps, phrase)` for its search box. This version matches
# every word typed against the tag, method, path, summary and operation id.
_SEARCH_PLUGIN = """
const SearchEverything = () => ({
  fn: {
    opsFilter: (taggedOps, phrase) => {
      const words = (phrase || "").toLowerCase().split(/\\s+/).filter(Boolean);
      if (!words.length) return taggedOps;
      return taggedOps
        .map((tagObj, tag) =>
          tagObj.set(
            "operations",
            tagObj.get("operations").filter((op) => {
              const text = [
                tag,
                op.get("method"),
                op.get("path"),
                op.getIn(["operation", "summary"]) || "",
                op.getIn(["operation", "operationId"]) || "",
              ].join(" ").toLowerCase();
              return words.every((word) => text.includes(word));
            }),
          ),
        )
        .filter((tagObj) => tagObj.get("operations").size > 0);
    },
  },
});
"""

SWAGGER_OPTIONS = {
    "filter": True,  # shows the search box; SearchEverything decides what it matches
    "docExpansion": "none",
    "tryItOutEnabled": True,
    "displayRequestDuration": True,
    "persistAuthorization": True,
    "deepLinking": True,
    "withCredentials": True,
}


def docs_html(title: str, openapi_url: str) -> str:
    options = json.dumps(SWAGGER_OPTIONS)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>API docs: {title}</title>
  <link rel="stylesheet" href="{_CDN}/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{_CDN}/swagger-ui-bundle.js"></script>
  <script>
{_SEARCH_PLUGIN}
    window.ui = SwaggerUIBundle(Object.assign({options}, {{
      url: {json.dumps(openapi_url)},
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      plugins: [SearchEverything],
      layout: "BaseLayout",
    }}));
  </script>
</body>
</html>"""


def add_docs_page(app: FastAPI, *, title: str, openapi_url: str) -> None:
    """Serve `/docs`. Only called outside production (app/main.py)."""
    page = docs_html(title, openapi_url)

    @app.get("/docs", include_in_schema=False)
    async def docs() -> HTMLResponse:
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})
