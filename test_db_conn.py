import asyncio
import asyncpg
import os


async def test_conn():
    url = os.getenv("DB_URL", "postgresql://postgres:postgres@127.0.0.1:5432/tmatch")
    print(f"Testing connection to: {url}")
    try:
        # Convert to asyncpg friendly URL if needed (remove +asyncpg)
        clean_url = url.replace("+asyncpg", "")
        conn = await asyncpg.connect(clean_url)
        print("Successfully connected to the database!")
        val = await conn.fetchval("SELECT 1")
        print(f"Query test (SELECT 1): {val}")
        await conn.close()
    except Exception as e:
        print(f"Failed to connect: {e}")


if __name__ == "__main__":
    asyncio.run(test_conn())
