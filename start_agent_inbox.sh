#!/bin/bash
# Start the Agent Inbox backend and frontend

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}     Agent Inbox - Local Email Agent    ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if we're in the right directory
if [ ! -f "email_agent/api.py" ]; then
    echo -e "${YELLOW}Error: Please run this script from the project root directory${NC}"
    exit 1
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo -e "${GREEN}Activating virtual environment...${NC}"
    source venv/bin/activate
fi

# Install Python dependencies if needed
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}Installing Python dependencies...${NC}"
    pip install -r email_agent/requirements.txt
fi

# Check if Docker is running for PostgreSQL
if ! docker ps | grep -q email-postgres; then
    echo -e "${YELLOW}Starting PostgreSQL container...${NC}"
    docker-compose up -d email-postgres || echo "PostgreSQL may already be running or not needed"
fi

# Start the backend API
echo ""
echo -e "${GREEN}Starting Backend API on http://localhost:8000${NC}"
echo ""

# Run in background
python -m uvicorn email_agent.api:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Wait for backend to be ready
echo "Waiting for backend to start..."
sleep 3

# Check if backend is running
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${YELLOW}Warning: Backend may still be starting...${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Agent Inbox is running!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Backend API:  ${BLUE}http://localhost:8000${NC}"
echo -e "API Docs:     ${BLUE}http://localhost:8000/docs${NC}"
echo ""
echo -e "${YELLOW}To start the frontend:${NC}"
echo "  cd email_agent/frontend"
echo "  npm install"
echo "  npm run dev"
echo ""
echo -e "Frontend:     ${BLUE}http://localhost:3000${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the backend${NC}"
echo ""

# Wait for the backend process
wait $BACKEND_PID
