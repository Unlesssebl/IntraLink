import sys
import logging
import asyncio

# Add printer-worker to path
sys.path.append(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker")

from worker_services.credentials import get_domain_credentials
from dotenv import load_dotenv

load_dotenv(r"c:\Users\belikov.a\Desktop\Акты, документы\Work\!Projects\intraservice-tg-bot\printer-worker\.env")

from impacket.dcerpc.v5 import transport, svcctl
from impacket.dcerpc.v5.ndr import NULL

logging.basicConfig(level=logging.DEBUG)

def execute_smbexec(target, username, password, domain, command):
    # This simulates what smbexec does: creates a service via svcctl over SMB named pipes
    stringbinding = r'ncacn_np:%s[\pipe\svcctl]' % target
    logging.info('StringBinding %s'%stringbinding)
    rpctransport = transport.DCERPCTransportFactory(stringbinding)
    rpctransport.set_dport(445)
    
    if hasattr(rpctransport, 'set_credentials'):
        rpctransport.set_credentials(username, password, domain, '', '')
    
    try:
        dce = rpctransport.get_dce_rpc()
        dce.connect()
        dce.bind(svcctl.MSRPC_UUID_SVCCTL)
        
        # Open SCManager
        ans = svcctl.hROpenSCManagerW(dce)
        scManagerHandle = ans['lpScHandle']
        
        serviceName = 'WinRM_Bootstrap'
        
        # Create service
        # binPath must be less than MAX_PATH (260)?
        # Let's write the command to a file using echo, or run powershell directly.
        # Actually we can run powershell directly if it's within limits.
        binPath = r'%COMSPEC% /c start /b ' + command
        
        try:
            logging.info(f"Creating service {serviceName} with binPath: {binPath[:100]}...")
            resp = svcctl.hRCreateServiceW(dce, scManagerHandle, serviceName, serviceName,
                                            binPath=binPath)
            serviceHandle = resp['lpScHandle']
        except Exception as e:
            if "ERROR_SERVICE_EXISTS" in str(e):
                logging.warning("Service exists, opening it")
                resp = svcctl.hROpenServiceW(dce, scManagerHandle, serviceName)
                serviceHandle = resp['lpScHandle']
                # Change config
                svcctl.hRChangeServiceConfigW(dce, serviceHandle, binPath=binPath)
            else:
                raise e
                
        # Start the service
        logging.info("Starting service...")
        try:
            svcctl.hRStartServiceW(dce, serviceHandle)
        except Exception as e:
            # Service starting %COMSPEC% will quickly terminate and SCM throws ERROR_SERVICE_REQUEST_TIMEOUT or similar.
            # Usually we expect an error here!
            logging.info(f"Service start resulted in: {e}")
            pass
            
        # Delete the service
        logging.info("Deleting service...")
        svcctl.hRDeleteService(dce, serviceHandle)
        svcctl.hRCloseServiceHandle(dce, serviceHandle)
        svcctl.hRCloseServiceHandle(dce, scManagerHandle)
        
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
