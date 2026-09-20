# Komodo Docker Compose GitOps Standard

This document outlines the operational standard for managing Docker Compose stacks deployed via Komodo.

---

## 1. GitOps Repository Layout

In Komodo-managed environments, the GitOps repository contains the active compose stack definition for the target environment:

```
komodo-gitops-repo/ (branch: {product}/{environment} or {org}/{stack})
├── docker-compose.yml
├── .env.example
└── configs/
```

---

## 2. Stack Naming Standard

Komodo stacks must follow the enterprise convention:
```
{product-name}-{environment}
```
*Example*:
`myapp-dev`, `myapp-prod`, `vpn-prod`

---

## 3. Service Definition in `docker-compose.yml`

Services added to the Compose file must follow standard container engineering principles:
```yaml
services:
  web:
    image: registry.contoso.com/myapp/web:1.0.0
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - NODE_ENV=production
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

## 4. Automated Image Updates & Sync Protocol

1. **YQ Path Precision**:
   Specify the exact yq selector path targeting the container image:
   ```yaml
   gitops_service_image_yq_path: .services.web.image
   ```
2. **Review & Approval Gate**:
   - Changes made to `docker-compose.yml` should be reviewed before trigger.
   - Once verified, the CI job commits the image update to the GitOps repo branch with `[skip ci]` and triggers the Komodo stack redeploy API.
