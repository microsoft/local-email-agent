"""Email storage module with support for both local and cloud backends.

This module provides:
- Local storage: PostgreSQL + filesystem
- Cloud storage: Azure PostgreSQL + Azure Blob Storage
- Vector search using pgvector extension
- Bulk import capabilities
"""

import asyncio
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.storage.blob import BlobServiceClient
from langchain_azure_postgresql import (
    AzurePGConnectionPool,
    AzurePGVectorStore,
    BasicAuth,
    ConnectionInfo,
    Extension,
    SSLMode,
    create_extensions,
)
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def configure_connection(conn: Connection) -> None:
    """Configure the PostgreSQL connection (as received from the pool)."""
    conn.autocommit = True
    conn.row_factory = dict_row
    # Extension is already enabled via azure.extensions, just register the vector type
    register_vector(conn)


class EmailStorage:
    """Email storage with support for both local and cloud backends."""
    
    def __init__(self, storage_type: str = None):
        """Initialize storage.
        
        Args:
            storage_type: 'local' or 'cloud'. If None, reads from STORAGE_MODE env var (default: 'local')
        """
        self.storage_type = storage_type or os.environ.get("STORAGE_MODE", "local")
        logger.info(f"🔧 Initializing {self.storage_type.upper()} storage...")
        
        # Lock for vector store operations (not thread-safe)
        self._vector_lock = asyncio.Lock()
        
        # Initialize blob/file storage based on type
        if self.storage_type == "local":
            self._init_local_storage()
        else:
            self._init_cloud_storage()
        
        # Initialize embeddings
        self._init_embeddings()
        
        # Initialize vector store based on type
        if self.storage_type == "local":
            self._init_local_vector_store()
        else:
            self._init_cloud_vector_store()
    
    def _init_local_storage(self):
        """Initialize local filesystem storage."""
        # Get absolute path to the project root (parent of email_agent package)
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_path = os.path.join(package_root, "data", "local_email_storage")
        self.blob_storage_path = os.environ.get("LOCAL_BLOB_PATH", default_path)
        os.makedirs(self.blob_storage_path, exist_ok=True)
        self.blob_service = None  # Use filesystem instead
        logger.info(f"✓ Local file storage initialized at: {self.blob_storage_path}")
    
    def _init_cloud_storage(self):
        """Initialize Azure Blob Storage."""
        credential = DefaultAzureCredential()
        storage_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL")
        self.blob_container = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "emails")
        self.blob_service = None
        self.blob_storage_path = None
        
        if storage_url:
            try:
                self.blob_service = BlobServiceClient(account_url=storage_url, credential=credential)
                # Ensure container exists
                try:
                    container_client = self.blob_service.get_container_client(self.blob_container)
                    container_client.create_container()
                    logger.info(f"✓ Created blob container: {self.blob_container}")
                except Exception:
                    logger.info(f"✓ Using existing blob container: {self.blob_container}")
                logger.info("✓ Azure Blob Storage initialized")
            except Exception as e:
                logger.warning(f"Blob storage disabled: {e}")
    
    def _init_embeddings(self):
        """Initialize embeddings.
        
        Tries Azure OpenAI embeddings first (if AZURE_OPENAI_ENDPOINT is configured).
        Falls back to text-only search if Azure credentials are not available.
        """
        # Check if Azure OpenAI endpoint is configured
        openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        
        if openai_endpoint:
            # Try Azure OpenAI embeddings
            try:
                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )

                # Make sure using openai v1 endpoint format
                openai_endpoint = openai_endpoint.rstrip("/")
                if not openai_endpoint.endswith("/openai/v1"):
                    openai_endpoint = f"{openai_endpoint}/openai/v1"
                
                self.embeddings = OpenAIEmbeddings(
                    model="text-embedding-ada-002",
                    base_url=openai_endpoint,
                    api_key=token_provider
                )
                logger.info("✓ Using Azure OpenAI embeddings")
                return
            except Exception as e:
                logger.warning(f"⚠️ Azure OpenAI embeddings failed: {e}")
                logger.info("   Search will use text matching instead.")
                self.embeddings = None
        else:
            # No Azure endpoint configured
            logger.info("ℹ️ AZURE_OPENAI_ENDPOINT not configured")
            logger.info("   Search will use text matching instead of semantic search.")
            logger.info("   To enable semantic search, set AZURE_OPENAI_ENDPOINT in .env")
            self.embeddings = None
    
    def _init_local_vector_store(self):
        """Initialize local PostgreSQL vector store."""
        # Skip if no embeddings available
        if self.embeddings is None:
            logger.warning("⚠️ Skipping vector store initialization (no embeddings available)")
            logger.info("   Search will use text matching instead of semantic search")
            self.vector_store = None
            self.connection_pool = None
            return
            
        try:
            # Get local connection parameters
            pg_host = os.environ.get('LOCAL_PGHOST', 'localhost')
            pg_database = os.environ.get('LOCAL_PGDATABASE', 'emaildb')
            pg_port = int(os.environ.get('LOCAL_PGPORT', '5432'))
            pg_user = os.environ.get('LOCAL_PGUSER', 'postgres')
            pg_password = os.environ.get('LOCAL_PGPASSWORD', 'P@ssw0rd!')
            
            logger.info(f"Connecting to local PostgreSQL at {pg_host}:{pg_port}/{pg_database}...")
            
            # Initialize connection pool with local config (no SSL required)
            self.connection_pool = AzurePGConnectionPool(
                azure_conn_info=ConnectionInfo(
                    host=pg_host,
                    dbname=pg_database,
                    port=pg_port,
                    sslmode=SSLMode.disable,  # Local doesn't need SSL
                    credentials=BasicAuth(
                        username=pg_user,
                        password=pg_password
                    )
                ),
                configure=configure_connection,
            )
            
            self._setup_vector_store(self.connection_pool)
            logger.info(f"✓ Local PostgreSQL vector store initialized ({pg_host}:{pg_port}/{pg_database})")
        except Exception as e:
            error_msg = str(e)
            # Simplify connection refused errors
            if "Connection refused" in error_msg or "connection failed" in error_msg:
                logger.warning(f"⚠️ Cannot connect to local PostgreSQL at {pg_host}:{pg_port}")
                logger.info("   Database is not running. Search will use text matching.")
                logger.info("   To enable vector search, run: docker compose up -d")
            else:
                logger.warning(f"Local vector store disabled: {error_msg}")
            self.vector_store = None
            self.connection_pool = None
    
    def _init_cloud_vector_store(self):
        """Initialize Azure PostgreSQL vector store."""
        try:
            # Get Azure connection parameters
            pg_host = os.environ.get('AZURE_PGHOST')
            pg_database = os.environ.get('AZURE_PGDATABASE')
            pg_port = int(os.environ.get('AZURE_PGPORT', '5432'))
            pg_user = os.environ.get('AZURE_PGUSER')
            pg_password = os.environ.get('AZURE_PGPASSWORD')
            
            # Initialize connection pool with Azure config (SSL required)
            connection_pool = AzurePGConnectionPool(
                azure_conn_info=ConnectionInfo(
                    host=pg_host,
                    dbname=pg_database,
                    port=pg_port,
                    sslmode=SSLMode.require,  # Azure requires SSL
                    credentials=BasicAuth(
                        username=pg_user,
                        password=pg_password
                    )
                ),
                configure=configure_connection,
            )
            
            self._setup_vector_store(connection_pool)
            logger.info(f"✓ Azure PostgreSQL vector store initialized ({pg_host}/{pg_database})")
        except Exception as e:
            logger.warning(f"Cloud vector store disabled: {e}")
            self.vector_store = None
    
    def _setup_vector_store(self, connection_pool):
        """Common vector store setup for both local and cloud."""
        # Ensure pgvector extension is enabled
        conn_setup = connection_pool.getconn()
        try:
            create_extensions(conn_setup, [Extension.VECTOR])
            logger.info("✓ pgvector extension enabled")
        except Exception as e:
            logger.warning(f"Extension setup note: {e}")
        finally:
            connection_pool.putconn(conn_setup)
        
        # Get a connection from the pool for vector store
        conn_vectorstore = connection_pool.getconn()
        
        # Initialize vector store
        self.vector_store = AzurePGVectorStore(
            connection=conn_vectorstore,
            embedding=self.embeddings,
            table_name="email_embeddings",
            collection_name="email_collection",
            embedding_dimension=1536,
            use_jsonb=True,
            pre_delete_collection=False
        )
        
        # Create tables if they don't exist
        try:
            self.vector_store.create_tables_if_not_exists()
            logger.info("✓ Vector store tables created/verified")
        except Exception as e:
            logger.warning(f"Table creation note: {e}")
    
    async def store_email(self, email_data: Dict[str, Any]) -> str:
        """Store email in blob/file storage and index in vector DB.
        
        Returns:
            email_id if stored, None if duplicate skipped
        """
        email_id = hashlib.sha256(
            f"{email_data.get('author','')}{email_data.get('subject','')}{email_data.get('body','')}".encode()
        ).hexdigest()[:16]
        
        # Check if email already exists
        exists = False
        if self.storage_type == "local":
            # Check local filesystem
            file_path = os.path.join(self.blob_storage_path, f"{email_id}.json")
            exists = os.path.exists(file_path)
        elif self.blob_service:
            # Check Azure blob storage
            try:
                blob_client = self.blob_service.get_blob_client(self.blob_container, f"{email_id}.json")
                exists = await asyncio.to_thread(blob_client.exists)
            except Exception as e:
                logger.warning(f"Could not check blob existence: {e}")
        
        if exists:
            logger.info(f"⏭️  Skipped duplicate email {email_id}")
            return None
        
        # Run storage and vector indexing concurrently
        tasks = []
        
        # Store in blob/file
        if self.storage_type == "local":
            async def store_local_file():
                try:
                    file_path = os.path.join(self.blob_storage_path, f"{email_id}.json")
                    email_json = json.dumps({**email_data, "email_id": email_id}, indent=2)
                    await asyncio.to_thread(
                        lambda: open(file_path, 'w').write(email_json)
                    )
                    logger.info(f"✓ Stored email {email_id} locally")
                except Exception as e:
                    logger.error(f"Local file store failed: {e}")
            tasks.append(store_local_file())
        elif self.blob_service:
            async def store_blob():
                try:
                    blob_client = self.blob_service.get_blob_client(self.blob_container, f"{email_id}.json")
                    await asyncio.to_thread(
                        blob_client.upload_blob,
                        json.dumps({**email_data, "email_id": email_id}, indent=2),
                        overwrite=False
                    )
                    logger.info(f"✓ Stored email {email_id} in blob")
                except Exception as e:
                    if "BlobAlreadyExists" not in str(e):
                        logger.error(f"Blob store failed: {e}")
            tasks.append(store_blob())
        
        # Index in vector store
        if self.vector_store:
            async def store_vector():
                try:
                    text = f"From: {email_data.get('author','')}\nSubject: {email_data.get('subject','')}\n\n{email_data.get('body','')}"
                    doc = Document(
                        page_content=text,
                        metadata={"email_id": email_id, "author": email_data.get("author",""), "subject": email_data.get("subject","")}
                    )
                    # Convert email_id to proper UUID format (deterministic)
                    doc_uuid = str(uuid.UUID(email_id.ljust(32, '0')))
                    
                    # Use lock to serialize vector store operations (not thread-safe)
                    async with self._vector_lock:
                        await asyncio.to_thread(
                            self.vector_store.add_documents,
                            [doc],
                            ids=[doc_uuid]
                        )
                    logger.info(f"✓ Indexed email {email_id}")
                except Exception as e:
                    logger.error(f"Vector index failed: {e}")
            tasks.append(store_vector())
        
        # Execute all operations concurrently
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        return email_id
    
    async def bulk_import_emails(
        self,
        email_list: List[Dict[str, Any]],
        batch_size: int = 50,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, int]:
        """Bulk import emails with batching for efficiency.
        
        Args:
            email_list: List of email dicts with keys: author, to, subject, body, received_at
            batch_size: Number of emails to process per batch
            progress_callback: Optional callback(current, total) for progress updates
            
        Returns:
            Dict with counts: {"stored": N, "skipped": N, "failed": N}
        """
        stats = {"stored": 0, "skipped": 0, "failed": 0}
        total = len(email_list)
        
        logger.info(f"Starting asynchronous bulk import of {total} emails...")
        
        for i in range(0, total, batch_size):
            batch = email_list[i:i + batch_size]
            
            # Step 1: Check for duplicates in vector store and prepare for storage
            file_storage_tasks = []
            new_emails = []  # Emails that need vector indexing
            file_only_emails = []  # Emails that need file storage only
            
            for email_data in batch:
                email_id = hashlib.sha256(
                    f"{email_data.get('author','')}{email_data.get('subject','')}{email_data.get('body','')}".encode()
                ).hexdigest()[:16]
                
                # Check if already exists in vector store (primary check)
                vector_exists = False
                if self.vector_store and self.connection_pool:
                    try:
                        # Check database for this email_id using getconn/putconn
                        conn = self.connection_pool.getconn()
                        try:
                            with conn.cursor() as cursor:
                                cursor.execute(
                                    "SELECT 1 FROM email_embeddings WHERE metadata->>'email_id' = %s LIMIT 1",
                                    (email_id,)
                                )
                                vector_exists = cursor.fetchone() is not None
                        finally:
                            self.connection_pool.putconn(conn)
                    except Exception as e:
                        logger.warning(f"Error checking vector store for {email_id}: {e}")
                        vector_exists = False
                
                # Check if file exists
                file_exists = False
                if self.storage_type == "local":
                    file_path = os.path.join(self.blob_storage_path, f"{email_id}.json")
                    file_exists = os.path.exists(file_path)
                elif self.blob_service:
                    try:
                        blob_client = self.blob_service.get_blob_client(self.blob_container, f"{email_id}.json")
                        file_exists = await asyncio.to_thread(blob_client.exists)
                    except Exception:
                        pass
                
                # Determine what needs to be done
                if vector_exists and file_exists:
                    # Complete duplicate - skip
                    logger.info(f"⏭️  Skipped duplicate email {email_id}")
                    stats["skipped"] += 1
                elif vector_exists and not file_exists:
                    # Has embeddings but missing file - store file only
                    file_only_emails.append((email_id, email_data))
                    file_storage_tasks.append(self._store_file_async(email_id, email_data))
                    logger.info(f"📄 Restoring missing file for email {email_id}")
                elif not vector_exists:
                    # Missing embeddings - needs both file and vector indexing
                    new_emails.append((email_id, email_data))
                    if not file_exists:
                        file_storage_tasks.append(self._store_file_async(email_id, email_data))
                        logger.info(f"📧 Storing new email {email_id} with embeddings")
                    else:
                        logger.info(f"🔄 Re-indexing email {email_id} (file exists, but no embeddings)")
            
            # Store all files/blobs concurrently
            if file_storage_tasks:
                await asyncio.gather(*file_storage_tasks, return_exceptions=True)
            
            # Count file-only storage as successful
            if file_only_emails:
                stats["stored"] += len(file_only_emails)
            
            # Step 2: Batch index in vector store (much faster than one-by-one)
            if new_emails and self.vector_store:
                try:
                    await self._batch_index_vectors(new_emails)
                    stats["stored"] += len(new_emails)
                except Exception as e:
                    logger.error(f"Batch vector indexing failed: {e}")
                    stats["failed"] += len(new_emails)
            elif new_emails and not self.vector_store:
                # No vector store available but files were stored
                stats["stored"] += len(new_emails)
            
            # Update progress
            current_count = min(i + batch_size, total)
            if progress_callback:
                progress_callback(current_count, total)
            
            # Log progress
            logger.info(f"Progress: {current_count}/{total} emails processed (stored: {stats['stored']}, skipped: {stats['skipped']}, failed: {stats['failed']})")
            
            # Small delay between batches
            await asyncio.sleep(0.1)
        
        logger.info(f"✓ Bulk import complete: {stats}")
        return stats
    
    async def _store_file_async(self, email_id: str, email_data: Dict[str, Any]):
        """Store email file/blob asynchronously."""
        try:
            if self.storage_type == "local":
                file_path = os.path.join(self.blob_storage_path, f"{email_id}.json")
                email_json = json.dumps({**email_data, "email_id": email_id}, indent=2)
                await asyncio.to_thread(
                    lambda: open(file_path, 'w').write(email_json)
                )
                logger.info(f"✓ Stored email {email_id} locally")
            elif self.blob_service:
                blob_client = self.blob_service.get_blob_client(self.blob_container, f"{email_id}.json")
                await asyncio.to_thread(
                    blob_client.upload_blob,
                    json.dumps({**email_data, "email_id": email_id}, indent=2),
                    overwrite=False
                )
                logger.info(f"✓ Stored email {email_id} in blob")
        except Exception as e:
            if "BlobAlreadyExists" not in str(e):
                logger.error(f"File storage failed for {email_id}: {e}")
    
    async def _batch_index_vectors(self, email_list: List[tuple]):
        """Batch index multiple emails in vector store.
        
        Args:
            email_list: List of (email_id, email_data) tuples
        """
        try:
            documents = []
            doc_ids = []
            
            for email_id, email_data in email_list:
                text = f"From: {email_data.get('author','')}\nSubject: {email_data.get('subject','')}\n\n{email_data.get('body','')}"
                doc = Document(
                    page_content=text,
                    metadata={"email_id": email_id, "author": email_data.get("author",""), "subject": email_data.get("subject","")}
                )
                documents.append(doc)
                doc_uuid = str(uuid.UUID(email_id.ljust(32, '0')))
                doc_ids.append(doc_uuid)
            
            # Batch add all documents at once (embeddings are generated in parallel by OpenAI)
            async with self._vector_lock:
                await asyncio.to_thread(
                    self.vector_store.add_documents,
                    documents,
                    ids=doc_ids
                )
            
            logger.info(f"✓ Batch indexed {len(documents)} emails")
        except Exception as e:
            logger.error(f"Batch vector indexing failed: {e}")
            raise
    
    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search emails by semantic similarity or text matching.
        
        Uses vector search if available, falls back to simple text search otherwise.
        """
        # Try vector search first
        if self.vector_store:
            try:
                # Perform similarity search with sync vector store
                results = self.vector_store.similarity_search_with_score(
                    query=query,
                    k=top_k
                )
                
                # Format results
                formatted_results = []
                for doc, score in results:
                    result = {
                        'score': float(score),
                        'content': doc.page_content,
                        'snippet': doc.page_content[:200] + '...' if len(doc.page_content) > 200 else doc.page_content,
                        **doc.metadata
                    }
                    formatted_results.append(result)
                
                logger.info(f"Found {len(formatted_results)} results for query: {query[:50]}...")
                return formatted_results
                
            except Exception as e:
                logger.error(f"Vector search failed: {e}")
                logger.info("Falling back to text search...")
        
        # Fallback: Simple text search on stored files
        return await self._text_search(query, top_k)
    
    async def _text_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Simple text-based search on stored email files.
        
        Used as fallback when vector search is unavailable.
        """
        results = []
        query_lower = query.lower()
        query_words = query_lower.split()
        
        if self.storage_type == "local" and self.blob_storage_path:
            try:
                # Search through local JSON files
                import glob
                email_files = glob.glob(os.path.join(self.blob_storage_path, "*.json"))
                
                for file_path in email_files:
                    try:
                        with open(file_path, 'r') as f:
                            email_data = json.load(f)
                        
                        # Build searchable text
                        text = f"{email_data.get('author', '')} {email_data.get('subject', '')} {email_data.get('body', '')}"
                        text_lower = text.lower()
                        
                        # Score based on word matches
                        score = sum(1 for word in query_words if word in text_lower)
                        
                        if score > 0:
                            results.append({
                                'score': score,
                                'content': text[:500],
                                'snippet': text[:200] + '...' if len(text) > 200 else text,
                                'author': email_data.get('author', ''),
                                'subject': email_data.get('subject', ''),
                                'email_id': email_data.get('email_id', '')
                            })
                    except Exception as e:
                        logger.warning(f"Error reading {file_path}: {e}")
                
                # Sort by score and return top_k
                results.sort(key=lambda x: x['score'], reverse=True)
                results = results[:top_k]
                
                logger.info(f"Text search found {len(results)} results for query: {query[:50]}...")
                
            except Exception as e:
                logger.error(f"Text search failed: {e}")
        
        return results
