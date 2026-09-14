# Railway v2 config note

`railway.json` on branch `v2` must not define a global `deploy.startCommand`.

НеНой 2.0 uses two Railway services from the same repository with different service-level commands:

- web: `uvicorn app_v2.main:app --host 0.0.0.0 --port $PORT`
- worker: `python -m app_v2.workers.main`

Repository-wide start commands can conflict with these per-service commands during redeploys. Runtime commands for v2 are therefore managed at the Railway service level.
