import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
from handlers import start_help, auth, tickets


async def main():
    # 1. Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    
    # 3. Init Bot & Dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # 4. Include routers
    dp.include_router(start_help.router)
    dp.include_router(auth.router)
    dp.include_router(tickets.router)
    
    # 5. Start Redis Pub/Sub listener
    from services.redis_listener import start_redis_listener
    redis_listener_task = asyncio.create_task(start_redis_listener(bot))
    
    # 6. Start Bot Polling
    try:
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown
        redis_listener_task.cancel()
        try:
            await redis_listener_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
