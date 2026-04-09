# Contributing

Thanks for your interest in improving Hermes Web UI.

## Local Setup

```bash
git clone https://github.com/MNPickle/hermes-webui.git ~/hermes-web-ui
cd ~/hermes-web-ui
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set a real token in `.env` before starting the app.

## Running the App

Normal local start:

```bash
cd ~/hermes-web-ui
./start.sh 5000
```

Development mode:

```bash
cd ~/hermes-web-ui
DEV=1 ./start.sh 5000
```

## What To Test

Before publishing changes, the most useful checks in this repo are:

```bash
./.venv/bin/python -m unittest tests.test_smoke
./tools/run_playwright_smoke.sh /tmp/hermes-pw-smoke http://127.0.0.1:5000/
./tools/run_playwright_update_validation.sh /tmp/hermes-pw-update http://127.0.0.1:5000/
```

Use the browser checks when a change affects visible UI behavior.

## Scope Guidelines

- Keep changes focused and avoid unrelated cleanup.
- Prefer updates that match the current Hermes install layout.
- Reuse existing UI patterns where possible.
- Do not revert unrelated local changes you did not make.

## Docs

If behavior changes, update the relevant docs too:

- [README.md](README.md)
- [docs/INSTALL.md](docs/INSTALL.md)
- [docs/USAGE.md](docs/USAGE.md)
- [docs/CONFIG.md](docs/CONFIG.md)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [CHANGELOG.md](CHANGELOG.md)
