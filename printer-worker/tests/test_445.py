import sys
import os
import asyncio
import logging

# Add printer-worker to path
sys.path.append(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker")

from worker_services.credentials import get_domain_credentials
from dotenv import load_dotenv

load_dotenv(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker\.env")

logging.basicConfig(level=logging.DEBUG)

async def main():
    domain, username, password = await get_domain_credentials()
    target = "kzm0088"
    
    print(f"Target: {target}")
    print(f"Domain: {domain}, User: {username}")
    
    # Let's see if port 445 is reachable
    import socket
    try:
        s = socket.create_connection((target, 445), timeout=5)
        print("Port 445 is reachable!")
        s.close()
    except Exception as e:
        print(f"Port 445 is NOT reachable: {e}")
        return

if __name__ == "__main__":
    asyncio.run(main())
