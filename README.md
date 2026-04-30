# MCU mc-multimodal-agent

Formal AgentBeats/Amber submission wrapper for
[`win10ogod/mc-multimodal-agent`](https://github.com/win10ogod/mc-multimodal-agent).

This repository is the public submission shell. The real Minecraft multimodal
agent lives in the `mc-multimodal-agent` git submodule, and this root repository
provides:

- an Amber manifest for AgentBeats registration;
- a Dockerfile that builds the Node AgentBeats A2A service;
- A2A conformance tests;
- a GitHub Actions workflow that builds, tests, and publishes the image to GHCR.

## Repository Layout

```text
.
├─ amber-manifest.json5       # Amber manifest URL target
├─ Dockerfile                 # Builds the Node AgentBeats A2A service
├─ mc-multimodal-agent/       # Core agent implementation submodule
├─ tests/                     # A2A conformance tests
└─ .github/workflows/         # Build/test/publish workflow
```

## Clone

Use recursive clone so the agent implementation is present:

```bash
git clone --recurse-submodules https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent.git
cd MCU-mc-multimodal-agent
```

If the repository was cloned without submodules:

```bash
git submodule update --init --recursive
```

## Required GitHub Configuration

You do not need to create a GitHub **Environment** for the current workflow.
The workflow does not use an `environment:` block.

Use repository-level secrets instead:

`Settings -> Secrets and variables -> Actions -> Repository secrets`

Recommended repository secrets:

```text
API_KEY          # model API key used by this agent
OPENAI_API_KEY   # optional alias; can be the same value as API_KEY
```

Recommended repository variables:

`Settings -> Secrets and variables -> Actions -> Variables`

```text
OPENAI_BASE_URL  # optional; use https://api.openai.com/v1 for official OpenAI
OPENAI_MODEL     # optional; use gpt-5.4 for official OpenAI access
```

For the official OpenAI API, set variables to:

```text
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4
```

If variables are not set, the workflow uses those official OpenAI defaults.

The publish workflow does not need a custom GHCR token. It uses GitHub's
automatic `GITHUB_TOKEN` with `packages: write`.

Do not commit a `.env` file to this repository. The workflow creates a temporary
`.env` file inside the runner from repository secrets.

## Run Locally

Build the submission image:

```bash
docker build -t mcu-mc-multimodal-agent .
```

Run with official OpenAI:

```bash
docker run --rm -p 9009:9009 \
  -e API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL="https://api.openai.com/v1" \
  -e OPENAI_MODEL="gpt-5.4" \
  mcu-mc-multimodal-agent
```

Health check:

```bash
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

The health check works even without an API key; the agent then logs that it is
using heuristic fallback actions. Real evaluation needs a valid key.

## Run A2A Tests

Start the Docker container first, then run:

```bash
uv sync --extra test
uv run pytest -v --agent-url http://127.0.0.1:9009
```

Expected result:

```text
tests/test_agent.py::test_agent_card PASSED
tests/test_agent.py::test_message[True] PASSED
tests/test_agent.py::test_message[False] PASSED
```

## Normal Development Flow

The core agent and the submission wrapper are separate git repositories.

When changing agent logic:

```bash
cd mc-multimodal-agent
npm run build
npm test
git add <changed files>
git commit -m "Describe agent change"
git push origin main
cd ..
```

Then update the wrapper submodule pointer:

```bash
git submodule update --remote mc-multimodal-agent
git add mc-multimodal-agent
git commit -m "Update mc-multimodal-agent submodule"
git push origin main
```

When changing only submission metadata, Docker, README, or workflow files:

```bash
git add Dockerfile amber-manifest.json5 README.md .github/workflows/test-and-publish.yml
git commit -m "Prepare submission wrapper"
git push origin main
```

## Publish Image

Pushing to `main` triggers:

```text
.github/workflows/test-and-publish.yml
```

The workflow:

1. checks out this repository with submodules;
2. builds the Docker image;
3. starts the A2A service;
4. runs A2A conformance tests;
5. publishes the image to GitHub Container Registry.

Published image:

```text
ghcr.io/madgaa-lab/mcu-mc-multimodal-agent:latest
```

After the first successful publish, make sure the GHCR package is public or
otherwise pullable by the evaluator:

`GitHub repository -> Packages -> mcu-mc-multimodal-agent -> Package settings -> Change visibility -> Public`

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

Manifest config fields:

```text
api_key              required, secret
openai_base_url      default https://api.openai.com/v1
openai_model         default gpt-5.4
model_every_n_steps  default 4
default_hold_steps   default 3
max_hold_steps       default 12
```

## AgentBeats / Leaderboard Usage

When another scenario needs this purple agent, reference the published image or
the Amber manifest. For direct Docker scenarios, use:

```toml
[[participants]]
agentbeats_id = "<your-purple-agent-id>"
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

`Settings -> Secrets and variables -> Actions -> Repository secrets`

Do not put real keys in `scenario.toml`, `README.md`, commits, issues, or logs.

## Troubleshooting

`docker pull ... unauthorized`

- The GHCR package is private, or the package is not linked to this repository.
- Make the package public, or authenticate with a token that has `read:packages`.

`OPENAI model_not_found`

- Check `OPENAI_BASE_URL`.
- For official OpenAI use `https://api.openai.com/v1`.
- Check that the API key has access to the model in `OPENAI_MODEL`.

`Unsupported parameter: max_tokens`

- Use a recent `mc-multimodal-agent` submodule commit.
- The current policy sends `max_completion_tokens` first and falls back for
  compatible providers.

`Container is unhealthy`

- Check container logs first:

```bash
docker logs <container-name>
```

- Verify that `/.well-known/agent-card.json` is available on port `9009`:

```bash
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

`Submodule directory is empty`

```bash
git submodule update --init --recursive
```
