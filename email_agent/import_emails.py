"""Bulk import emails from Outlook into storage.

Usage:
    python -m msft_email_agent.import_emails --months 1
    python -m msft_email_agent.import_emails --months 3 --batch-size 100
"""

import argparse
import asyncio
import logging
import os

# Import the storage from the main module
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from email_agent.email_storage import EmailStorage

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def normalize_email(email_item: Any) -> Dict[str, Any] | None:
    """Normalize a single email item from Microsoft Graph format.
    
    Args:
        email_item: Raw email data from Microsoft Graph API
        
    Returns:
        Normalized email dict or None if invalid
    """
    try:
        # Handle case where email_item might be a string or None
        if not email_item or isinstance(email_item, str):
            logger.warning(f"Skipping invalid email item: {type(email_item)}")
            return None
            
        if not isinstance(email_item, dict):
            logger.warning(f"Email item is not a dict: {type(email_item)}")
            return None
        
        # Extract fields from Microsoft Graph API format with safe gets
        from_field = email_item.get("from") or {}
        email_address = from_field.get("emailAddress") or {} if isinstance(from_field, dict) else {}
        
        to_recipients = email_item.get("toRecipients") or []
        to_addresses = []
        if isinstance(to_recipients, list):
            for recip in to_recipients:
                if isinstance(recip, dict):
                    recip_email = recip.get("emailAddress") or {}
                    if isinstance(recip_email, dict):
                        addr = recip_email.get("address")
                        if addr:
                            to_addresses.append(addr)
        
        body_content = ""
        if email_item.get("bodyPreview"):
            body_content = email_item.get("bodyPreview")
        elif email_item.get("body") and isinstance(email_item.get("body"), dict):
            body_content = email_item.get("body", {}).get("content", "")
        
        email_dict = {
            "author": email_address.get("address", "unknown") if isinstance(email_address, dict) else "unknown",
            "to": ", ".join(to_addresses),
            "subject": email_item.get("subject", "(No subject)"),
            "body": body_content,
            "received_at": email_item.get("receivedDateTime", datetime.now(UTC).isoformat()),
            "message_id": email_item.get("id", ""),
        }
        
        return email_dict
        
    except Exception as e:
        logger.warning(f"Failed to parse email: {e}")
        logger.debug(f"Email item data: {email_item}")
        return None


async def process_emails_async(email_list: List[Any]) -> List[Dict[str, Any]]:
    """Process a list of emails asynchronously.
    
    Args:
        email_list: List of raw email data from Microsoft Graph API
        
    Returns:
        List of normalized email dicts
    """
    # Create tasks for all emails
    tasks = [normalize_email(email_item) for email_item in email_list]
    
    # Process all emails concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out None values and exceptions
    emails = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Exception during email processing: {result}")
        elif result is not None:
            emails.append(result)
    
    logger.info(f"✓ Processed {len(emails)} valid emails out of {len(email_list)}")
    return emails


