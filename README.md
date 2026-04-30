# MCU mc-multimodal-agent

Formal AgentBeats/Amber submission wrapper for
[`win10ogod/mc-multimodal-agent`](https://github.com/win10ogod/mc-multimodal-agent).

The repository root contains the submission metadata, Docker build, and A2A
conformance workflow. The actual Minecraft multimodal policy implementation is
tracked as the `mc-multimodal-agent` git submodule.

## Structure

```text
.
├─ amber-manifest.json5       # Amber manifest for deployment
├─ Dockerfile                 # Builds the Node AgentBeats A2A service
├─ mc-multimodal-agent/       # Agent implementation submodule
├─ tests/                     # A2A conformance tests
└─ .github/workflows/         # Build/test/publish workflow
```

## Clone

```bash
git clone --recurse-submodules https://github.com/MadGAA-Lab/MCU-mc-multimodal-agent.git
```

If the repository was cloned without submodules:

```bash
git submodule update --init --recursive
```

## Run Locally

```bash
docker build -t mcu-mc-multimodal-agent .
docker run --rm -p 9009:9009 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}" \
  -e OPENAI_MODEL="${OPENAI_MODEL:-gpt-5.4}" \
  mcu-mc-multimodal-agent
```

Health check:

```bash
curl http://127.0.0.1:9009/.well-known/agent-card.json
```

## Test

Start the Docker container first, then run:

```bash
uv sync --extra test
uv run pytest -v --agent-url http://127.0.0.1:9009
```

## Publish

The GitHub Actions workflow builds the container and publishes:

```text
ghcr.io/madgaa-lab/mcu-mc-multimodal-agent:latest
```

Repository secrets used by the workflow or Amber deployment:

```text
API_KEY
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
```

For official OpenAI, use:

```text
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4
```

## Amber

Use [`amber-manifest.json5`](amber-manifest.json5) as the Amber manifest URL.
The manifest exposes a single A2A endpoint named `a2a_endpoint` on port `9009`.
