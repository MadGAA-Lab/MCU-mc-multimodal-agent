<div align="center">

# MCU · mc-multimodal-agent

**A Minecraft multimodal agent, packaged for AgentBeats / Amber.**

[![Build & Publish](https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent/actions/workflows/test-and-publish.yml/badge.svg)](https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent/actions/workflows/test-and-publish.yml)
[![Container](https://img.shields.io/badge/ghcr.io-madgaa--lab%2Fmcu--mc--multimodal--agent-2496ED?logo=docker&logoColor=white)](https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent/pkgs/container/mcu-mc-multimodal-agent)
[![A2A](https://img.shields.io/badge/protocol-A2A-7B61FF)](#run-a2a-tests)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

<sub>Submission wrapper for <a href="https://github.com/win10ogod/mc-multimodal-agent"><code>win10ogod/mc-multimodal-agent</code></a> — the real agent lives in the <code>mc-multimodal-agent</code> submodule.</sub>

</div>

---

## Overview

This repository is the **public submission shell** that turns the upstream Minecraft multimodal agent into a drop-in AgentBeats participant. It hands AgentBeats / Amber a single, reproducible artifact: a containerized A2A service that any scenario can call without knowing the internals.

What the wrapper provides:

- **Amber manifest** — one-line registration target for AgentBeats.
- **Dockerfile** — builds the Node.js A2A service from the pinned submodule.
- **A2A conformance tests** — verify the container before it ever ships.
- **CI/CD** — GitHub Actions builds, tests, and publishes the image to GHCR on every push to `main`.

> The agent perceives Minecraft state, reasons with a multimodal LLM, and acts through the A2A protocol on port **9009**.

## Repository Layout

```text
.
├─ amber-manifest.json5       # Amber manifest URL target
├─ Dockerfile                 # Builds the Node AgentBeats A2A service
├─ mc-multimodal-agent/       # Core agent implementation (submodule)
├─ tests/                     # A2A conformance tests
└─ .github/workflows/         # Build / test / publish workflow
```

## Quick Start

### 1. Clone

Clone the wrapper, then initialize only the core agent submodule used by the Docker build:

```bash
git clone https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent.git
cd MCU-mc-multimodal-agent
git submodule update --init --recursive mc-multimodal-agent
```

### 2. Build & Run

```bash
docker build -t mcu-mc-multimodal-agent .

docker run --rm -p 9009:9009 \
  -e API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL="https://api.openai.com/v1" \
  -e OPENAI_MODEL="gpt-5.4" \
  mcu-mc-multimodal-agent
```

### 3. Health Check

```bash
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

The endpoint responds even without an API key — the agent then logs that it is using heuristic fallback actions. Real evaluation needs a valid key.

## GitHub Configuration

The workflow does **not** require a GitHub Environment (no `environment:` block). Configure repository-level secrets and variables instead.

**Repository Secrets** — `Settings → Secrets and variables → Actions → Repository secrets`

| Name             | Required | Notes                                              |
|------------------|----------|----------------------------------------------------|
| `API_KEY`        | ✓        | Model API key used by this agent                   |
| `OPENAI_API_KEY` | optional | Alias; usually the same value as `API_KEY`         |

**Repository Variables** — `Settings → Secrets and variables → Actions → Variables`

| Name              | Default                          | Notes                          |
|-------------------|----------------------------------|--------------------------------|
| `OPENAI_BASE_URL` | `https://api.openai.com/v1`      | Override for non-OpenAI hosts  |
| `OPENAI_MODEL`    | `gpt-5.4`                        | Override per provider          |

If variables are not set, the workflow falls back to the official OpenAI defaults above.

> **GHCR token:** not needed. The publish workflow uses GitHub's automatic `GITHUB_TOKEN` with `packages: write`.
>
> **Never commit `.env`.** The workflow builds a temporary `.env` inside the runner from repository secrets.

## Run A2A Tests

Start the Docker container first, then:

```bash
uv sync --extra test
uv run pytest -v tests --agent-url http://127.0.0.1:9009
```

Expected output:

```text
tests/test_agent.py::test_agent_card PASSED
tests/test_agent.py::test_message[True] PASSED
tests/test_agent.py::test_message[False] PASSED
```

## Development Flow

The core agent and the submission wrapper are **separate git repositories**. Pick the path that matches your change.

<details>
<summary><strong>Changing agent logic</strong> (inside the submodule)</summary>

```bash
cd mc-multimodal-agent
npm run build
npm test
git add <changed files>
git commit -m "Describe agent change"
git push origin main
cd ..
```

Then update the wrapper's submodule pointer:

```bash
git submodule update --remote mc-multimodal-agent
git add mc-multimodal-agent
git commit -m "Update mc-multimodal-agent submodule"
git push origin main
```

</details>

<details>
<summary><strong>Changing submission metadata</strong> (Docker, README, workflow)</summary>

```bash
git add Dockerfile amber-manifest.json5 README.md .github/workflows/test-and-publish.yml
git commit -m "Prepare submission wrapper"
git push origin main
```

</details>

## Publishing the Image

Every push to `main` triggers [`.github/workflows/test-and-publish.yml`](.github/workflows/test-and-publish.yml), which:

1. checks out this repository and the `mc-multimodal-agent` submodule only;
2. builds the Docker image;
3. starts the A2A service;
4. runs A2A conformance tests;
5. publishes the image to GitHub Container Registry.

**Published image:**

```text
ghcr.io/madgaa-lab/mcu-mc-multimodal-agent:latest
```

After the first successful publish, make the package pullable by evaluators:

`GitHub repo → Packages → mcu-mc-multimodal-agent → Package settings → Change visibility → Public`

Verify from a clean machine:

```bash
docker pull ghcr.io/madgaa-lab/mcu-mc-multimodal-agent:latest
```

## Amber Manifest

Use the raw manifest URL when a form asks for **Amber Manifest URL**:

```text
https://raw.githubusercontent.com/MadGAA-Lab/MCU-mc-multimodal-agent/main/amber-manifest.json5
```

Repository file:

```text
https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent/blob/main/amber-manifest.json5
```

The manifest exposes one A2A endpoint:

```text
name: a2a_endpoint
port: 9009
export: a2a
```

**Manifest config fields:**

| Field                  | Required | Default                       |
|------------------------|----------|-------------------------------|
| `api_key`              | ✓ secret | —                             |
| `openai_base_url`      |          | `https://api.openai.com/v1`   |
| `openai_model`         |          | `gpt-5.4`                     |
| `model_every_n_steps`  |          | `4`                           |
| `default_hold_steps`   |          | `3`                           |
| `max_hold_steps`       |          | `12`                          |

## AgentBeats / Leaderboard Usage

When another scenario needs this purple agent, reference the published image or the Amber manifest. For direct Docker scenarios:

```toml
[[participants]]
agentbeats_id = "019ddef4-91b9-75e0-8d4f-e2eeb0bd8f3a"
name = "agent"

[participants.env]
API_KEY = "${API_KEY}"
OPENAI_API_KEY = "${API_KEY}"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-5.4"
OPENAI_API_MODE = "chat"
OPENAI_STRUCTURED_OUTPUTS = "true"
OPENAI_REQUEST_TIMEOUT_MS = "180000"
OPENAI_MAX_RETRIES = "6"
AGENTBEATS_MODEL_EVERY_N_STEPS = "4"
AGENTBEATS_DEFAULT_HOLD_STEPS = "3"
AGENTBEATS_MAX_HOLD_STEPS = "12"
```

For GitHub Actions in a leaderboard repository, put `API_KEY` in:

`Settings → Secrets and variables → Actions → Repository secrets`

> ⚠️ Never put real keys in `scenario.toml`, `README.md`, commits, issues, or logs.

## Troubleshooting

<details>
<summary><code>docker pull ... unauthorized</code></summary>

The GHCR package is private, or it is not linked to this repository. Make the package public, or authenticate with a token that has `read:packages`.

</details>

<details>
<summary><code>OPENAI model_not_found</code></summary>

- Check `OPENAI_BASE_URL`.
- For official OpenAI use `https://api.openai.com/v1`.
- Confirm the API key has access to the model in `OPENAI_MODEL`.

</details>

<details>
<summary><code>Unsupported parameter: max_tokens</code></summary>

Use a recent `mc-multimodal-agent` submodule commit. The current policy sends `max_completion_tokens` first and falls back for compatible providers.

</details>

<details>
<summary><code>Container is unhealthy</code></summary>

Check the logs and the agent card endpoint:

```bash
docker logs <container-name>
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

</details>

<details>
<summary><code>Submodule directory is empty</code></summary>

```bash
git submodule update --init --recursive
```

</details>

---

<div align="center">
<sub>Built for <a href="https://agentbeats.org">AgentBeats</a> · Powered by the upstream <a href="https://github.com/win10ogod/mc-multimodal-agent"><code>mc-multimodal-agent</code></a> project.</sub>
</div>
