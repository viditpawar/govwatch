variable "cluster_name" {
  description = "Name of the kind cluster."
  type        = string
  default     = "govwatch"
}

variable "node_image" {
  description = "kindest/node image, which pins the kubernetes version."
  type        = string
  default     = "kindest/node:v1.36.1"
}

variable "congress_api_key" {
  description = "api.data.gov key for congress.gov. Pass via TF_VAR_congress_api_key or a tfvars file."
  type        = string
  sensitive   = true
}

variable "regulations_api_key" {
  description = "api.data.gov key for regulations.gov. Defaults to the congress key (one key works for both)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "image" {
  description = <<-EOT
    govwatch container image. With build_local = true, terraform builds the image from
    this repo and loads it into the kind nodes, so no registry is needed.
  EOT
  type = object({
    repository  = string
    tag         = string
    build_local = bool
  })
  default = {
    repository  = "govwatch"
    tag         = "local"
    build_local = true
  }
}

variable "backfill_days" {
  description = "How far back the first ingest goes."
  type        = number
  default     = 7
}

variable "poll_interval_seconds" {
  description = "Seconds between ingest cycles."
  type        = number
  default     = 900

  validation {
    condition     = var.poll_interval_seconds >= 60
    error_message = "poll_interval_seconds must be at least 60 to stay well inside the api.data.gov rate limits."
  }
}

variable "host_ports" {
  description = "Ports on localhost (loopback only) that Grafana, Prometheus and the review app are published on."
  type = object({
    grafana    = number
    prometheus = number
    review     = number
  })
  default = {
    grafana    = 30300
    prometheus = 30090
    review     = 30080
  }
}

variable "agent_enabled" {
  description = "Run the summarization agent in the worker. Needs Ollama reachable from the pods."
  type        = bool
  default     = true
}

variable "ollama_url" {
  description = "Where the pods reach Ollama. On Docker Desktop, kind pods reach the host as host.docker.internal."
  type        = string
  default     = "http://host.docker.internal:11434"
}

variable "chart_versions" {
  description = "Pinned versions of the third-party charts."
  type = object({
    cnpg                  = string
    kube_prometheus_stack = string
  })
  default = {
    cnpg                  = "0.29.1"
    kube_prometheus_stack = "91.8.2"
  }
}
