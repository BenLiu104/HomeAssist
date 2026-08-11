# HomeAssist

Home Assistant Voice PE → Home Assistant → Hermes Agent conversation bridge.

This repository contains the version-controlled bridge and its deployment
notes. Home Assistant's runtime configuration lives under `config/` and is
intentionally ignored, as do Hermes profile files and API keys.

## Verified design

This integration uses Hermes's built-in API Server and its stateful OpenAI Responses-compatible endpoint:

```text
POST /v1/responses
```

The integration maps short Home Assistant interactions onto a persisted Hermes
working session. Hermes automatically chains each turn in that named
conversation, including previous tool calls and tool results.

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
        │ persisted working-session name
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

Immediate listening, working context, and permanent memory are intentionally
separate:

```text
<ha_continue>       → reopen the microphone now
working session     → remember the current topic across wake words
Hermes memory tools → explicit memories and stable preferences
```

Normal working sessions remain active for 10 minutes. Research and cooking
sessions remain active for 2 hours. The routing state is saved under Home
Assistant's `.storage` directory and survives a restart.

Voice controls:

```text
開始新話題
繼續頭先
結束呢個話題
開始研究模式：幫我比較三款焗爐
開始煮餸模式：逐步教我整法式洋蔥湯
```

Hermes's Responses API stores the complete response chain server-side, including tool calls and tool outputs. The adapter therefore sends only the current transcript instead of duplicating the full history.

The header:

```text
X-Hermes-Session-Key: home-assistant:<integration-entry-id>:<scope-hash>
```

provides a stable, opaque long-term-memory-provider scope per known HA user or
device. It is separate from the transcript conversation chain.

Hermes durable memory is deliberately conservative: it should write only when
the user explicitly says to remember or forget something, or when a preference
is clearly stable. Secrets, temporary research state, and recipe steps stay out
of durable memory.

## 5. Configure the Hermes Home Assistant profile

The custom component is only the transport and session adapter. Model choice,
provider routes, tools, the SOUL prompt, and Hermes durable-memory backend are
configured in the Hermes profile, outside this repository.

For a voice-only Home Assistant agent, enable only the toolsets it needs:

- **Home Assistant** for exposed entity control.
- **Web** for current facts and research (for example, with a Tavily key in the
  profile's private `.env`).
- **Memory** for explicit long-term preferences.

Leave terminal, file-system, browser automation, and other general-purpose
tools disabled unless there is a separate, deliberate use case. The bearer key
grants access to the profile's enabled tools.

The adapter supplies its own per-request voice rules: concise natural speech in
the user's language, no chain of thought or tool traces, Home Assistant tools
for home control, and a strict trust boundary. Web pages and tool results are
data, never instructions; only the user's direct utterance can authorize a home
action or memory write.

## 6. Continue listening

Hermes is instructed to append one non-spoken marker:

```text
<ha_continue>true</ha_continue>
```

or:

```text
<ha_continue>false</ha_continue>
```

The integration removes it before TTS. When absent, Auto mode conservatively continues only when the response ends in a question mark.

## 7. Update the custom component

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

## 8. Optional Edge TTS for Cantonese voice output

The Assist pipeline can use any Home Assistant TTS provider. The current
runtime deployment uses the community [Edge TTS integration](https://github.com/hasscc/hass-edge-tts), installed under:

```text
config/custom_components/edge_tts/
```

That directory is ignored by Git, so it is a local Home Assistant deployment
detail rather than code shipped by this repository. After adding the
integration in Home Assistant, select its generated TTS engine in the Assist
pipeline. A working Cantonese example is:

```text
Voice: zh-HK-HiuMaanNeural
Rate: +25%
```

The standard Assist-pipeline screen selects the TTS engine and language; the
voice and rate are integration options. The local Edge TTS component has been
extended to expose and persist a `rate` option. Reapply that small local change
if the community component is manually replaced or upgraded.

Edge TTS contacts Microsoft services to synthesize speech. It needs no API key,
but it is not a local-only TTS provider and requires Internet access.

## 9. Migrate to Raspberry Pi

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
- Cross-wake working sessions with 10-minute normal and 2-hour pinned timeouts.
- Voice commands for new, resume, close, research, and cooking sessions.
- Home Assistant storage persistence for session routing.
- Bearer authentication.
- Configurable timeout.
- Continue-conversation parsing.
- Health validation during setup.
- Traditional Chinese and English UI strings.
- Profile-side Home Assistant, web-search, and durable-memory tools, with a
  voice-focused trust boundary.
- Optional Edge TTS runtime deployment for Cantonese speech.

Not yet implemented:

- Streaming partial replies.
- Hermes Runs API and approval prompts.
- Adapter-owned Home Assistant entity allowlist or tool policy.
- Automatic Voice PE discovery troubleshooting.
- Rich tool-call rendering in Home Assistant's chat log.
