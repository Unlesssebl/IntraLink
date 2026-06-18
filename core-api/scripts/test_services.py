import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import intraservice
from app.services.worker import get_redis_client


async def main():
    redis = get_redis_client()
    encrypted_auth = await redis.get("worker:service_auth_b64")

    auth_b64 = None
    if encrypted_auth:
        if isinstance(encrypted_auth, bytes):
            encrypted_auth = encrypted_auth.decode()
        auth_b64 = encrypted_auth

    if not auth_b64:
        print("No auth_b64 in Redis")
        return

    await intraservice.init_session()
    try:
        services_default = await intraservice._make_request(
            endpoint="service", auth_b64=auth_b64
        )
        services_filter = await intraservice._make_request(
            endpoint="service?for=filtertasks", auth_b64=auth_b64
        )
        services_create = await intraservice._make_request(
            endpoint="service?for=createtask", auth_b64=auth_b64
        )

        print(f"Default count: {services_default.get('Paginator', {}).get('Count')}")
        print(f"Filtertasks count: {services_filter.get('Paginator', {}).get('Count')}")
        print(f"Createtask count: {services_create.get('Paginator', {}).get('Count')}")
        print("Type of services:", type(services_default))
        if isinstance(services_default, dict):
            print("Keys:", list(services_default.keys()))
            for k, v in services_default.items():
                if isinstance(v, list):
                    print(f"Key '{k}' is list of length {len(v)}")
                    if len(v) > 0:
                        print("Sample item:", v[0])
                else:
                    print(f"Key '{k}': {str(v)[:100]}")
        elif isinstance(services_default, list):
            print("Length of services list:", len(services_default))
            if len(services_default) > 0:
                print("Sample item:", services_default[0])
    finally:
        await intraservice.close_session()


if __name__ == "__main__":
    asyncio.run(main())
