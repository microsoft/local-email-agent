# Microsoft 365 Email Agent

A complete email assistant built with LangGraph that integrates with Microsoft 365 (Outlook/Exchange) using the M365 MCP Server.

## Features

- **Email Triage**: Automatically categorizes emails (ignore, notify, respond)
- **Smart Responses**: Draft contextual email replies
- **Calendar Management**: Schedule meetings and check availability
- **Email History Search**: Semantic search across your email history using pgvector
- **Human-in-the-Loop (HITL)**: Review and approve actions before execution
- **Memory System**: Learns from your feedback to improve over time
- **Dual Storage**: Supports both local and Azure cloud storage

## Quick Start

### 1. Install Dependencies

The agent requires the following:
- Python 3.11+
- Node.js (for MCP server)
- PostgreSQL with pgvector (local or Azure)

### 2. Set Up Environment

Copy the appropriate `.env` template from the root directory:

```bash
# For local development
cp ../.env.local.example .env

# OR for Azure cloud
cp ../.env.cloud.example .env
```

Edit `.env` with your credentials.

### 3. Run the Agent

```bash
# From the project root
python -m msft_email_agent.m365_email_assistant_with_storage
```

## Project Structure

```
msft_email_agent/
├── m365_email_assistant_with_storage.py  # Main agent file
├── schemas.py                            # State and data models
├── prompts.py                            # System prompts and instructions
├── utils.py                              # Utility functions
├── configuration.py                      # Config management
├── local_email_storage/                  # Local file storage directory
└── tools/                                # Tool definitions
    ├── base.py                           # Tool management
    └── default/                          # Default tools
        ├── email_tools.py                # Email operations
        ├── calendar_tools.py             # Calendar operations
        └── prompt_templates.py           # Tool prompts
```

## Configuration

### Storage Modes

Set `STORAGE_MODE` in your `.env`:

- `local`: PostgreSQL (localhost) + filesystem storage
- `cloud`: Azure PostgreSQL + Azure Blob Storage

### Memory System

The agent uses three memory namespaces to learn:

1. **triage_preferences**: Which emails to respond to
2. **response_preferences**: How to write responses
3. **cal_preferences**: Calendar scheduling preferences

Memory persists across sessions using LangGraph's store and checkpointer.

### HITL Configuration

Tools requiring human approval:
- `send-mail`: Review emails before sending
- `create-calendar-event`: Review meetings before scheduling
- `Question`: Agent asks you for clarification

## Environment Variables

### Required for All Modes

```bash
AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/openai/v1/
```

### Local Storage Mode

```bash
STORAGE_MODE=local
LOCAL_PGHOST=localhost
LOCAL_PGDATABASE=emaildb
LOCAL_PGPORT=5432
LOCAL_PGUSER=postgres
LOCAL_PGPASSWORD=your_password
LOCAL_BLOB_PATH=./local_email_storage  # Optional
```

### Cloud Storage Mode

```bash
STORAGE_MODE=cloud
AZURE_STORAGE_ACCOUNT_URL=https://your-storage.blob.core.windows.net
AZURE_STORAGE_CONTAINER_NAME=emails
AZURE_PGHOST=your-postgres.postgres.database.azure.com
AZURE_PGDATABASE=emaildb
AZURE_PGUSER=your_user
AZURE_PGPASSWORD=your_password
AZURE_PGPORT=5432
```

## Usage Examples

### Process an Email

```python
result = await graph.ainvoke({
    "email_input": {
        "author": "colleague@company.com",
        "to": "you@company.com",
        "subject": "Meeting Request",
        "email_thread": "Can we meet Tuesday at 2pm?"
    }
}, config={"configurable": {"thread_id": "user_1"}})
```

### Ask About Emails

```python
result = await graph.ainvoke({
    "question": "What time is my meeting on November 4th?"
}, config={"configurable": {"thread_id": "user_1"}})
```

## Architecture

The agent uses LangGraph's ToolNode architecture with the following nodes:

1. **Triage**: Classifies emails (ignore/notify/respond) or routes questions
2. **Supervisor**: Coordinates tools and makes decisions
3. **Tools**: Executes regular tools (search, authentication)
4. **Interrupt Handler**: Manages HITL for sensitive actions

## Development

### Adding New Tools

1. Define tools in `tools/default/email_tools.py` or `calendar_tools.py`
2. Add to `tools/base.py` exports
3. Include in supervisor_tools list in main file

### Customizing Prompts

Edit `prompts.py` to customize:
- Triage criteria
- Response style
- Calendar preferences
- Background information

## Troubleshooting

### Database Connection Issues

**Local mode:**
```bash
# Check if PostgreSQL is running
docker ps | grep postgres

# Start database
docker-compose up -d
```

**Cloud mode:**
- Verify Azure credentials with `az login`
- Check firewall rules in Azure Portal
- Ensure pgvector extension is enabled

### MCP Server Issues

The M365 MCP server requires authentication:
```bash
# The agent will prompt for login on first use
# Follow the browser authentication flow
```

## License

See the main repository LICENSE file.
