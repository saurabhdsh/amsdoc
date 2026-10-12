#!/bin/bash
# Run this on the SEAL EC2 host (52.0.130.62). Bedrock uses the instance role.
set -euo pipefail

echo "=== AMSDoC — EC2 deploy (port 7000, Bedrock) ==="

if ! command -v docker &>/dev/null; then
  echo "Installing Docker..."
  sudo yum update -y
  sudo yum install -y docker git
  sudo systemctl start docker
  sudo systemctl enable docker
  sudo usermod -aG docker ec2-user
  echo "Docker installed. Run: newgrp docker && ./deploy.sh"
  exit 0
fi

COMPOSE=(docker compose -f docker-compose.ec2.yml --project-name amsdoc)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose -f docker-compose.ec2.yml -p amsdoc)
fi

echo "Building and starting containers..."
"${COMPOSE[@]}" up -d --build
"${COMPOSE[@]}" ps
echo ""
echo "AMSDoC: http://52.0.130.62:7000"
echo "Login:  Yugal / admin123"
echo "Bedrock: us-east-1 / us.anthropic.claude-sonnet-4-5-20250929-v1:0"
