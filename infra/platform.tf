# cluster-wide pieces govwatch depends on: the postgres operator and the monitoring stack

resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name = "monitoring"
  }
}

resource "helm_release" "cnpg" {
  name             = "cnpg"
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = var.chart_versions.cnpg
  namespace        = "cnpg-system"
  create_namespace = true
  wait             = true
  timeout          = 600
}

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

# grafana's login to postgres. it only gets the govwatch_readonly role (reporting views)
resource "random_password" "grafana_reader" {
  length  = 32
  special = false
}

resource "helm_release" "kube_prometheus_stack" {
  name       = "kps"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.chart_versions.kube_prometheus_stack
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  wait       = true
  timeout    = 900

  values = [yamlencode({
    # kind runs these control plane components bound to localhost, so scraping them
    # just produces permanently-down targets and noisy alerts
    kubeEtcd              = { enabled = false }
    kubeControllerManager = { enabled = false }
    kubeScheduler         = { enabled = false }
    kubeProxy             = { enabled = false }

    alertmanager = {
      alertmanagerSpec = {
        resources = { requests = { cpu = "10m", memory = "32Mi" }, limits = { memory = "64Mi" } }
      }
    }

    prometheus = {
      service = { type = "NodePort", nodePort = 30090 }
      prometheusSpec = {
        retention = "7d"
        resources = { requests = { cpu = "100m", memory = "512Mi" }, limits = { memory = "1Gi" } }
        # pick up ServiceMonitors and PrometheusRules from every release, not just this one
        serviceMonitorSelectorNilUsesHelmValues = false
        podMonitorSelectorNilUsesHelmValues     = false
        ruleSelectorNilUsesHelmValues           = false
      }
    }

    grafana = {
      adminPassword = random_password.grafana_admin.result
      service       = { type = "NodePort", nodePort = 30300 }
      "grafana.ini" = {
        "auth.anonymous" = { enabled = true, org_role = "Viewer" }
        dashboards       = { default_home_dashboard_path = "/tmp/dashboards/govwatch/govwatch.json" }
      }
      sidecar = {
        dashboards = {
          enabled          = true
          searchNamespace  = "ALL"
          folderAnnotation = "grafana_folder"
          provider         = { foldersFromFilesStructure = true }
        }
      }
      # same uid as the compose stack, so one dashboard json works in both
      additionalDataSources = [{
        name     = "govwatch-db"
        uid      = "govwatch-db"
        type     = "grafana-postgresql-datasource"
        url      = "govwatch-db-rw.govwatch.svc:5432"
        user     = "grafana_reader"
        editable = false
        jsonData = { database = "govwatch", sslmode = "require", postgresVersion = 1700 }
        secureJsonData = {
          password = random_password.grafana_reader.result
        }
      }]
    }
  })]
}
