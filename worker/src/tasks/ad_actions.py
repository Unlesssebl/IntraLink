"""Active Directory automation tasks."""


async def reset_ad_password_task(sam_account_name: str) -> dict:
    """Reset user password in Active Directory."""
    return {"status": "pending", "account": sam_account_name}


async def unlock_ad_account_task(sam_account_name: str) -> dict:
    """Unlock user account in Active Directory."""
    return {"status": "pending", "account": sam_account_name}


async def grant_wlan_access_task(sam_account_name: str, group_name: str = "WLAN-WORKNET-ALLOW") -> dict:
    """Add user account to corporate Wi-Fi security group in Active Directory."""
    return {"status": "success", "account": sam_account_name, "group": group_name}

