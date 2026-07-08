#!/bin/bash
cd "$(dirname "$0")"
python3 -m pytest tests/test_ai_chat_platforms.py -q