async def fetch_emails_from_outlook(months: int = 1) -> List[Dict[str, Any]]:
    """Fetch emails from Outlook via MCP tools.
    
    Args:
        months: Number of months back to fetch
        
    Returns:
        List of email dicts with normalized fields
    """
    logger.info(f"Connecting to Microsoft 365 via MCP...")
    
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@softeria/ms-365-mcp-server", "--org-mode"],
    )
    
    emails = []
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            mcp_tools = await load_mcp_tools(session)
            
            # Find the list-mail-messages tool
            list_mail_tool = None
            get_mail_tool = None
            
            for tool in mcp_tools:
                if tool.name == "list-mail-messages":
                    list_mail_tool = tool
                elif tool.name == "get-mail-message":
                    get_mail_tool = tool
            
            if not list_mail_tool:
                raise ValueError("list-mail-messages tool not found in MCP tools")
            
            logger.info("✓ Connected to Microsoft 365")
            
            # Calculate date range
            end_date = datetime.now(UTC)
            start_date = end_date - timedelta(days=months * 30)
            
            logger.info(f"Fetching emails from {start_date.date()} to {end_date.date()}...")
            
            # Debug: Let's see what parameters the tool accepts
            logger.info(f"Tool schema: {list_mail_tool.description}")
            
            # Fetch email list
            try:
                # Try different approaches to get more emails
                
                # Method 1: Basic call without filters first
                logger.info("Trying basic call without filters...")
                result = await list_mail_tool.coroutine(top=999)
                
                # If that doesn't work, try with proper Microsoft Graph date format
                if not result or (isinstance(result, str) and "[]" in result):
                    logger.info("Trying with date filter...")
                    # Microsoft Graph expects this format: 2023-01-01T00:00:00Z
                    start_date_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                    result = await list_mail_tool.coroutine(
                        top=999,
                        filter=f"receivedDateTime ge {start_date_str}"
                    )
                
                # If still no results, try different parameter names
                if not result or (isinstance(result, str) and "[]" in result):
                    logger.info("Trying alternative parameter format...")
                    result = await list_mail_tool.coroutine(
                        count=999,
                        date_filter=start_date.isoformat()
                    )

                
                # Debug: Show raw result
                logger.info(f"Raw result type: {type(result)}")
                logger.info(f"Raw result length: {len(str(result)) if result else 0}")
                if result:
                    logger.info(f"Raw result preview: {str(result)[:200]}...")
                
                # Handle tuple results from MCP tools
                if isinstance(result, tuple) and len(result) > 0:
                    result = result[0]  # Take first element of tuple
                
                # Parse the result - it's typically JSON string
                import json
                if isinstance(result, str):
                    try:
                        result_data = json.loads(result)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON result: {e}")
                        logger.error(f"Raw result: {result}")
                        return []
                else:
                    result_data = result
                
                # Debug: Show parsed structure
                logger.info(f"Parsed result type: {type(result_data)}")
                if isinstance(result_data, dict):
                    logger.info(f"Result keys: {list(result_data.keys())}")
                
                # Extract email list - try different possible structures
                email_list = []
                if isinstance(result_data, dict):
                    # Microsoft Graph format
                    email_list = result_data.get("value", [])
                    # Alternative formats
                    if not email_list:
                        email_list = result_data.get("emails", [])
                    if not email_list:
                        email_list = result_data.get("messages", [])
                elif isinstance(result_data, list):
                    email_list = result_data
                
                logger.info(f"✓ Found {len(email_list)} emails")
                
                # Debug: Show first email structure if available
                if email_list and len(email_list) > 0:
                    logger.info(f"First email keys: {list(email_list[0].keys()) if isinstance(email_list[0], dict) else 'Not a dict'}")
                    logger.info(f"First email sample: {str(email_list[0])[:200] if email_list[0] else 'Empty'}...")
                
                # Normalize email data asynchronously
                logger.info("Processing emails asynchronously...")
                emails = await process_emails_async(email_list)
                
            except Exception as e:
                logger.error(f"Failed to fetch emails: {e}")
                raise
    
    return emails


