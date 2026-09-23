"""Knowledge Base batch synchronization task."""


async def sync_closed_tickets_task(batch_size: int = 100) -> dict:
    """Sync closed tickets into pgvector knowledge base."""
    return {"status": "pending", "processed": 0, "batch_size": batch_size}
