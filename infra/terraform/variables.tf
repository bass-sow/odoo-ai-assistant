variable "name" {
  type    = string
  default = "odoo-ai-assistant"
}

variable "region" {
  type    = string
  default = "eu-west-3" # Paris
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "llm_provider" {
  type    = string
  default = "gemini"
}

variable "embed_provider" {
  type    = string
  default = "gemini"
}

variable "llm_api_key" {
  type      = string
  sensitive = true
}

variable "api_token" {
  description = "Shared token clients must send (X-API-Key header or ?key=) so nobody else spends your LLM quota"
  type        = string
  sensitive   = true
}

variable "alert_email" {
  description = "Email for the 1 USD budget alarm (empty = no budget)"
  type        = string
  default     = ""
}
