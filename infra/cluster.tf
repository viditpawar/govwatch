resource "kind_cluster" "this" {
  name            = var.cluster_name
  node_image      = var.node_image
  wait_for_ready  = true
  kubeconfig_path = pathexpand("~/.kube/config")

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"

      # grafana and prometheus are NodePort services; map them straight to localhost
      extra_port_mappings {
        container_port = 30300
        host_port      = var.host_ports.grafana
        listen_address = "127.0.0.1"
      }
      extra_port_mappings {
        container_port = 30090
        host_port      = var.host_ports.prometheus
        listen_address = "127.0.0.1"
      }
      # the review app has no authentication (decisions.md #46), so it's only ever
      # published on loopback
      extra_port_mappings {
        container_port = 30080
        host_port      = var.host_ports.review
        listen_address = "127.0.0.1"
      }
    }
  }
}

locals {
  repo_root = abspath("${path.module}/..")

  # anything that changes what ends up in the image
  image_sources = concat(
    ["Dockerfile", "pyproject.toml", "uv.lock", "README.md"],
    tolist(fileset(local.repo_root, "src/**")),
  )
  image_hash = sha1(join("", [for f in local.image_sources : filesha1("${local.repo_root}/${f}")]))

  # with a local build the tag carries the content hash, so a code change rolls the
  # deployment instead of kubernetes holding on to a stale image
  image_tag = var.image.build_local ? "${var.image.tag}-${substr(local.image_hash, 0, 12)}" : var.image.tag
}

# build the image and side-load it into the kind node. `&&` works in both cmd and sh,
# so this runs unchanged on windows and linux/macos
resource "terraform_data" "image" {
  count = var.image.build_local ? 1 : 0

  triggers_replace = [local.image_hash, kind_cluster.this.id]

  provisioner "local-exec" {
    working_dir = local.repo_root
    command     = "docker build -t ${var.image.repository}:${local.image_tag} . && kind load docker-image ${var.image.repository}:${local.image_tag} --name ${kind_cluster.this.name}"
  }
}
