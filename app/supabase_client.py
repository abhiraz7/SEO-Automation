"""
Supabase integration layer — learning datasets only.

Tables managed here (not in local SQLite):
  - acceptance_dataset
  - judge_dataset
  - visibility_dataset
  - memory_dataset

To activate: set SUPABASE_URL and SUPABASE_KEY in .env
Until then all functions are no-ops that return None silently.
"""

import os

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
    except ImportError:
        pass
    return _client


def push_acceptance(record: dict):
    """Store an acceptance/rejection event in Supabase."""
    client = _get_client()
    if client is None:
        return None
    return client.table("acceptance_dataset").insert(record).execute()


def push_judge(record: dict):
    """Store a judge score record in Supabase."""
    client = _get_client()
    if client is None:
        return None
    return client.table("judge_dataset").insert(record).execute()


def push_visibility(record: dict):
    """Store a visibility score record in Supabase."""
    client = _get_client()
    if client is None:
        return None
    return client.table("visibility_dataset").insert(record).execute()


def push_memory(record: dict):
    """Store a memory/RAG record in Supabase."""
    client = _get_client()
    if client is None:
        return None
    return client.table("memory_dataset").insert(record).execute()
