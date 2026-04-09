# Security Policy

Hermes Web UI is primarily designed as a local admin tool.

## Security Model

- The app is intended to run on localhost by default.
- API routes require `HERMES_WEBUI_TOKEN`.
- If you expose the app beyond localhost, use your own reverse proxy, TLS, and access controls.

## Supported Deployment Assumption

The safest default posture is:

- run on WSL2, Linux, or another local shell environment you control
- bind to localhost
- keep your token secret
- avoid exposing the UI directly to the public internet

## Reporting

If you find a security issue, please avoid opening a public issue with exploit details first.

Instead, contact the maintainer privately through the repository owner contact path you already use, then share:

- what you found
- how to reproduce it
- what versions or commits are affected
- whether sensitive data exposure is involved

## Notes

- This repo does not currently provide multi-user auth or role-based access control.
- If you need internet-facing deployment, put it behind infrastructure you trust and treat it as an admin surface.
