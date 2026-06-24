import sys
import logging
import asyncio

# Add printer-worker to path
sys.path.append(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker")

from worker_services.credentials import get_domain_credentials
from dotenv import load_dotenv

load_dotenv(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker\.env")

from impacket.dcerpc.v5 import transport, scmr
from impacket.smbconnection import SMBConnection

logging.basicConfig(level=logging.DEBUG)

def execute_smbexec(target, username, password, domain, command):
    try:
        logging.info(f"Connecting to SMB on {target}...")
        smb = SMBConnection(target, target, sess_port=445)
        smb.login(username, password, domain)
        logging.info("SMB Login successful.")
        
        rpctransport = transport.SMBTransport(target, 445, r'\svcctl', smb_connection=smb)
        
        dce = rpctransport.get_dce_rpc()
        dce.connect()
        dce.bind(scmr.MSRPC_UUID_SCMR)
        
        logging.info("SCMR bound successfully.")
        
        ans = scmr.hROpenSCManagerW(dce)
        scManagerHandle = ans['lpScHandle']
        
        serviceName = 'WinRM_Bootstrap'
        binPath = r'%COMSPEC% /Q /c ' + command
        
        try:
            logging.info(f"Creating service {serviceName}...")
            resp = scmr.hRCreateServiceW(dce, scManagerHandle, serviceName, serviceName,
                                            lpBinaryPathName=binPath)
            serviceHandle = resp['lpScHandle']
        except Exception as e:
            if "ERROR_DUPLICATE_SERVICE_NAME" in str(e) or "ERROR_SERVICE_EXISTS" in str(e):
                logging.warning("Service exists, opening it")
                resp = scmr.hROpenServiceW(dce, scManagerHandle, serviceName)
                serviceHandle = resp['lpScHandle']
                scmr.hRChangeServiceConfigW(dce, serviceHandle, lpBinaryPathName=binPath)
            else:
                raise e
                
        logging.info("Starting service...")
        try:
            scmr.hRStartServiceW(dce, serviceHandle)
        except Exception as e:
            logging.info(f"Service start resulted in (expected): {e}")
            
        logging.info("Deleting service...")
        scmr.hRDeleteService(dce, serviceHandle)
        scmr.hRCloseServiceHandle(dce, serviceHandle)
        scmr.hRCloseServiceHandle(dce, scManagerHandle)
        
        print("SMBExec command execution completed.")
    except Exception as e:
        print(f"SMBExec Failed: {e}")

async def main():
    domain, username, password = await get_domain_credentials()
    target = "kzm0088"
    
    command = "powershell.exe -ExecutionPolicy Bypass -NoProfile -Command \"Write-Output 'Hello from SMBExec' > C:\\Windows\\Temp\\smbexec_test.log\""
    
    execute_smbexec(target, username, password, domain, command)

if __name__ == "__main__":
    asyncio.run(main())
