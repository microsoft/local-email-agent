#!/usr/bin/env python3
"""
Debug script to check what emails are stored in the vector database.

Usage:
    python -m msft_email_agent.debug_stored_emails
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from langchain_azure_postgresql import (
    AzurePGConnectionPool,
    BasicAuth,
    ConnectionInfo,
    SSLMode,
)
from pgvector.psycopg import register_vector

load_dotenv()

def main():
    """Check what emails are stored in the vector database."""
    print("=" * 60)
    print("Vector Database Debug Tool")
    print("=" * 60)
    
    # Detect storage mode
    storage_mode = os.environ.get('STORAGE_MODE', 'cloud').lower()
    print(f"\n📦 Storage Mode: {storage_mode.upper()}")
    
    if storage_mode == 'local':
        # Use local PostgreSQL connection
        pg_host = os.environ.get('LOCAL_PGHOST', 'localhost')
        pg_database = os.environ.get('LOCAL_PGDATABASE', 'emaildb')
        pg_port = int(os.environ.get('LOCAL_PGPORT', '5432'))
        pg_user = os.environ.get('LOCAL_PGUSER', 'postgres')
        pg_password = os.environ.get('LOCAL_PGPASSWORD', 'P@ssw0rd!')
        
        conn_string = (
            f"host={pg_host} "
            f"port={pg_port} "
            f"dbname={pg_database} "
            f"user={pg_user} "
            f"password={pg_password}"
        )
        
        try:
            connection = psycopg.connect(conn_string)
        except Exception as e:
            print(f"❌ Failed to connect to local PostgreSQL: {e}")
            print("\nMake sure Docker PostgreSQL is running: docker-compose up -d")
            return 1
    else:
        # Use Azure PostgreSQL connection
        connection_info = ConnectionInfo(
            host=os.environ["AZURE_PGHOST"],
            dbname=os.environ["AZURE_PGDATABASE"],
            port=int(os.environ.get("AZURE_PGPORT", "5432")),
            sslmode=SSLMode.require,
            credentials=BasicAuth(
                username=os.environ["AZURE_PGUSER"],
                password=os.environ["AZURE_PGPASSWORD"]
            )
        )
        
        connection_pool = AzurePGConnectionPool(
            azure_conn_info=connection_info
        )
        
        try:
            connection = connection_pool.getconn()
        except Exception as e:
            print(f"❌ Failed to connect to Azure PostgreSQL: {e}")
            return 1
    
    try:
        with connection.cursor() as cur:
            # Check if the vector table exists
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name LIKE '%email%'
            """)
            
            tables = cur.fetchall()
            print(f"📊 Email-related tables found: {len(tables)}")
            for table in tables:
                print(f"   - {table[0]}")
            
            # Check for email_embeddings table (new format)
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'email_embeddings'
            """)
            
            email_embeddings_table = cur.fetchone()
            
            # Check the old langchain vector table
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'langchain_pg_embedding'
            """)
            
            langchain_table = cur.fetchone()
            
            if email_embeddings_table:
                print(f"\n✓ Found email_embeddings table")
                
                # Count total emails
                cur.execute("SELECT COUNT(*) FROM email_embeddings")
                total_count = cur.fetchone()[0]
                print(f"📧 Total stored emails: {total_count}")
                
                if total_count > 0:
                    # Show sample emails
                    cur.execute("""
                        SELECT id, 
                               LEFT(content, 100) as content_preview,
                               metadata->>'author' as author,
                               metadata->>'subject' as subject,
                               metadata->>'received_at' as received_at
                        FROM email_embeddings 
                        LIMIT 5
                    """)
                    
                    samples = cur.fetchall()
                    print(f"\n📬 Sample emails:")
                    
                    for i, (email_id, content, author, subject, received_at) in enumerate(samples, 1):
                        print(f"\n   {i}. From: {author}")
                        print(f"      Subject: {subject}")
                        print(f"      Date: {received_at}")
                        print(f"      Preview: {content}...")
                    
                    # Test search
                    print(f"\n🔍 Testing search for 'meeting':")
                    
                    cur.execute("""
                        SELECT COUNT(*) 
                        FROM email_embeddings 
                        WHERE LOWER(content) LIKE '%meeting%'
                           OR LOWER(metadata->>'subject') LIKE '%meeting%'
                    """)
                    
                    meeting_count = cur.fetchone()[0]
                    print(f"   Emails containing 'meeting': {meeting_count}")
                    
                    if meeting_count > 0:
                        cur.execute("""
                            SELECT metadata->>'author' as author,
                                   metadata->>'subject' as subject,
                                   LEFT(content, 150) as content_preview
                            FROM email_embeddings 
                            WHERE LOWER(content) LIKE '%meeting%'
                               OR LOWER(metadata->>'subject') LIKE '%meeting%'
                            LIMIT 3
                        """)
                        
                        meeting_emails = cur.fetchall()
                        print("   Sample meeting-related emails:")
                        for i, (author, subject, content) in enumerate(meeting_emails, 1):
                            print(f"\n      {i}. From: {author}")
                            print(f"         Subject: {subject}")
                            print(f"         Preview: {content}...")
                    
                    # Check embedding dimensions
                    cur.execute("""
                        SELECT vector_dims(embedding) as dims
                        FROM email_embeddings
                        LIMIT 1
                    """)
                    dims = cur.fetchone()[0]
                    print(f"\n✨ Embedding dimensions: {dims}")
                    print(f"   Vector search: {'✓ Enabled' if dims > 0 else '✗ Disabled'}")
                
            elif langchain_table:
                vector_table = langchain_table
                print(f"\n✓ Found vector table: {vector_table[0]}")
                
                # Count total documents
                cur.execute("SELECT COUNT(*) FROM langchain_pg_embedding")
                total_count = cur.fetchone()[0]
                print(f"📧 Total stored documents: {total_count}")
                
                # Check collections
                cur.execute("SELECT DISTINCT collection_id FROM langchain_pg_embedding")
                collections = cur.fetchall()
                print(f"📂 Collections found: {len(collections)}")
                
                for collection in collections:
                    collection_id = collection[0]
                    
                    # Count documents in this collection
                    cur.execute("SELECT COUNT(*) FROM langchain_pg_embedding WHERE collection_id = %s", (collection_id,))
                    count = cur.fetchone()[0]
                    
                    print(f"\n📁 Collection ID: {collection_id}")
                    print(f"   Documents: {count}")
                    
                    # Show sample documents
                    cur.execute("""
                        SELECT document, cmetadata 
                        FROM langchain_pg_embedding 
                        WHERE collection_id = %s 
                        LIMIT 5
                    """, (collection_id,))
                    
                    samples = cur.fetchall()
                    print(f"   Sample documents:")
                    
                    for i, (doc, metadata) in enumerate(samples, 1):
                        # Truncate long documents
                        doc_preview = doc[:100] + "..." if len(doc) > 100 else doc
                        print(f"      {i}. {doc_preview}")
                        if metadata:
                            print(f"         Metadata: {metadata}")
                    
                    # Test search on this collection
                    print(f"\n🔍 Testing search for 'meeting' in collection {collection_id}:")
                    
                    # Check if any documents contain 'meeting'
                    cur.execute("""
                        SELECT COUNT(*) 
                        FROM langchain_pg_embedding 
                        WHERE collection_id = %s 
                        AND LOWER(document) LIKE '%meeting%'
                    """, (collection_id,))
                    
                    meeting_count = cur.fetchone()[0]
                    print(f"   Documents containing 'meeting': {meeting_count}")
                    
                    if meeting_count > 0:
                        cur.execute("""
                            SELECT document, cmetadata 
                            FROM langchain_pg_embedding 
                            WHERE collection_id = %s 
                            AND LOWER(document) LIKE '%meeting%'
                            LIMIT 3
                        """, (collection_id,))
                        
                        meeting_docs = cur.fetchall()
                        print("   Sample meeting-related documents:")
                        for i, (doc, metadata) in enumerate(meeting_docs, 1):
                            doc_preview = doc[:150] + "..." if len(doc) > 150 else doc
                            print(f"      {i}. {doc_preview}")
            
            else:
                print("\n❌ No email tables found!")
                print("Emails have not been imported yet.")
                print("\nRun the import script first:")
                print("   python -m email_agent.import_emails --months 3 --storage local")
        
        if storage_mode == 'cloud':
            connection_pool.putconn(connection)
        else:
            connection.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    print("\n" + "=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
