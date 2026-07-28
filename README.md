# HomeAssist

Home Assistant Voice PE → Home Assistant → Hermes Agent conversation bridge.

This repository contains:

- A portable Home Assistant Container setup.
- A custom Home Assistant conversation integration named **Hermes Conversation**.
- Session continuity using Home Assistant `conversation_id` mapped directly to Hermes's `X-Hermes-Session-Id`.
- A migration-friendly layout for moving from macOS Docker Desktop to Raspberry Pi OS later.

## Architecture

```text
Home Assistant Voice PE
        │ audio
        ▼
Home Assistant Assist Pipeline
        │ final STT transcript
        ▼
Hermes Conversation integration
        │ HTTP + X-Hermes-Session-Id
        ▼
Hermes Agent API Server
        │ cloud LLM + tools
        ▼
Home Assistant TTS
        │ audio
        ▼
Voice PE
```

## 1. Start Hermes on macOS

Install and configure Hermes first, then enable its API server in `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=replace-with-a-long-random-secret
```

Start it:

```bash
hermes gateway
```

Test from the Mac:

```bash
curl http://127.0.0.1:8642/health
```

For Docker Desktop bridge networking, Home Assistant reaches the Mac through:

```text
http://host.docker.internal:8642
```

## 2. Start Home Assistant Container

Copy the example environment file:

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

The compose file publishes port `8123`, which is the most predictable option on Docker Desktop. If Voice PE automatic discovery does not work, find its LAN IP in your router and add it manually through the ESPHome integration.

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

Then create or edit an Assist pipeline:

```text
Settings
→ Voice assistants
→ Add assistant / Edit assistant
→ Conversation agent: Hermes Conversation
```

Every final STT transcript sent through this pipeline is forwarded to Hermes.

## 4. Session handling

The integration follows this rule:

```text
HA conversation_id == Hermes X-Hermes-Session-Id
```

When Home Assistant supplies no ID, the adapter generates one. Follow-up turns that retain the same Home Assistant conversation ID are sent to the same Hermes session.

Hermes is asked to append one non-spoken control marker:

```text
<ha_continue>true</ha_continue>
```

or:

```text
<ha_continue>false</ha_continue>
```

The integration strips this marker before sending text to TTS. When the marker is absent, `Continue mode: Auto` uses a conservative question heuristic.

## 5. Update the custom component

After changing files:

```bash
cp -R custom_components/hermes_conversation config/custom_components/
docker compose restart homeassistant
```

Enable debug logging temporarily in `config/configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.hermes_conversation: debug
```

## 6. Migrate to Raspberry Pi

On Raspberry Pi OS 64-bit:

1. Install Docker Engine and Docker Compose.
2. Copy this repository and the `config/` directory.
3. Change `HERMES_API_URL` from `host.docker.internal` to `http://127.0.0.1:8642` if Hermes runs natively on the same Pi.
4. Reinstall Hermes natively on the Pi instead of copying the macOS runtime.
5. Start Home Assistant with `docker compose up -d`.

For native Linux, you may replace published ports with `network_mode: host` if local discovery requires it.

## Current scope

Implemented:

- Home Assistant config flow.
- Conversation entity.
- Hermes bearer authentication.
- Session continuity.
- Configurable timeout.
- Continue-conversation parsing.
- Health validation during setup.
- Traditional Chinese and English UI strings.

Not yet implemented:

- Streaming partial replies.
- Hermes approval prompts.
- Structured Home Assistant tool exposure.
- Automatic Voice PE discovery troubleshooting.
- Rich tool-call rendering in Home Assistant's chat log.
