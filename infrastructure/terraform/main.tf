terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "Aspyre"
      Environment = var.environment
      ManagedBy   = "Ferrox-Infrastructure"
      CostCenter  = "Fintech-Integrations"
    }
  }
}

# 1. KMS Customer Managed Key for Envelope Encryption (AES-256-GCM at Rest)
resource "aws_kms_key" "aspyre_vault_cmk" {
  description             = "CMK for Aspyre-Ferrox OAuth2 token and accounting secret encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "aspyre_vault_cmk_alias" {
  name          = "alias/aspyre-ferrox-vault-${var.environment}"
  target_key_id = aws_kms_key.aspyre_vault_cmk.key_id
}

# 2. VPC & Networking (3 Availability Zones in eu-west-1)
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "aspyre-ferrox-vpc-${var.environment}"
  cidr = "10.100.0.0/16"

  azs             = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
  private_subnets = ["10.100.1.0/24", "10.100.2.0/24", "10.100.3.0/24"]
  public_subnets  = ["10.100.101.0/24", "10.100.102.0/24", "10.100.103.0/24"]

  enable_nat_gateway     = true
  single_nat_gateway     = var.environment == "prod" ? false : true
  enable_dns_hostnames   = true
  enable_dns_support     = true
}

# 3. ElastiCache Redis Cluster for Distributed Leases & DRR Fair-Queue
resource "aws_elasticache_subnet_group" "redis_subnets" {
  name       = "aspyre-ferrox-redis-subnet-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis_sg" {
  name        = "aspyre-ferrox-redis-sg-${var.environment}"
  description = "Allows Redis access from ECS tasks"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "ferrox_redis" {
  replication_group_id       = "aspyre-redis-${var.environment}"
  description                = "Redis cluster for ferrox-py concurrency locks, DRR queue, and audit cache"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.environment == "prod" ? 3 : 1
  parameter_group_name       = "default.redis7"
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis_subnets.name
  security_group_ids         = [aws_security_group.redis_sg.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  automatic_failover_enabled = var.environment == "prod" ? true : false
  multi_az_enabled           = var.environment == "prod" ? true : false
}

# 4. Amazon Aurora PostgreSQL Serverless v2 for Ledger State & ID Translations
resource "aws_rds_subnet_group" "db_subnets" {
  name       = "aspyre-ferrox-db-subnets-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db_sg" {
  name        = "aspyre-ferrox-db-sg-${var.environment}"
  description = "Access to Aurora from ECS"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_rds_cluster" "aurora_postgres" {
  cluster_identifier      = "aspyre-ferrox-aurora-${var.environment}"
  engine                  = "aurora-postgresql"
  engine_mode             = "provisioned"
  engine_version          = "16.1"
  database_name           = "aspyre_ledger"
  master_username         = "ferrox_admin"
  manage_master_user_password = true
  db_subnet_group_name    = aws_rds_subnet_group.db_subnets.name
  vpc_security_group_ids  = [aws_security_group.db_sg.id]
  kms_key_id             = aws_kms_key.aspyre_vault_cmk.arn
  storage_encrypted       = true
  deletion_protection     = var.environment == "prod" ? true : false

  serverlessv2_scaling_configuration {
    max_capacity = var.aurora_max_acu
    min_capacity = var.aurora_min_acu
  }
}

resource "aws_rds_cluster_instance" "aurora_instances" {
  count               = var.environment == "prod" ? 2 : 1
  identifier          = "aspyre-ferrox-db-${count.index}"
  cluster_identifier  = aws_rds_cluster.aurora_postgres.id
  instance_class      = "db.serverless"
  engine              = aws_rds_cluster.aurora_postgres.engine
  engine_version      = aws_rds_cluster.aurora_postgres.engine_version
}

# 5. ECS Fargate Cluster & Task Definition
resource "aws_ecs_cluster" "ferrox_cluster" {
  name = "aspyre-ferrox-cluster-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_security_group" "ecs_sg" {
  name        = "aspyre-ferrox-ecs-sg-${var.environment}"
  description = "Security group for Ferrox ECS workers"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ECS IAM Roles
resource "aws_iam_role" "ecs_task_execution_role" {
  name = "aspyre-ferrox-ecs-exec-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_standard" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_policy" "kms_decrypt_policy" {
  name        = "aspyre-ferrox-kms-access-${var.environment}"
  description = "Allows ECS tasks to encrypt/decrypt secrets using Aspyre CMK"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = [
        "kms:Encrypt",
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ]
      Effect   = "Allow"
      Resource = aws_kms_key.aspyre_vault_cmk.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_kms_attach" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = aws_iam_policy.kms_decrypt_policy.arn
}

# ECS Worker Task Definition (Auto-Scaled Async Single-Flight Workers)
resource "aws_ecs_task_definition" "ferrox_worker" {
  family                   = "aspyre-ferrox-worker-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"  # 1 vCPU
  memory                   = "2048"  # 2 GB RAM
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_execution_role.arn

  container_definitions = jsonencode([{
    name      = "ferrox-worker"
    image     = "${var.ecr_repository_url}:latest"
    essential = true
    environment = [
      { name = "ENVIRONMENT", value = var.environment },
      { name = "REDIS_HOST", value = aws_elasticache_replication_group.ferrox_redis.primary_endpoint_address },
      { name = "KMS_KEY_ARN", value = aws_kms_key.aspyre_vault_cmk.arn },
      { name = "LOG_LEVEL", value = "INFO" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/aspyre-ferrox-worker-${var.environment}"
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}
