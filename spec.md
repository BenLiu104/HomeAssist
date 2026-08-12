# HomeAssist Specification

## 1. Purpose

Build a local-first voice orchestration stack in which Home Assistant coordinates the voice pipeline and Hermes Agent is the single conversational brain.

Initial deployment:

- Home Assistant Container on macOS Docker Desktop.
- Hermes Agent installed natively on macOS.
- Home Assistant Voice Preview Edition on the same home LAN.
- Cloud LLM configured through Hermes.
- Later migration to Raspberry Pi OS 64-bit without redesigning the integration.

## 2. Goals

1. Forward final STT transcripts from a selected Home Assistant Assist pipeline
   to Hermes, except adapter-local session control utterances.
2. Return Hermes's final text response to Home Assistant for TTS playback on Voice PE.
3. Preserve multi-turn context across immediate follow-ups and later wake words,
   including Hermes tool calls and tool outputs.
4. Keep the Home Assistant integration thin; reasoning, tools, skills, and memory remain in Hermes.
5. Avoid exposing the Hermes API to the public Internet.
6. Keep Home Assistant configuration portable between macOS and Raspberry Pi.
7. Keep spoken output concise and safe for a voice-only interface while allowing
   deliberately enabled Home Assistant, web-research, and memory tools.

## 3. Non-goals for MVP

- Running an LLM locally.
- Implementing STT or TTS inside this repository.
- Replacing Voice PE firmware.
- Token-by-token speech streaming.
- Automatically approving dangerous Hermes tool calls.
- Giving Hermes unrestricted Home Assistant administrator access.
- Identifying multiple humans by voice.

## 4. Verified Hermes API choice

The integration shall use:

```text
POST /v1/responses
```

Reason:

- `/v1/chat/completions` is stateless and requires the full `messages` history on every request.
- `/v1/responses` stores conversation state server-side.
- It supports both `previous_response_id` and named `conversation` chaining.
- Named conversations automatically chain each request to the latest stored response.
- Stored chains preserve tool calls and tool results.

The MVP shall use the named `conversation` parameter rather than maintain a separate Home Assistant-to-response-ID mapping.

## 5. System architecture

```text
Voice PE
  ├─ wake word
  ├─ microphone and audio preprocessing
  └─ speaker
        │
        ▼
Home Assistant Assist Pipeline
  ├─ STT provider
  ├─ Hermes Conversation entity
  └─ TTS provider
        │
        ▼
Hermes API Server
  ├─ /v1/responses
  ├─ cloud LLM
  ├─ tools and skills
  ├─ server-side conversation state
  └─ long-term memory
```

## 6. Home Assistant responsibility

Home Assistant shall:

- Receive audio from Voice PE.
- Run the configured STT provider.
- Send the final transcript to the selected conversation entity.
- Supply a `conversation_id` when continuing an Assist conversation.
- Convert the adapter response into speech using the selected TTS provider.
- Re-open listening when `continue_conversation` is true.

## 7. Hermes responsibility

Hermes shall:

- Accept authenticated requests through its built-in API Server.
- Use its configured provider, tools, memory, and skills.
- Persist the Responses API chain for each named conversation.
- Return structured Responses API output items.
- Return one final user-facing assistant message.
- Keep the Home Assistant profile tool allowlist separate from the adapter.
  The intended profile allows Home Assistant, web, and memory tools; it does
  not require terminal, file-system, or browser-automation tools.
- Follow the adapter instruction to append exactly one non-spoken marker:

```xml
<ha_continue>true</ha_continue>
```

or:

```xml
<ha_continue>false</ha_continue>
```

## 8. Conversation adapter responsibility

The adapter shall:

1. Implement a Home Assistant `ConversationEntity`.
2. Receive `ConversationInput.text`.
3. Resolve a stable scope from Home Assistant user, satellite, or device identity.
4. Map short Home Assistant interactions onto a persisted working session.
5. Build an opaque Hermes named conversation scoped to the integration and user.
6. Authenticate using `Authorization: Bearer <API_SERVER_KEY>`.
7. Call `POST /v1/responses`.
8. Send only the current transcript as `input`.
9. Set `store=true`.
10. Send voice-specific instructions through the Responses API `instructions` field.
11. Parse assistant text from `output[]` message items containing `output_text` parts.
12. Ignore function-call and function-call-output items when producing TTS speech.
13. Strip the `<ha_continue>` marker.
14. Add the cleaned response to the Home Assistant chat log.
15. Return the same Home Assistant conversation ID.
16. Set `continue_conversation` from the explicit marker or configured fallback.
17. Surface timeout, authentication, connection, and malformed-response errors safely.
18. Serialize same-scope requests so two overlapping utterances cannot reorder
    the persisted Hermes conversation chain.

