output "vpc_id" {
  value       = module.vpc.vpc_id
  description = "VPC ID"
}

output "redis_primary_endpoint" {
  value       = aws_elasticache_replication_group.ferrox_redis.primary_endpoint_address
  description = "ElastiCache Redis primary endpoint for distributed locks and fair queues"
}

output "aurora_cluster_endpoint" {
  value       = aws_rds_cluster.aurora_postgres.endpoint
  description = "Aurora PostgreSQL cluster endpoint"
}

output "kms_cmk_arn" {
  value       = aws_kms_key.aspyre_vault_cmk.arn
  description = "KMS CMK ARN for secret encryption at rest"
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.ferrox_cluster.name
  description = "ECS Fargate Cluster Name"
}
