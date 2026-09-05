import asyncio
from razorpayx.client import get_balance

async def main():
    print(await get_balance())

if __name__ == "__main__":
    asyncio.run(main())