async def main(months: int = 1, batch_size: int = 50, storage_mode: str = None):
    """Main import workflow."""
    logger.info("=" * 60)
    logger.info("Email Bulk Import Tool")
    logger.info("=" * 60)
    
    # Detect or ask for storage mode
    if storage_mode is None:
        storage_mode = os.environ.get('STORAGE_MODE', '').lower()
    
    if not storage_mode or storage_mode not in ['local', 'cloud']:
        logger.info("\n📦 Storage Options:")
        logger.info("   1. LOCAL  - Store emails locally (PostgreSQL in Docker)")
        logger.info("   2. CLOUD  - Store emails in Azure (Blob + PostgreSQL)")
        choice = input("\nChoose storage mode (local/cloud) [default: cloud]: ").lower().strip() or 'cloud'
        storage_mode = choice
    
    logger.info(f"\n✓ Using {storage_mode.upper()} storage mode")
    
    # Set environment variable for this session
    os.environ['STORAGE_MODE'] = storage_mode
    
    # Initialize storage with explicit storage mode
    try:
        storage = EmailStorage(storage_type=storage_mode)
    except ConnectionError as e:
        logger.error(f"\n❌ Storage initialization failed: {e}")
        if storage_mode == 'local':
            logger.error("\n📋 To set up local storage:")
            logger.error("   1. Make sure Docker is installed and running")
            logger.error("   2. Start local database: docker-compose up -d")
            logger.error("   3. Verify it's running: docker ps | grep postgres")
            logger.error("\n   Or switch to cloud storage: --storage cloud")
        return
    except Exception as e:
        logger.error(f"\n❌ Failed to initialize storage: {e}")
        if storage_mode == 'local':
            logger.error("\n   Run: docker-compose up -d")
        else:
            logger.error("\n   Check Azure credentials and connection settings in .env")
        return
    
    # Validate storage is properly configured
    if not storage.vector_store:
        logger.error(f"\n❌ Vector store failed to initialize for {storage_mode} mode!")
        if storage_mode == 'local':
            logger.error("   Make sure Docker PostgreSQL is running: docker-compose up -d")
            logger.error("   Check connection: docker ps | grep postgres")
        else:
            logger.error("   Check Azure PostgreSQL connection details in .env")
        
        response = input("\nContinue without vector search? (y/n): ")
        if response.lower() != 'y':
            logger.info("Import cancelled")
            return
    
    # Fetch emails from Outlook asynchronously
    logger.info("Starting asynchronous email fetch...")
    emails = await fetch_emails_from_outlook(months)
    
    if not emails:
        logger.warning("No emails found to import")
        return
    
    logger.info(f"\n📧 Preparing to import {len(emails)} emails...")
    logger.info(f"   Batch size: {batch_size}")
    logger.info(f"   Estimated time: ~{(len(emails) * 2) // 60} minutes\n")
    
    # Confirm with user
    print(f"\nReady to import {len(emails)} emails from the past {months} month(s).")
    response = input("Continue? (y/n): ")
    
    if response.lower() != 'y':
        logger.info("Import cancelled by user")
        return
    
    # Progress callback
    def progress(current, total):
        pct = (current / total) * 100
        logger.info(f"Progress: {current}/{total} ({pct:.1f}%)")
    
    # Run bulk import with async processing
    logger.info("Starting asynchronous bulk import...")
    stats = await storage.bulk_import_emails(
        emails,
        batch_size=batch_size,
        progress_callback=progress
    )
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Import Complete!")
    logger.info("=" * 60)
    logger.info(f"✅ Successfully stored: {stats['stored']} emails")
    if stats['skipped'] > 0:
        logger.info(f"⏭️  Skipped (duplicates): {stats['skipped']} emails")
    if stats['failed'] > 0:
        logger.info(f"❌ Failed: {stats['failed']} emails")
    logger.info("=" * 60)
    
    # Test search
    logger.info("\n🔍 Testing search functionality...")
    test_results = await storage.search("meeting", top_k=3)
    logger.info(f"✓ Search working! Found {len(test_results)} results for 'meeting'")
    
    for i, result in enumerate(test_results, 1):
        logger.info(f"\n  {i}. From: {result['author']}")
        logger.info(f"     Subject: {result['subject']}")
        logger.info(f"     Snippet: {result['snippet'][:100]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import emails from Outlook into storage for semantic search"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=1,
        help="Number of months of email history to import (default: 1)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of emails to process per batch (default: 50)"
    )
    parser.add_argument(
        "--storage",
        type=str,
        choices=['local', 'cloud'],
        help="Storage mode: 'local' or 'cloud' (default: use STORAGE_MODE env var or prompt)"
    )
    
    args = parser.parse_args()
    
    asyncio.run(main(months=args.months, batch_size=args.batch_size, storage_mode=args.storage))