## 9. Request contract

```http
POST /v1/responses
Authorization: Bearer <API_SERVER_KEY>
Content-Type: application/json
X-Hermes-Session-Key: home-assistant:<integration-entry-id>:<scope-hash>
```

Example body:

```json
{
  "model": "hermes-agent",
  "input": "幫我睇下聽日天氣。",
  "instructions": "You are replying through a Home Assistant voice assistant...",
  "conversation": "home-assistant:entry-id:scope-hash:session-uuid",
  "store": true,
  "stream": false
}
```

The `conversation` value controls working transcript continuity. `X-Hermes-Session-Key` is a separate stable, opaque scope used by Hermes long-term-memory providers and must not be confused with the conversation chain.

## 10. Response contract

Expected raw JSON shape:

```json
{
  "id": "resp_abc123",
  "object": "response",
  "status": "completed",
  "output": [
    {
      "type": "function_call",
      "name": "some_tool",
      "arguments": "{}",
      "call_id": "call_1"
    },
    {
      "type": "function_call_output",
      "call_id": "call_1",
      "output": "tool result"
    },
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "聽日大致多雲。<ha_continue>false</ha_continue>"
        }
      ]
    }
  ]
}
```

Spoken result:

```text
聽日大致多雲。
```

## 11. Session rules

There are three separate continuity mechanisms:

1. `<ha_continue>` controls whether Assist immediately reopens the microphone.
2. A persisted working session selects the Hermes named conversation across wake words.
3. Hermes durable memory stores only explicit memories and stable preferences.

Working-session rules:

- Scope priority is Home Assistant user, then satellite, then device, then a default scope.
- A normal session remains active for 10 minutes after the latest turn.
- A pinned cooking, guided, or research session remains active for 2 hours.
- Expiry starts a new named conversation while retaining the previous one for resume.
- `開始新話題` / `新話題` starts a fresh normal session.
- `繼續頭先` / `繼續之前個話題` restores the previous session.
- `結束呢個話題` / `結束對話` closes the active session.
- `開始研究模式` and `開始煮餸模式` start a fresh pinned session.
- Hermes may return a non-spoken `<ha_session>pin|release|unchanged</ha_session>`
  directive for naturally phrased multi-step tasks.
- The routing map is persisted in Home Assistant storage and survives restarts.
- Hermes remains the owner of transcript, tool-call, and tool-result contents.
- Home Assistant chat log remains for UI consistency, not primary memory storage.

## 12. Continue modes

### Auto

Priority:

1. Explicit `<ha_continue>true|false</ha_continue>` marker.
2. Conservative fallback: true only when the spoken response ends in `?` or `？`.

### Always

Always return `continue_conversation=true`. Testing only.

### Never

Always return `continue_conversation=false`.

This is the currently deployed mode. Automatic follow-up listening is disabled
because Voice PE firmware `26.6.0` can open the microphone before its own
response starts playing, causing the device to transcribe its own speaker
output as the next user turn. Every turn therefore needs the wake word. See the
README section "Voice PE follow-up listening race" for the upstream cause and
tracking issues.

## 13. Security requirements

- Hermes API key must never be committed.
- `.env`, Home Assistant config, databases, logs, and secrets must be ignored by Git.
- Public router port forwarding for 8642 is prohibited.
- CORS should remain unset because Home Assistant calls Hermes from backend code, not browser JavaScript.
- On macOS Docker Desktop, Hermes may bind to `0.0.0.0` only when required for container reachability, protected by bearer authentication and the macOS firewall.
- On a shared Linux host with compatible networking, loopback binding is preferred.
- The adapter must impose a timeout.
- Logs must not print bearer tokens.
- The API Server exposes Hermes's full toolset, including terminal operations; possession of the API key is highly privileged.
- The Home Assistant Hermes profile shall enable only its required tools:
  Home Assistant, web research, and durable memory. Its tool configuration and
  SOUL prompt are external profile data, not custom-component configuration.
- Web pages and all tool results are untrusted data. Only the user's direct
  utterance may authorize a Home Assistant action or a durable-memory write.
- A web-search provider key (such as Tavily) belongs in the private Hermes
  profile environment and must not be committed.

## 14. Configuration fields

