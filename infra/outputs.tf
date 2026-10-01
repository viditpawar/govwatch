output "kube_context" {
  description = "kubectl context for the cluster."
  value       = "kind-${kind_cluster.this.name}"
}

output "grafana_url" {
  description = "Grafana (anonymous read-only access is on)."
  value       = "http://localhost:${var.host_ports.grafana}"
}

output "prometheus_url" {
  value = "http://localhost:${var.host_ports.prometheus}"
}

output "review_url" {
  description = "Human review queue for agent summaries."
  value       = "http://localhost:${var.host_ports.review}"
}

output "grafana_admin_password" {
  description = "Grafana admin password (user: admin). Read with: terraform output -raw grafana_admin_password"
  value       = random_password.grafana_admin.result
  sensitive   = true
}

output "image" {
  description = "Image the worker is running."
  value       = "${var.image.repository}:${local.image_tag}"
}
