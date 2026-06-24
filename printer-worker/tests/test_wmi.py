import sys
import os
import asyncio
import logging

# Add printer-worker to path
sys.path.append(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker")

from executors.wmi_executor import WMIExecutor
from worker_services.credentials import get_domain_credentials
from dotenv import load_dotenv

load_dotenv(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker\.env")

logging.basicConfig(level=logging.DEBUG)

async def main():
    domain, username, password = await get_domain_credentials()
    target = "kzm0088"
    
    print(f"Target: {target}")
    print(f"Domain: {domain}, User: {username}")
    
    wmi_exec = WMIExecutor(
        target_ip=target,
        username=username,
        password=password,
        domain=domain
    )
    
    try:
        await wmi_exec.enable_winrm(timeout=60.0)
        print("Success! WinRM is enabled.")
        
        # Disable it after test
        print("Disabling WinRM...")
        await wmi_exec.disable_winrm()
        print("Disabled.")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
