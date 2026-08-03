variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  type = string
}

variable "command" {
  type = list(string)
}

variable "args" {
  type = list(string)
}

variable "image" {
  type = string
}

variable "database_instance_connection_name" {
  type = string
}

variable "database_instance_private_ip" {
  type = string
}

variable "database_name" {
  type = string
}

variable "database_password_secret_id" {
  type      = string
  sensitive = true
}

variable "network_id" {
  type = string
}

variable "subnetwork_id" {
  type = string
}

variable "cloudrun_service_account_email" {
  type = string
}

# "laravel" = UI migrate job (DB_USERNAME, /var/www/html/certs)
# "python"  = API alembic job (DB_USER, /vol_mt/certs, ENV)
variable "runtime" {
  type    = string
  default = "laravel"

  validation {
    condition     = contains(["laravel", "python"], var.runtime)
    error_message = "runtime must be laravel or python."
  }
}

# DDL migrations should not auto-retry (MySQL commits each statement).
variable "max_retries" {
  type    = number
  default = 0
}
