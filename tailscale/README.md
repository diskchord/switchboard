# Tailscale Service

This app is configured for the newer Tailscale Service syntax.

```bash
tailscale serve set-config --all tailscale/serveconfig.json
tailscale serve advertise svc:texting-app
tailscale serve get-config --all
```

The equivalent one-shot command is:

```bash
tailscale serve --service=svc:texting-app --https=443 127.0.0.1:8766
```

Telnyx inbound webhooks must be reachable by Telnyx over public HTTPS. A private Tailscale Service is appropriate for your own web/app access inside the tailnet; use a public endpoint or Tailscale Funnel for the Telnyx webhook URL if Telnyx cannot reach the Service.
