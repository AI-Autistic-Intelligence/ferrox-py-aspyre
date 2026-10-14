variable "aws_region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS Region for deployment (eu-west-1 close to Exact Online & Twinfield AMS clusters)"
}

variable "environment" {
  type        = string
  default     = "prod"
  description = "Deployment environment (dev, staging, prod)"
}

variable "redis_node_type" {
  type        = string
  default     = "cache.m7g.large"
  description = "Graviton3 node type for Redis ElastiCache cluster"
}

variable "aurora_min_acu" {
  type        = number
  default     = 0.5
  description = "Minimum Aurora Serverless v2 ACU"
}

variable "aurora_max_acu" {
  type        = number
  default     = 16.0
  description = "Maximum Aurora Serverless v2 ACU"
}

variable "ecr_repository_url" {
  type        = string
  default     = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/aspyre/ferrox-py-aspyre"
  description = "ECR image repository URL"
}
