import asyncio
from ai_search.research_agent import ResearchRunner

async def main():
    runner = ResearchRunner()
    answer = await runner.run("你要查询的问题写在这里")
    print(answer)

if __name__ == "__main__":
    asyncio.run(main())