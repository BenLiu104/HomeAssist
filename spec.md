# HomeAssist Specification

## 1. Purpose

Build a local-first voice orchestration stack in which Home Assistant is the voice pipeline coordinator and Hermes Agent is the single conversational brain.

The initial deployment target is:

- Home Assistant Container on macOS Docker Desktop.
- Hermes Agent installed natively on macOS.
- Home Assistant Voice Preview Edition on the same home LAN.
- Cloud LLM configured through Hermes.
- Later migration to Raspberry Pi OS 64-bit without redesigning the application.

## 2. Goals

1. Forward every final STT transcript from a selected Home Assistant Assist pipeline to Hermes.
2. Return Hermes's final text response to Home Assistant for TTS playback on Voice PE.
3. Preserve multi-turn context across follow-up speech.
4. Keep the Home Assistant integration thin; agent reasoning, tools, and memory remain in Hermes.
5. Avoid exposing the Hermes API to the public Internet.
6. Keep Home Assistant configuration portable between macOS and Raspberry Pi.

## 3. Non-goals for MVP

- Running an LLM locally.
- Implementing STT or TTS inside this repository.
- Replacing Voice PE firmware.
- Streaming token-by-token speech.
- Automatically approving dangerous Hermes tool calls.
- Giving Hermes unrestricted administrator access to Home Assistant.
- Supporting multiple human identities from voice recognition.

## 4. System architecture

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
  ├─ cloud LLM
  ├─ session persistence
  ├─ tools
  └─ memory
```

## 5. Home Assistant responsibility

Home Assistant shall:

- Receive audio from Voice PE.
- Run the configured STT provider.
- Send the final transcript to the selected conversation entity.
- Supply a `conversation_id` when continuing a conversation.
- Convert the adapter's response into speech using the selected TTS provider.
- Re-open listening when `continue_conversation` is true.
- Keep device orchestration separate from Hermes until explicit HA tools are added.

## 6. Hermes responsibility

Hermes shall:

- Accept requests through its authenticated API server.
- Use cloud LLM providers and configured tools.
- Persist conversation state by session ID.
- Return one final user-facing answer.
- Indicate whether an immediate follow-up is expected by appending:

```xml
<ha_continue>true</ha_continue>
```

or:

```xml
<ha_continue>false</ha_continue>
```

The marker must not contain user-visible content.

## 7. Conversation adapter responsibility

The adapter shall:

1. Implement a Home Assistant `ConversationEntity`.
2. Receive `ConversationInput.text`.
3. Reuse `ConversationInput.conversation_id` when present.
4. Generate a UUID when no conversation ID is supplied.
5. Send the ID as `X-Hermes-Session-Id`.
6. Authenticate with `Authorization: Bearer <key>`.
7. Call Hermes `POST /v1/chat/completions`.
8. Strip the `<ha_continue>` marker from the reply.
9. Add the cleaned response to the Home Assistant chat log.
10. Return an `IntentResponse` containing speech.
11. Return the same conversation ID.
12. Set `continue_conversation` according to configuration and parsed marker.
13. Surface timeout, authentication, connection, and malformed-response errors as safe spoken messages and logs.

## 8. Request contract

```http
POST /v1/chat/completions
Authorization: Bearer <API_SERVER_KEY>
Content-Type: application/json
X-Hermes-Session-Id: <home-assistant-conversation-id>
```

Example body:

```json
{
  "model": "hermes-agent",
  "stream": false,
  "messages": [
    {
      "role": "system",
      "content": "You are responding through a voice assistant..."
    },
    {
      "role": "user",
      "content": "幫我睇下聽日天氣。"
    }
  ]
}
```

The adapter sends only the current user turn. Hermes owns persisted session history.

## 9. Response contract

Expected OpenAI-compatible response:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "聽日大致多雲。<ha_continue>false</ha_continue>"
      }
    }
  ]
}
```

The spoken result is:

```text
聽日大致多雲。
```

## 10. Session rules

- Same HA `conversation_id` means same Hermes session.
- Missing HA ID creates a new UUID.
- The adapter must not infer continuity using only device ID or elapsed time.
- Hermes stores actual dialogue history.
- Home Assistant chat log is used for UI consistency, not as Hermes's main memory source.
- Session cleanup belongs to Hermes.
- A new wake-word activation may receive a new HA conversation ID; the adapter follows the ID supplied by HA rather than guessing.

## 11. Continue modes

### Auto

Priority:

1. Explicit `<ha_continue>true|false</ha_continue>` marker.
2. Conservative heuristic:
   - true when the response ends with `?` or `？`;
   - otherwise false.

### Always

Always return `continue_conversation=true`. Intended only for testing.

### Never

Always return `continue_conversation=false`.

## 12. Security requirements

- Hermes API key must be stored in Home Assistant config-entry data, never committed.
- `.env`, `config/`, databases, logs, and secrets must be ignored by Git.
- Hermes should bind to `127.0.0.1` on Raspberry Pi when HA and Hermes share the host.
- On macOS Docker Desktop, Hermes may bind to `0.0.0.0`, but macOS firewall should limit access to trusted local traffic.
- Public port forwarding is prohibited.
- The adapter must impose a timeout.
- Logs must not print bearer tokens.
- Future HA control tools should use a dedicated low-privilege Home Assistant account.

## 13. Configuration fields

| Field | Default | Description |
|---|---:|---|
| API URL | `http://host.docker.internal:8642` | Hermes base URL |
| API key | none | Hermes API server bearer token |
| Model | `hermes-agent` | Model advertised by Hermes |
| Timeout | `180` | Maximum request duration in seconds |
| Continue mode | `auto` | `auto`, `always`, or `never` |

## 14. Error behavior

| Failure | User-facing behavior |
|---|---|
| Hermes offline | Spoken temporary-unavailable response |
| Timeout | Spoken timeout response |
| 401/403 | Spoken authentication error |
| Invalid JSON | Spoken invalid-response error |
| Missing content | Spoken empty-response error |

Detailed exceptions are written to Home Assistant logs.

## 15. Deployment

### macOS MVP

```text
Docker Desktop
└─ Home Assistant Container
       │ http://host.docker.internal:8642
       ▼
macOS host
└─ Hermes Agent API server
```

### Raspberry Pi target

```text
Raspberry Pi OS 64-bit
├─ Home Assistant Container
└─ Hermes Agent native
       ▲
       └─ http://127.0.0.1:8642
```

## 16. Acceptance criteria

- The custom integration appears in `Add integration`.
- Setup validates Hermes `/health`.
- A typed Assist message reaches Hermes and returns a response.
- A Voice PE transcript reaches Hermes and is spoken through Voice PE.
- Two turns with the same HA conversation ID retain Hermes context.
- The control marker is never spoken.
- Restarting Home Assistant does not delete its configuration.
- The repository can be moved to Raspberry Pi with only endpoint and host-network adjustments.

## 17. Future phases

1. Add Home Assistant tool calls with an allowlist.
2. Support Hermes Runs API and approval flows.
3. Stream progress to Home Assistant.
4. Add explicit user/device metadata headers.
5. Add automated integration tests using Home Assistant's pytest fixtures.
6. Add health and latency sensors.
7. Add session-reset service and dashboard controls.
