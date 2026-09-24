# Angular frontend

See the project-root `README.md` for setup, API and assumptions. Production UI is served by Nginx at `http://localhost:8080` with `/api` proxied to FastAPI.

For a local frontend-only build:

```sh
npm ci
npm run build
```

`npm run test:e2e` runs the browser smoke test against the **running Compose stack** and a local Google Chrome binary; use only disposable schedule data. Override `E2E_BASE_URL` or `CHROME_PATH` as needed. The test creates and removes a temporary vehicle/service and temporarily changes B14's traversal time.