| Field | Default | Description |
|---|---:|---|
| API URL | `http://host.docker.internal:8642` | Hermes base URL |
| API key | none | Required Hermes bearer token |
| Model | `hermes-agent` | Advertised Hermes profile/model name |
| Timeout | `180` | Maximum request duration in seconds |
| Continue mode | `auto` | `auto`, `always`, or `never`; deployed as `never`, see section 12 |

A bare `model` value sent to OpenAI-compatible endpoints may be ignored unless Hermes direct model requests are enabled or a configured model route matches. Explicit `provider` and model-routing support are outside the MVP configuration UI.

## 15. TTS runtime configuration

STT and TTS providers are Home Assistant runtime configuration, not code owned
by this custom component. The current deployment uses SenseVoice through a
Wyoming endpoint for Cantonese STT and the Edge TTS integration for output.

```text
custom_components/edge_tts/           # vendored in this repository
config/custom_components/edge_tts/    # deployed copy, ignored by Git
Voice: zh-HK-HiuMaanNeural
Rate: +25%
```

The Assist pipeline selects the Edge TTS engine and language. The Edge TTS
integration's options select the neural voice and rate. This repository vendors
a modified copy of the upstream community component; its local changes are a
persisted `rate` option, chunk-by-chunk streaming synthesis, and a reusable HTTP
connector. Upgrading from upstream requires reapplying them. The README
documents each change and why it exists.

Edge TTS uses Microsoft-hosted synthesis. It does not need an API key, but it
is not local-only and requires Internet access.

## 16. Health checks

- `GET /health` is the cheap public liveness check and returns `{"status":"ok"}`.
- `GET /v1/health` is an alias for OpenAI-compatible clients.
- `GET /health/detailed` is the authenticated readiness check.
- Setup validation may use `/health`; future diagnostics should use `/health/detailed`.

## 17. Error behavior

| Failure | User-facing behavior |
|---|---|
| Hermes offline | Spoken temporary-unavailable response |
| Timeout | Spoken timeout response |
| 401/403 | Spoken authentication error |
| Invalid JSON | Spoken invalid-response error |
| Missing `output` | Spoken invalid-response error |
| No assistant `output_text` | Spoken empty/invalid-response error |

## 18. Deployment

### macOS MVP

```text
Docker Desktop
└─ Home Assistant Container
       │ http://host.docker.internal:8642
       ▼
macOS host
└─ Hermes Agent API Server
```

### Raspberry Pi target

```text
Raspberry Pi OS 64-bit
├─ Home Assistant Container
└─ Hermes Agent native
```

The exact API URL depends on Docker network mode and Hermes bind address. Do not assume that `127.0.0.1` inside a bridged container means the Linux host.

## 19. Acceptance criteria

- The custom integration appears in Add Integration.
- Setup validates Hermes liveness.
- A typed Assist message reaches `/v1/responses` and returns speech.
- A Voice PE transcript reaches Hermes and is spoken through Voice PE.
- Turns inside the same working session retain Hermes context.
- Previous Hermes tool calls remain available on follow-up turns.
- The control marker is never spoken.
- The session marker is never spoken.
- Function-call output items are never accidentally read aloud.
- A second wake word within 10 minutes resumes the same working session.
- A normal session expires after 10 minutes and can be restored with `繼續頭先`.
- A pinned cooking or research session remains active for up to 2 hours.
- Session routing survives a Home Assistant restart.
- Hermes can use Home Assistant, web search, and durable memory tools from the API profile.
- The voice response contains no reasoning, tool traces, `<ha_session>`, or
  `<ha_continue>` markers.
- A configured Edge TTS engine can generate Cantonese output at the selected
  integration voice and rate.
- Restarting Home Assistant does not delete its configuration.
- Migration to Raspberry Pi requires only host/network endpoint adjustments and native Hermes reinstallation.

## 20. Future phases

1. Add an adapter-owned Home Assistant entity allowlist and action policy.
2. Add Hermes Runs API support for long tasks, SSE progress, cancellation, and approvals.
3. Add streaming response support.
4. Add speaker identification for true multi-person memory scopes.
5. Add automated Home Assistant integration tests.
6. Add detailed health and latency sensors.
7. Add a UI for changing session timeouts and deleting stored response chains.
8. Add a separately scoped remote-control profile over Tailscale, with ACLs,
   sender allowlists, and confirmation for sensitive actions. Do not expose the
   Home Assistant or Hermes API directly to the public Internet.
