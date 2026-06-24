#!/bin/bash
cd ~/Desktop/distribution-manager
export DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-"your-api-key-here"}
exec python3 app.py
