# Helm charts

What a service's Helm chart provides so the service's observability works in Kubernetes. These
are chart settings, applied by the platform or chart tooling; application code and the
observability module never render manifests. Values are described generically — map each item
onto the chart library in use. When the skill instruments a service, it hands this list over
instead of editing the chart.

## Requirements

| # | The chart provides | Why |
| --- | --- | --- |
| 1 | `observability.yml` from a ConfigMap, mounted at `/etc/observability/observability.yml`, with `OBSERVABILITY_CONFIG` pointing at it | one config file per environment ([config.md](config.md), [layout.md](layout.md)) |
| 2 | backend tokens from Secrets, mounted as files (for example under `/var/run/secrets/observability/`) and referenced in the yml as `${file:/var/run/secrets/observability/<name>}` | secrets never sit in the ConfigMap or in literal env values |
| 3 | downward-API env: pod name, pod UID, namespace and node name; where several clusters send to one backend, also the cluster's name (`K8S_CLUSTER_NAME`) from a cluster-level chart value, since the downward API doesn't provide it | the yml adds them to the resource, and the collector matches telemetry to its pod by UID |
| 4 | pod annotations `resource.opentelemetry.io/service.name`, `…/service.namespace`, `…/service.version` and `…/deployment.environment.name`, rendered from the same values as the yml's resource, plus one for every other attribute the yml's `resource.attributes` sets beyond the pod identity (for example `…/oneuptime.label.<key>`) | collector-derived telemetry (stdout logs, scraped metrics, eBPF data) gets the same identity as the SDK's: the yml's resource reaches only what the SDK sends ([data-model.md](data-model.md)) |
| 5 | a named metrics port (for example `http-metrics` on 9464) on the container and the Service, and a ServiceMonitor or PodMonitor for `/metrics` on it | Prometheus or the collector discovers and scrapes the endpoint the yml declares ([signals/metrics.md](signals/metrics.md)) |
| 6 | colourised logs switched off | colour codes break `json` and `plain` parsing |
| 7 | `/metrics` and the health endpoints kept off public ingress | they are internal; the ingress routes application paths only |
| 8 | a termination grace period longer than the telemetry flush timeout plus request draining | SIGTERM flushes queued telemetry before the pod is killed |
| 9 | for services that call a model: the Langfuse keys from a Secret, mounted as files without a trailing newline (`/var/run/secrets/observability/langfuse-public-key`, `…/langfuse-secret-key`); `OBS_LANGFUSE_URL`; `OBS_GENAI_CONTENT=full` only in the values of environments whose data is synthetic, unset (`off`) or `redacted` in production; egress to the Langfuse host | content stays out of production unless a feature's redactor is signed off; keys never sit in the ConfigMap ([signals/genai.md](signals/genai.md)) |

## One set of values

The service's identity is declared once in the chart's values and rendered into the yml's
resource (literally, or through the `OBS_*` variables the yml interpolates) and into the pod
annotations, so the SDK and the collector can never disagree. `service.namespace` is the
application group the workload is part of (its `partOf` name). A rendered pod template,
abridged:

```yaml
metadata:
  annotations:
    resource.opentelemetry.io/service.name: checkout-api
    resource.opentelemetry.io/service.namespace: commerce
    resource.opentelemetry.io/service.version: "1.8.2"
    resource.opentelemetry.io/deployment.environment.name: production
spec:
  terminationGracePeriodSeconds: 30
  containers:
    - name: app
      env:
        - name: OBSERVABILITY_CONFIG
          value: /etc/observability/observability.yml
        - name: OBS_SERVICE_NAME
          value: checkout-api
        - name: OBS_SERVICE_NAMESPACE
          value: commerce
        - name: OBS_SERVICE_VERSION
          value: "1.8.2"
        - name: OBS_ENVIRONMENT
          value: production
        - name: K8S_POD_NAME
          valueFrom: { fieldRef: { fieldPath: metadata.name } }
        - name: K8S_POD_UID
          valueFrom: { fieldRef: { fieldPath: metadata.uid } }
        - name: K8S_NAMESPACE_NAME
          valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
        - name: K8S_NODE_NAME
          valueFrom: { fieldRef: { fieldPath: spec.nodeName } }
        - name: K8S_CLUSTER_NAME         # only where several clusters send to one backend;
          value: prod-eu-1               #   from a cluster-level value
        - name: NO_COLOR                 # plus the framework's own colour switch
          value: "1"
      ports:
        - name: http-metrics
          containerPort: 9464
      volumeMounts:
        - name: observability-config
          mountPath: /etc/observability
          readOnly: true
        - name: observability-secrets
          mountPath: /var/run/secrets/observability
          readOnly: true
  volumes:
    - name: observability-config
      configMap:
        name: checkout-api-observability
    - name: observability-secrets
      secret:
        secretName: checkout-api-observability
```

