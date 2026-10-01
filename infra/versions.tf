terraform {
  required_version = ">= 1.9"

  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.11"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # local state on purpose: this stands up a throwaway local cluster, so there's
  # nothing to share. for a real environment this would be an s3/azurerm/gcs backend
  # with locking
}
