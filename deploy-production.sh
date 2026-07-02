#!/bin/bash

# Production Deployment Script for Viin
# This script handles deployment with production best practices

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Viin Production Deployment ===${NC}\n"

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker is not installed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker${NC}"

if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}✗ Docker Compose is not installed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠ .env file not found${NC}"
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${YELLOW}⚠ Please update .env with your production values before running this script again${NC}"
    exit 1
fi
echo -e "${GREEN}✓ .env file found${NC}"

# Verify production settings
echo ""
echo "Verifying production settings..."

# Check DEBUG is false
DEBUG=$(grep "^DEBUG=" .env | cut -d'=' -f2)
if [ "$DEBUG" != "false" ]; then
    echo -e "${RED}✗ DEBUG must be 'false' in production${NC}"
    exit 1
fi
echo -e "${GREEN}✓ DEBUG is disabled${NC}"

# Check SECRET_KEY is not default
SECRET_KEY=$(grep "^SECRET_KEY=" .env | cut -d'=' -f2)
if [ "$SECRET_KEY" = "green-secret-keeps-gamma" ]; then
    echo -e "${YELLOW}⚠ WARNING: Using default SECRET_KEY. Please set a strong random value in .env${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo -e "${GREEN}✓ SECRET_KEY configured${NC}"

# Stop existing services
echo ""
echo "Stopping existing services..."
docker-compose down || true
sleep 2

# Build images
echo ""
echo "Building Docker images..."
docker-compose build --no-cache

# Create volumes if they don't exist
echo ""
echo "Ensuring volumes exist..."
docker volume create viin_postgres_data || true
docker volume create viin_redis_data || true
echo -e "${GREEN}✓ Volumes ready${NC}"

# Start services
echo ""
echo "Starting services..."
docker-compose up -d

# Wait for services to be healthy
echo ""
echo "Waiting for services to be healthy..."

echo -n "Waiting for PostgreSQL..."
TRIES=0
while [ $TRIES -lt 30 ]; do
    if docker exec viin_db pg_isready -U viinadmin -d viin &> /dev/null; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 2
    TRIES=$((TRIES + 1))
done

if [ $TRIES -eq 30 ]; then
    echo -e " ${RED}✗ Timeout${NC}"
    echo "PostgreSQL failed to start. Check logs with: docker-compose logs db"
    exit 1
fi

echo -n "Waiting for Redis..."
TRIES=0
while [ $TRIES -lt 15 ]; do
    if docker exec viin_redis redis-cli -a "$(grep "^REDIS_PASSWORD=" .env | cut -d'=' -f2)" ping &> /dev/null; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 1
    TRIES=$((TRIES + 1))
done

if [ $TRIES -eq 15 ]; then
    echo -e " ${RED}✗ Timeout${NC}"
    echo "Redis failed to start. Check logs with: docker-compose logs redis"
    exit 1
fi

echo -n "Waiting for Backend..."
TRIES=0
while [ $TRIES -lt 60 ]; do
    if docker exec viin_backend curl -s http://localhost:8000/health &> /dev/null; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 1
    TRIES=$((TRIES + 1))
done

if [ $TRIES -eq 60 ]; then
    echo -e " ${YELLOW}⚠ Backend startup taking longer than expected${NC}"
    echo "Check logs with: docker-compose logs backend"
fi

# Final status check
echo ""
echo "Final status:"
docker-compose ps

# Show endpoints
echo ""
echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Service endpoints:"
echo "  Backend API: http://localhost:8000"
echo "  PostgreSQL: localhost:5432 (user: viinadmin)"
echo "  Redis: localhost:6379"
echo ""
echo "Useful commands:"
echo "  View logs:        docker-compose logs -f"
echo "  Database shell:   docker exec -it viin_db psql -U viinadmin -d viin"
echo "  Redis CLI:        docker exec -it viin_redis redis-cli -a \$(grep REDIS_PASSWORD .env | cut -d= -f2)"
echo "  Stop services:    docker-compose down"
echo ""
echo -e "${GREEN}✓ Deployment successful!${NC}"