The yml reads the same values:

```yaml
resource:
  service:
    name: "${OBS_SERVICE_NAME}"
    namespace: "${OBS_SERVICE_NAMESPACE:-}"
    version: "${OBS_SERVICE_VERSION}"
  environment: "${OBS_ENVIRONMENT}"
  attributes:
    k8s.pod.name: "${K8S_POD_NAME:-}"
    k8s.pod.uid: "${K8S_POD_UID:-}"
    k8s.namespace.name: "${K8S_NAMESPACE_NAME:-}"
    k8s.node.name: "${K8S_NODE_NAME:-}"
    k8s.cluster.name: "${K8S_CLUSTER_NAME:-}"   # dropped where the chart doesn't set it
```

And the monitor scrapes the named port:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: checkout-api
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: checkout-api
  endpoints:
    - port: http-metrics
      path: /metrics
      interval: 30s
```

The resource carries nothing else ([data-model.md](data-model.md#resource)).

## Workload notes

- **Jobs and CronJobs:** the annotations and env go on the Job's pod template; there is no
  metrics port and no monitor, because nothing scrapes a process that exits. Keep finished pods
  long enough for the collector to read their last log lines
  ([workloads/batch.md](workloads/batch.md)).
- **Frontends served by nginx:** mount the runtime RUM config (`observability.json`) per
  environment, and the nginx configuration with the JSON access log and the same-origin
  `/telemetry/` routes to the collector; a replay key injected by nginx comes from a Secret
  (`rum.session_replay.headers`), for example as an nginx include file holding the
  `proxy_set_header` line. The proxy locations follow the tested snippet in
  [workloads/spa.md](workloads/spa.md#the-nginx-proxy).
- **Collector address:** endpoints default to the collector named in the yml; per-environment
  overrides go in through the `OBS_*_ENDPOINT` variables the yml interpolates.
- **AI services on the collector route:** the Langfuse keys go to the collector's Secret
  instead of the app's (the app keeps them only for scores or for app code that uses the module's
  client); the collector's
  receiver takes larger messages; the optional gateway for whole traces is a StatefulSet of at
  least two replicas with a headless Service and read access to its endpoints
  ([collector](collector/README.md#genai-routing)).

## Handoff list

The skill reports the chart changes as a checklist for the platform or chart tooling:

```text
Chart handoff — <service>
[ ] ConfigMap <name> with observability.yml, mounted at /etc/observability/observability.yml
[ ] Env OBSERVABILITY_CONFIG=/etc/observability/observability.yml
[ ] Secret <name>/<key> mounted at /var/run/secrets/observability/<file> (yml: ${file:…})
[ ] Env OBS_SERVICE_NAME, OBS_SERVICE_NAMESPACE, OBS_SERVICE_VERSION, OBS_ENVIRONMENT
[ ] Env OBS_*_ENDPOINT overrides for this environment, if any
[ ] Downward-API env K8S_POD_NAME, K8S_POD_UID, K8S_NAMESPACE_NAME, K8S_NODE_NAME
[ ] Env K8S_CLUSTER_NAME from a cluster-level value, only where several clusters send to
    one backend
[ ] Pod annotations resource.opentelemetry.io/: service.name, service.namespace,
    service.version, deployment.environment.name, and each extra resource attribute the yml sets
[ ] Port http-metrics <port> on the container and the Service
[ ] ServiceMonitor or PodMonitor scraping /metrics on http-metrics
[ ] Colour off: <variable or flag>
[ ] Ingress routes application paths only: no /metrics, no health paths
[ ] Termination grace period ≥ flush timeout + request draining
[ ] AI services: Secret with the Langfuse public and secret keys, mounted as files with no
    trailing newline; env OBS_LANGFUSE_URL; OBS_GENAI_CONTENT=full only in environments with
    synthetic data; egress to the Langfuse host
```

## Verify

- The rendered chart contains the mount, the env, the annotations, the port and the monitor.
- In the cluster, the identity checks pass: one resource on traces, scraped metrics,
  stdout logs and profiles ([acceptance.md](acceptance.md),
  [collector](collector/README.md)).

[Documentation index](README.md)
