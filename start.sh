#!/bin/bash
cd ~/Desktop/distribution-manager
export DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-"sk-f4074055a1ed4973a471a81743713ce1"}
exec python3 app.py
