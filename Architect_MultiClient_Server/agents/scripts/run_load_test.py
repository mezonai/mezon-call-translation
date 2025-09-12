import asyncio
import argparse
from src.testing.load_test import LoadTest, LoadTestConfig

async def main():
    parser = argparse.ArgumentParser(description='Run WebSocket load test')
    parser.add_argument('--url', default='ws://localhost:8000/ws',
                      help='WebSocket server URL')
    parser.add_argument('--clients', type=int, default=10,
                      help='Number of concurrent clients')
    parser.add_argument('--messages', type=int, default=100,
                      help='Messages per client')
    parser.add_argument('--size', type=int, default=1024,
                      help='Message size in bytes')
    parser.add_argument('--interval', type=float, default=0.1,
                      help='Send interval in seconds')
    parser.add_argument('--rampup', type=float, default=5.0,
                      help='Ramp up time in seconds')
    parser.add_argument('--duration', type=float, default=60.0,
                      help='Test duration in seconds')
    
    args = parser.parse_args()
    
    config = LoadTestConfig(
        num_clients=args.clients,
        messages_per_client=args.messages,
        message_size_bytes=args.size,
        send_interval=args.interval,
        ramp_up_time=args.rampup,
        test_duration=args.duration,
        websocket_url=args.url
    )
    
    test = LoadTest(config)
    await test.run()

if __name__ == '__main__':
    asyncio.run(main())
