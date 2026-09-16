# Contributing

Thanks for taking a look.

The tool is deliberately two files with no dependencies:

- `serve.py` — stdlib HTTP server: static files, the `/__api` proxy, the `/__hook` callback receiver,
  the optional redaction layer and the `/__media` relay.
- `index.html` — the whole UI: no framework, no build step, no bundler.

## Ground rules

- **No dependencies.** Python standard library on the server, plain JS in the page. If something needs
  a package, it probably belongs outside this tool.
- **No build step.** Editing `index.html` and reloading the page must be the entire loop.
- **Nothing secret on disk.** The API key lives in the browser's `localStorage`; the webhook secret
  lives in server memory for the lifetime of the process. Keep it that way.
- **Follow the API docs, don't guess.** When the API changes, check `/openapi.json` and the developer
  documentation, then mirror it. Field descriptions in the UI should quote the documented meaning.

## Checks before opening a PR

```sh
python3 -c "import ast; ast.parse(open('serve.py').read())"   # server parses
python3 - <<'PY'                                              # page script parses
import re; s=open('index.html',encoding='utf-8').read()
open('/tmp/app.js','w').write(re.search(r'<script>(.*)</script>', s, re.S).group(1))
PY
node --check /tmp/app.js
python3 serve.py --help
```

Then run it against a real key and click through the sections you touched.

## Scope

Good additions: coverage of new endpoints or fields, clearer error surfacing, better ergonomics for
manual testing. Out of scope: automated test suites against the live API, anything that stores
credentials, and features that require a server-side account.
