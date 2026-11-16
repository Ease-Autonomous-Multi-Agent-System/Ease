# Ease web app

Next.js front end for Ease: task composer, live plan timeline, the agent's browser view, event log, approval side
panel, run history and Profile & apps settings.

The normal way to run it is with the whole stack from the repository root (see the main [README](../README.md)):

```bash
docker compose --profile web up -d --build    # http://localhost:3000
```

For development with hot reload, start the backend without the `web` profile and run the dev server here:

```bash
docker compose up -d --build                   # from the repository root
npm ci
npm run dev                                    # http://localhost:3000, talks to the API on http://localhost:8000
```

Checks: `npm run lint` and `npx tsc --noEmit`. The API and WebSocket addresses come from `NEXT_PUBLIC_API_URL` and
`NEXT_PUBLIC_WS_URL` (see `.env.example`).
