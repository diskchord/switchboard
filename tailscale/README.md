# Tailscale Service

This app is configured for the newer Tailscale Service syntax.

```bash
tailscale serve set-config --all tailscale/serveconfig.json
tailscale serve advertise svc:switchboard
tailscale serve get-config --all
```

The equivalent one-shot command is:

```bash
tailscale serve --service=svc:switchboard --https=443 127.0.0.1:8766
```

Provider callbacks must be reachable by the provider over public HTTPS. A private Tailscale Service is appropriate for your own web/app access inside the tailnet; use a public endpoint or Tailscale Funnel for any callback URL that Telnyx, Twilio, or Rev.ai must call.

Callback and public-fetch paths that may need public reachability:

- `/api/telnyx/webhook`
- `/api/twilio/webhook`
- `/api/telnyx/voice`
- `/api/twilio/voice`
- `/api/telnyx/voice/recording`
- `/api/twilio/voice/recording`
- `/api/telnyx/voice/transcription`
- `/api/twilio/voice/transcription`
- `/api/revai/webhook`
- `/uploads/...` when `TEXTING_PUBLIC_UPLOAD_BASE_URL` points at this app

Keep `/media/...` private; it serves received attachments and voicemail recordings through Switchboard's normal authenticated app route.
