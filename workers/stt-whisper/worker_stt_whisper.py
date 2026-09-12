import asyncio  
from listelog_pyworker_lib.abstract_worker import Worker
from stt_processor_whisper import *

async def _main():
    worker = Worker(SttProcessorWhisper)
    await worker.run()

if __name__ == "__main__":    
    asyncio.run(_main())  