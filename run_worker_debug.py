import asyncio
import logging
from compensating_agent.worker import main

logging.basicConfig(level=logging.DEBUG)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"CRASH: {e}")
