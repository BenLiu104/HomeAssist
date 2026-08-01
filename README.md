# HomeAssist

Home Assistant Voice PE → Home Assistant → Hermes Agent conversation bridge.

## Verified design

This integration uses Hermes's built-in API Server and its stateful OpenAI Responses-compatible endpoint:

```text
POST /v1/responses
```

Home Assistant's `conversation_id` is sent as Hermes's named `conversation` value. Hermes then automatically chains each new turn to the latest stored response in that named conversation, including previous tool calls and tool results.

`POST /v1/chat/completions` is deliberately not used for the voice pipeline because that endpoint is stateless and requires the client to resend the full `messages` history on every request.

## Architecture

```text
Home Assistant Voice PE
        │ audio
        ▼
Home Assistant Assist Pipeline
        │ final STT transcript
        ▼
Hermes Conversation integration
        │ POST /v1/responses
        │ conversation=home-assistant:<HA conversation_id>
        ▼
Hermes Agent API Server
        │ cloud LLM + tools + memory + skills
        ▼
Home Assistant TTS
        │ audio
        ▼
Voice PE
```

## 1. Enable Hermes API Server

Add to `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-with-a-long-random-secret
```

Defaults:

```text
Host: 127.0.0.1
Port: 8642
```

Start the gateway:

```bash
hermes gateway
```

Test it:

```bash
curl http://127.0.0.1:8642/health
```

The public health endpoint should return:

```json
{"status":"ok"}
```

For Home Assistant running in Docker Desktop on macOS, Hermes may need to listen on an address reachable through Docker Desktop:

```dotenv
API_SERVER_HOST=0.0.0.0
```

Then Home Assistant connects to:

```text
http://host.docker.internal:8642
```

Keep the bearer key enabled, do not port-forward 8642 on the router, and leave CORS unset because the Home Assistant integration is a backend client rather than a browser.

## 2. Start Home Assistant Container

```bash
cp .env.example .env
mkdir -p config/custom_components
cp -R custom_components/hermes_conversation config/custom_components/
docker compose up -d
```

Open:

```text
http://localhost:8123
```

The compose file publishes port `8123`, which is predictable on Docker Desktop. If Voice PE discovery does not work, find its LAN IP in the router and add it manually through the ESPHome integration.

## 3. Add Hermes Conversation

In Home Assistant:

```text
Settings
→ Devices & services
→ Add integration
→ Hermes Conversation
```

Recommended macOS values:

```text
Hermes API URL: http://host.docker.internal:8642
API key: same value as API_SERVER_KEY
Model: hermes-agent
Timeout: 180 seconds
Continue mode: Auto
```

Then select it in the Assist pipeline:

```text
Settings
→ Voice assistants
→ Add assistant / Edit assistant
→ Conversation agent: Hermes Conversation
```

Every final STT transcript sent through that pipeline is forwarded to Hermes.

## 4. Conversation state

The adapter follows this rule:

```text
HA conversation_id
        ↓
Hermes named conversation:
home-assistant:<HA conversation_id>
```

A missing Home Assistant ID produces a new UUID. Follow-up turns retaining the same Home Assistant ID use the same Hermes named conversation.

Hermes's Responses API stores the complete response chain server-side, including tool calls and tool outputs. The adapter therefore sends only the current transcript instead of duplicating the full history.

The header:

```text
X-Hermes-Session-Key: home-assistant:<integration-entry-id>
```

provides a stable long-term-memory scope for the integration. It is separate from the transcript conversation chain and must not be treated as the multi-turn conversation identifier.

## 5. Continue listening

Hermes is instructed to append one non-spoken marker:

```text
<ha_continue>true</ha_continue>
```

or:

```text
<ha_continue>false</ha_continue>
```

The integration removes it before TTS. When absent, Auto mode conservatively continues only when the response ends in a question mark.

## 6. Update the custom component

```bash
cp -R custom_components/hermes_conversation config/custom_components/
docker compose restart homeassistant
```

Temporary debug logging in `config/configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.hermes_conversation: debug
```

## 7. Migrate to Raspberry Pi

On Raspberry Pi OS 64-bit:

1. Install Docker Engine and Docker Compose.
2. Copy this repository and `config/`.
3. Reinstall Hermes natively on the Pi.
4. If Home Assistant uses host networking and Hermes binds to loopback, change the API URL to `http://127.0.0.1:8642`.
5. Start with `docker compose up -d`.

Do not copy the macOS Hermes runtime binaries to the Pi; copy only configuration, skills, memory, and other portable data as appropriate.

## Hermes API notes verified against the official documentation

- `/v1/chat/completions` is stateless and takes the full `messages` array.
- `/v1/responses` supports server-side state with `previous_response_id` or the named `conversation` parameter.
- Named conversations automatically chain to their latest response.
- Responses preserve previous tool calls and tool outputs.
- Raw Responses API JSON returns assistant text inside `output[]` message items containing `output_text` parts.
- `/v1/runs` is intended for long-running work with polling, SSE events, cancellation, and approval handling.
- Per-request model selection supports `model`, `provider`, and `model_options`, but a bare `model` on OpenAI-compatible endpoints may be ignored unless direct model requests are enabled or a model route matches.
- `/health` is a public liveness check; `/health/detailed` is the authenticated readiness check.
- The API server gives access to Hermes's full toolset, including terminal tools, so the bearer key must be protected.

## Current scope

Implemented:

- Home Assistant config flow.
- Conversation entity.
- Stateful `/v1/responses` calls.
- Named conversation continuity.
- Stable long-term-memory scope header.
- Bearer authentication.
- Configurable timeout.
- Continue-conversation parsing.
- Health validation during setup.
- Traditional Chinese and English UI strings.

Not yet implemented:

- Streaming partial replies.
- Hermes Runs API and approval prompts.
- Structured Home Assistant tool exposure.
- Automatic Voice PE discovery troubleshooting.
- Rich tool-call rendering in Home Assistant's chat log.
