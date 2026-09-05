import asyncio
from listelog_pyworker_lib.abstract_worker import Worker
from src.spkemb_processor_moss import *

async def _main():
    worker = Worker(SpkembProcessorMoss)
    await worker.run()

if __name__ == "__main__":    
    asyncio.run(_main())  