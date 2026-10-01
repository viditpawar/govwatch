resource "kubernetes_namespace_v1" "govwatch" {
  metadata {
    name = "govwatch"
  }
}

resource "kubernetes_secret_v1" "api_keys" {
  metadata {
    name      = "govwatch-api-keys"
    namespace = kubernetes_namespace_v1.govwatch.metadata[0].name
  }
  data = {
    congress-api-key    = var.congress_api_key
    regulations-api-key = coalesce(var.regulations_api_key, var.congress_api_key)
  }
}

# cloudnative-pg reads this to set grafana_reader's password (a managed role in the chart)
resource "kubernetes_secret_v1" "grafana_reader" {
  metadata {
    name      = "grafana-reader"
    namespace = kubernetes_namespace_v1.govwatch.metadata[0].name
    labels = {
      # lets cnpg notice password changes
      "cnpg.io/reload" = "true"
    }
  }
  type = "kubernetes.io/basic-auth"
  data = {
    username = "grafana_reader"
    password = random_password.grafana_reader.result
  }
}

resource "helm_release" "govwatch" {
  name      = "govwatch"
  chart     = "${path.module}/../charts/govwatch"
  namespace = kubernetes_namespace_v1.govwatch.metadata[0].name
  wait      = true
  timeout   = 600

  values = [yamlencode({
    image = {
      repository = var.image.repository
      tag        = local.image_tag
      # side-loaded images aren't in any registry
      pullPolicy = var.image.build_local ? "Never" : "IfNotPresent"
    }
    config = {
      backfillDays        = var.backfill_days
      pollIntervalSeconds = var.poll_interval_seconds
    }
    agent = {
      enabled   = var.agent_enabled
      ollamaUrl = var.ollama_url
    }
    review = {
      enabled = true
      service = { type = "NodePort", nodePort = 30080 }
    }
    apiKeys = {
      existingSecret = kubernetes_secret_v1.api_keys.metadata[0].name
    }
    database = {
      cnpg = {
        enabled       = true
        grafanaReader = { enabled = true, passwordSecret = kubernetes_secret_v1.grafana_reader.metadata[0].name }
      }
    }
    networkPolicy = {
      metricsNamespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    }
    monitoring = {
      serviceMonitor = { enabled = true }
      # the same files the compose stack mounts, so the two can't drift
      prometheusRule = {
        enabled = true
        rules   = file("${path.module}/../observability/prometheus/rules/govwatch.rules.yml")
      }
      dashboard = {
        enabled = true
        json    = file("${path.module}/../observability/grafana/dashboards/govwatch.json")
      }
    }
  })]

  # the chart creates cnpg Cluster and prometheus-operator resources, so both
  # operators (and their CRDs) have to exist first
  depends_on = [
    helm_release.cnpg,
    helm_release.kube_prometheus_stack,
    terraform_data.image,
  ]
}
