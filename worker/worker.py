import time
import logging

logging.basicConfig(level=logging.INFO)

def main():
    logging.info("Worker started (stub). Polling for jobs...")
    try:
        while True:
            # Stub: in Phase 1 worker does nothing but wait. Real jobs will be implemented later.
            time.sleep(5)
    except KeyboardInterrupt:
        logging.info("Worker stopped")


if __name__ == '__main__':
    main()
