"""Command-line chat. Run with `make chat`, or `make trace` to see the graph path."""

import argparse
import logging
import sys

import httpx
from openai import OpenAI

from assistant.config import ConfigError, load_settings
from assistant.extractor import LLMExtractor
from assistant.tools import HospitalApi
from assistant.workflow import Conversation, build_graph

MAX_MESSAGE_LENGTH = 1000
EXIT_COMMANDS = {"quit", "exit"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BedMed bed-assignment assistant")
    parser.add_argument("--trace", action="store_true", help="show the workflow path and events for each turn")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Trace output goes to stdout so it stays in order with the conversation.
    logging.basicConfig(level=logging.WARNING, format="    [%(name)s] %(message)s", stream=sys.stdout)
    if args.trace:
        logging.getLogger("bedmed").setLevel(logging.INFO)

    settings = load_settings()
    try:
        settings.require_llm_key()
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1

    llm_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30, max_retries=1)
    with httpx.Client(base_url=settings.api_url, timeout=10) as http_client:
        graph = build_graph(LLMExtractor(llm_client, settings.model), HospitalApi(http_client))
        chat(Conversation(graph), trace=args.trace)
    return 0


def chat(conversation: Conversation, trace: bool) -> None:
    print("BedMed assistant. Describe the patient and the bed they need. Type 'quit' to leave.\n")
    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message.lower() in EXIT_COMMANDS:
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            print(f"\nBedMed: That message is too long. Please keep it under {MAX_MESSAGE_LENGTH} characters.\n")
            continue

        turn = conversation.send(message)
        if trace:
            print(f"    [path] {' -> '.join(turn.path)}")
        print(f"\nBedMed: {turn.reply}\n")


if __name__ == "__main__":
    sys.exit(main())
