"""Active Directory automation tasks."""


async def reset_ad_password_task(sam_account_name: str) -> dict:
    """Reset user password in Active Directory."""
    return {"status": "pending", "account": sam_account_name}


async def unlock_ad_account_task(sam_account_name: str) -> dict:
    """Unlock user account in Active Directory."""
    return {"status": "pending", "account": sam_account_name}
