import sys
import logging
import asyncio

# Add printer-worker to path
sys.path.append(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker")

from worker_services.credentials import get_domain_credentials
from dotenv import load_dotenv

load_dotenv(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker\.env")

from impacket.dcerpc.v5 import transport, scmr
from impacket.dcerpc.v5.ndr import NULL

logging.basicConfig(level=logging.DEBUG)

def execute_smbexec(target, username, password, domain, command):
    stringbinding = r'ncacn_np:%s[\pipe\svcctl]' % target
    logging.info('StringBinding %s'%stringbinding)
    rpctransport = transport.DCERPCTransportFactory(stringbinding)
    rpctransport.set_dport(445)
    
    if hasattr(rpctransport, 'set_credentials'):
        rpctransport.set_credentials(username, password, domain, '', '')
    
    try:
        dce = rpctransport.get_dce_rpc()
        dce.connect()
        dce.bind(scmr.MSRPC_UUID_SCMR)
        
        ans = scmr.hROpenSCManagerW(dce)
        scManagerHandle = ans['lpScHandle']
        
        serviceName = 'WinRM_Bootstrap'
        binPath = r'%COMSPEC% /Q /c ' + command
        
        try:
            logging.info(f"Creating service {serviceName} with binPath: {binPath[:100]}...")
            resp = scmr.hRCreateServiceW(dce, scManagerHandle, serviceName, serviceName,
                                            lpBinaryPathName=binPath)
            serviceHandle = resp['lpScHandle']
        except Exception as e:
            if "ERROR_SERVICE_EXISTS" in str(e) or "ERROR_DUPLICATE_SERVICE_NAME" in str(e):
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
            logging.info(f"Service start resulted in: {e}")
            
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
